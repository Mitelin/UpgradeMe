from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from upgrademe import Inventory, Server, canonical


class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.media = self.base / 'media'
        self.folder = self.media / 'Movie'
        self.folder.mkdir(parents=True)
        self.video = self.folder / 'video.mkv'
        self.video.write_bytes(b'unchanged media')
        self.inventory = Inventory(self.base / 'data', ffprobe=sys.executable,
                                   probe=lambda path, exe: (720, 576), settle_seconds=0)
        self.inventory.add_root(str(self.media), 'movie')
        self.path = canonical(str(self.folder))

    def test_priority_survives_scans_restart_and_root_readdition(self):
        self.assertEqual(self.inventory.listing()['priorities'], {})
        self.assertEqual(self.inventory.adjust_priority(self.path, 1), 1)
        self.inventory.start_scan(background=False)
        root_id = self.inventory.roots()[0]['id']
        self.inventory.remove_root(root_id)
        self.inventory.add_root(str(self.media), 'movie')
        restarted = Inventory(self.base / 'data')
        self.assertEqual(restarted.listing()['priorities'], {self.path: 1})
        self.assertEqual(restarted.status()['priority_revision'], 1)
        self.assertEqual(restarted.adjust_priority(self.path, -1), 0)
        self.assertEqual(restarted.listing()['priorities'], {})
        self.assertEqual(restarted.status()['priority_revision'], 2)
        self.assertEqual(self.video.read_bytes(), b'unchanged media')

    def test_concurrent_adjustments_do_not_lose_updates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.inventory.adjust_priority(self.path, 1), range(16)))
        self.assertEqual(sorted(results), list(range(1, 17)))
        self.assertEqual(self.inventory.listing()['priorities'][self.path], 16)
        self.assertEqual(self.inventory.status()['priority_revision'], 16)

    def test_invalid_requests_do_not_change_priority_or_revision(self):
        for delta in (True, False, '1', 1.0, None, 0, 2, -2, -1):
            with self.subTest(delta=delta):
                with self.assertRaises(ValueError):
                    self.inventory.adjust_priority(self.path, delta)
        for path in ('relative', '', None, str(self.base / 'outside')):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.inventory.adjust_priority(path, 1)
        self.assertEqual(self.inventory.listing()['priorities'], {})
        self.assertEqual(self.inventory.status()['priority_revision'], 0)

    def test_numeric_limit_prevents_loss_of_precision_in_browser(self):
        with self.inventory.db() as db:
            db.execute('INSERT INTO folder_priorities VALUES (?,?)', (self.path, 9007199254740991))
        with self.assertRaises(ValueError):
            self.inventory.adjust_priority(self.path, 1)
        self.assertEqual(self.inventory.adjust_priority(self.path, -1), 9007199254740990)

    def test_existing_database_is_upgraded_without_losing_cache_or_settings(self):
        self.inventory.start_scan(background=False)
        self.inventory.exception(str(self.video), 'file', 'keep')
        with self.inventory.db() as db:
            db.execute('DROP TABLE folder_priorities')
            db.execute("INSERT INTO settings VALUES ('maintenance_event', 'preserved')")
        restarted = Inventory(self.base / 'data')
        library = restarted.listing()
        self.assertEqual(library['priorities'], {})
        self.assertEqual(library['total'], 1)
        self.assertEqual(library['exceptions'][0]['note'], 'keep')
        with restarted.db() as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='maintenance_event'").fetchone()[0],
                             'preserved')
        self.assertEqual(restarted.adjust_priority(self.path, 1), 1)

    def test_http_protection_validation_persistence_and_update_exclusion(self):
        server = Server(('127.0.0.1', 0), self.inventory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}/api/'

        def post(data, headers=None):
            request = Request(base + 'priorities/adjust', data=json.dumps(data).encode(),
                              headers={'X-UpgradeMe': '1'} if headers is None else headers)
            with urlopen(request, timeout=3) as response:
                return json.load(response)

        def rejected(code, data, headers=None):
            with self.assertRaises(HTTPError) as caught:
                post(data, headers)
            self.assertEqual(caught.exception.code, code)
            caught.exception.close()

        data = {'path': self.path, 'delta': 1}
        try:
            rejected(403, data, {})
            rejected(403, data, {'X-UpgradeMe': '1', 'Origin': 'https://elsewhere.example'})
            rejected(400, {'path': self.path})
            rejected(400, {'path': self.path, 'delta': -1})
            self.assertEqual(post(data), {'ok': True, 'priority': 1})
            self.assertEqual(post(data), {'ok': True, 'priority': 2})
            with urlopen(base + 'library') as response:
                self.assertEqual(json.load(response)['priorities'], {self.path: 2})
            with urlopen(base + 'status') as response:
                self.assertEqual(json.load(response)['priority_revision'], 2)
            self.inventory.enable_updates = True
            self.inventory.update_request.touch()
            rejected(409, data)
            self.assertEqual(Inventory(self.base / 'data').listing()['priorities'], {self.path: 2})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
