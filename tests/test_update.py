from contextlib import nullcontext
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deploy.update_app import Updater
from upgrademe import Inventory, Server


class UpdateRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.inventory = Inventory(self.temp.name, enable_updates=True)

    def test_request_excludes_scans_and_survives_restart(self):
        self.assertTrue(self.inventory.request_update())
        self.assertFalse(self.inventory.request_update())
        self.assertFalse(self.inventory.start_scan(background=False))
        restarted = Inventory(self.temp.name, enable_updates=True)
        self.assertNotEqual(self.inventory.instance_id, restarted.instance_id)
        self.assertTrue(restarted.status()['update']['busy'])
        self.assertFalse(restarted.start_scan(background=False))
        restarted.update_request.unlink()
        self.assertTrue(restarted.start_scan(background=False))

    def test_scan_excludes_update_and_disabled_installations_reject_it(self):
        with self.inventory.scan_lock:
            self.assertFalse(self.inventory.request_update())
        self.assertFalse(self.inventory.update_request.exists())
        self.inventory.enable_updates = False
        with self.assertRaisesRegex(ValueError, 'nejsou nastavené'):
            self.inventory.request_update()

    def test_concurrent_update_requests_have_one_winner(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.inventory.request_update()))
                   for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(results.count(True), 1)

    def test_http_protection_and_conflict_responses(self):
        server = Server(('127.0.0.1', 0), self.inventory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}/api/'

        def post(headers=None):
            request = Request(base + 'update', data=b'{}',
                              headers=headers if headers is not None else {'X-UpgradeMe': '1'})
            with urlopen(request, timeout=3) as response:
                return json.load(response)

        def rejected(code, headers=None):
            with self.assertRaises(HTTPError) as caught:
                post(headers)
            self.assertEqual(caught.exception.code, code)
            caught.exception.close()

        try:
            rejected(403, {})
            rejected(403, {'X-UpgradeMe': '1', 'Origin': 'https://elsewhere.example'})
            with self.inventory.scan_lock:
                rejected(409)
            self.inventory.enable_updates = False
            rejected(503)
            self.inventory.enable_updates = True
            self.assertTrue(post()['ok'])
            rejected(409)
            with urlopen(base + 'status') as response:
                self.assertTrue(json.load(response)['update']['busy'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class UpdateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.worker = Updater(self.base / 'repo', self.base, self.base / 'backups', 'upgrademe')
        self.worker.request.touch()
        self.worker.publish = Mock()
        self.worker.health = Mock(return_value={'instance_id': 'previous'})
        self.worker.maintenance_lock = Mock(side_effect=nullcontext)

    def prepare_git(self):
        values = {('status', '--porcelain'): '', ('branch', '--show-current'): 'main',
                  ('rev-parse', 'HEAD'): 'old',
                  ('rev-parse', 'refs/remotes/origin/main^{commit}'): 'new'}
        self.worker.git = Mock(side_effect=lambda *args: values.get(args, ''))

    def test_clean_fast_forward_restarts_and_reports_success(self):
        self.prepare_git()
        self.worker.install = Mock(return_value='backup')
        self.worker.update()
        self.worker.git.assert_any_call('merge-base', '--is-ancestor', 'old', 'new')
        self.worker.install.assert_called_once_with('old', 'new', 'previous')
        self.worker.publish.assert_called_with('complete', revision='new', backup='backup')
        self.assertFalse(self.worker.request.exists())

    def test_dirty_checkout_is_not_stopped_or_changed(self):
        self.worker.git = Mock(return_value=' M upgrademe.py')
        self.worker.install = Mock()
        with self.assertLogs('upgrademe.update', level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'Local Git changes'):
                self.worker.update()
        self.worker.install.assert_not_called()
        self.assertEqual(self.worker.publish.call_args.args[0], 'failed')
        self.assertFalse(self.worker.request.exists())

    def test_fetch_failure_is_visible_and_allows_retry(self):
        self.prepare_git()
        original = self.worker.git.side_effect

        def git(*args):
            if args[0] == 'fetch':
                raise subprocess.CalledProcessError(1, 'git fetch', stderr='network unavailable')
            return original(*args)

        self.worker.git.side_effect = git
        with self.assertLogs('upgrademe.update', level='ERROR'):
            with self.assertRaises(subprocess.CalledProcessError):
                self.worker.update()
        self.worker.publish.assert_called_with('failed', error='network unavailable')
        self.assertFalse(self.worker.request.exists())

    def test_failed_restart_restores_previous_code_and_restarts(self):
        self.worker.run = Mock()
        self.worker.git = Mock()
        self.worker.backup = Mock(return_value='backup')
        self.worker.wait_ready = Mock(side_effect=[RuntimeError('bad version'), None])
        with self.assertLogs('upgrademe.update', level='ERROR'):
            with self.assertRaisesRegex(RuntimeError, 'bad version'):
                self.worker.install('old', 'new', 'previous')
        self.worker.git.assert_any_call('reset', '--keep', 'old')
        self.assertEqual(self.worker.run.call_args.args[0], ['systemctl', 'start', 'upgrademe.service'])

    def test_backup_failure_restarts_old_application(self):
        self.worker.run = Mock()
        self.worker.git = Mock()
        self.worker.backup = Mock(side_effect=OSError('disk full'))
        self.worker.wait_ready = Mock()
        with self.assertLogs('upgrademe.update', level='ERROR'):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.worker.install('old', 'new', 'previous')
        self.assertEqual(self.worker.run.call_args.args[0], ['systemctl', 'start', 'upgrademe.service'])

    def test_database_backup_preserves_settings_and_cache(self):
        inventory = Inventory(self.base)
        with inventory.db() as db:
            db.execute("INSERT INTO settings VALUES ('maintenance_event', 'keep-me')")
        backup = Path(self.worker.backup('old'))
        db = sqlite3.connect(backup / 'upgrademe.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT value FROM settings').fetchone()[0], 'keep-me')
        finally:
            db.close()

    def test_git_is_never_run_as_root(self):
        with patch('deploy.update_app.subprocess.run') as run:
            run.return_value.stdout = ''
            self.worker.git('fetch', 'origin', 'main')
        self.assertEqual(run.call_args.args[0][:4], ['runuser', '-u', 'upgrademe', '--'])

    def test_real_git_fast_forward_and_database_preservation(self):
        origin = self.base / 'origin'
        origin.mkdir()

        def git(directory, *args):
            return subprocess.run(['git', '-C', str(directory), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()

        git(origin, 'init', '-b', 'main')
        git(origin, 'config', 'user.name', 'Update test')
        git(origin, 'config', 'user.email', 'update@example.invalid')
        (origin / 'upgrademe.py').write_text("VERSION = 'old'\n")
        git(origin, 'add', 'upgrademe.py')
        git(origin, 'commit', '-m', 'old')
        git(self.base, 'clone', str(origin), str(self.worker.repo))
        (origin / 'upgrademe.py').write_text("VERSION = 'new'\n")
        git(origin, 'commit', '-am', 'new')
        target = git(origin, 'rev-parse', 'HEAD')
        inventory = Inventory(self.base)
        with inventory.db() as db:
            db.execute("INSERT INTO settings VALUES ('maintenance_event', 'preserved')")
        service_commands = []

        def run(command, as_user=False):
            if command[0] == 'systemctl':
                service_commands.append(command[1])
                return ''
            self.assertTrue(as_user)
            if command[0] == '/usr/bin/python3':
                command = [sys.executable, *command[1:]]
            return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()

        self.worker.run = run
        self.worker.wait_ready = Mock()
        self.worker.update()
        self.assertEqual(service_commands, ['stop', 'start'])
        self.assertEqual(git(self.worker.repo, 'rev-parse', 'HEAD'), target)
        self.assertEqual(git(self.worker.repo, 'branch', '--show-current'), 'main')
        with inventory.db() as db:
            self.assertEqual(db.execute('SELECT value FROM settings').fetchone()[0], 'preserved')
        self.assertEqual(self.worker.publish.call_args.args[0], 'complete')

    @unittest.skipUnless(os.name == 'posix', 'POSIX flock')
    def test_busy_maintenance_times_out_without_stopping_services(self):
        import fcntl
        lock = self.base / 'media-maintenance.lock'
        lock.touch()
        with lock.open('r+b') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch('deploy.update_app.time.monotonic', side_effect=[0, 301]):
                with self.assertRaisesRegex(TimeoutError, 'Media Maintenance'):
                    with Updater.maintenance_lock(self.worker):
                        self.fail('Acquired an already held maintenance lock')

    @unittest.skipUnless(os.name == 'posix', 'POSIX file permissions and flock')
    def test_atomic_status_and_shared_lock_do_not_follow_symlinks(self):
        victim = self.base / 'victim'
        victim.write_text('unchanged')
        target = self.base / 'update-status.json'
        target.symlink_to(victim)
        Updater.publish(self.worker, 'complete', revision='new')
        self.assertFalse(target.is_symlink())
        self.assertEqual(json.loads(target.read_text())['status'], 'complete')
        lock = self.base / 'media-maintenance.lock'
        lock.symlink_to(victim)
        with self.assertRaises(OSError):
            with Updater.maintenance_lock(self.worker):
                pass
        self.assertEqual(victim.read_text(), 'unchanged')
