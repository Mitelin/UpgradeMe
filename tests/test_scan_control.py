import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from upgrademe import Inventory, ScanCancelled, Server, _probe_output


class ScanControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.media = self.base / 'media'
        self.media.mkdir()
        self.inventory = Inventory(self.base / 'data', probe=lambda path, exe: (640, 360),
                                   settle_seconds=0)
        self.inventory.add_root(str(self.media), 'movie')

    def tearDown(self):
        self.inventory.stop.set()
        if self.inventory.worker:
            self.inventory.worker.join(timeout=5)
        self.temp.cleanup()

    def test_idle_stop_is_rejected_and_does_not_stop_application(self):
        self.assertFalse(self.inventory.cancel_scan())
        self.assertFalse(self.inventory.stop.is_set())
        self.assertFalse(self.inventory.scan_cancel.is_set())

    def test_stop_retains_completed_results_without_pruning_unvisited_cache(self):
        gone = self.media / 'gone.mkv'
        gone.write_bytes(b'old video')
        self.inventory.start_scan(background=False)
        gone.unlink()
        for name in ('a.mkv', 'b.mkv'):
            (self.media / name).write_bytes(b'new video')

        def probe(path, executable):
            if Path(path).name == 'b.mkv':
                self.assertTrue(self.inventory.cancel_scan())
            return 720, 576

        self.inventory.probe = probe
        with patch('upgrademe.os.walk', return_value=[(str(self.media), [], ['a.mkv', 'b.mkv'])]):
            self.inventory.start_scan(background=False)
        status = self.inventory.status()
        self.assertEqual(status['last']['status'], 'cancelled')
        self.assertEqual(status['last']['result']['errors'], 0)
        self.assertEqual(status['last']['result']['removed'], 0)
        self.assertEqual({f['name'] for f in self.inventory.listing()['files']}, {'gone.mkv', 'a.mkv'})
        self.assertFalse(self.inventory.scan_lock.locked())
        self.assertFalse(self.inventory.stop.is_set())
        self.inventory.probe = lambda path, exe: (720, 576)
        self.assertTrue(self.inventory.start_scan(background=False))
        self.assertEqual(self.inventory.status()['last']['status'], 'complete')
        self.assertFalse(self.inventory.scan_cancel.is_set())

    def test_builtin_probe_receives_cancellation_without_recording_a_video_error(self):
        from upgrademe import probe_video
        (self.media / 'video.mkv').write_bytes(b'video')
        self.inventory.probe = probe_video
        self.inventory.ffprobe = sys.executable

        def output(command, check_cancelled):
            self.inventory.cancel_scan()
            check_cancelled()

        with patch('upgrademe._probe_output', side_effect=output):
            self.inventory.start_scan(background=False)
        status = self.inventory.status()
        self.assertEqual(status['last']['status'], 'cancelled')
        self.assertEqual(status['last']['result']['errors'], 0)
        self.assertEqual(self.inventory.listing()['total'], 0)

    def test_http_start_stop_status_and_restart(self):
        (self.media / 'video.mkv').write_bytes(b'video')
        entered = threading.Event()
        release = threading.Event()

        def probe(path, executable):
            entered.set()
            while not release.wait(0.02):
                self.inventory._check_cancelled()
            return 640, 360

        self.inventory.probe = probe
        server = Server(('127.0.0.1', 0), self.inventory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}/api/'

        def post(route, headers=None):
            request = Request(base + route, data=b'{}',
                              headers=headers or {'X-UpgradeMe': '1', 'Content-Type': 'application/json'})
            with urlopen(request, timeout=5) as response:
                return json.load(response)

        try:
            self.assertTrue(post('scan')['ok'])
            self.assertTrue(entered.wait(timeout=3))
            with self.assertRaises(HTTPError) as denied:
                post('scan/stop', {'X-UpgradeMe': '1', 'Origin': 'https://elsewhere.example'})
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()
            self.assertFalse(self.inventory.scan_cancel.is_set())
            self.assertTrue(post('scan/stop')['ok'])
            self.inventory.worker.join(timeout=3)
            self.assertFalse(self.inventory.worker.is_alive())
            with urlopen(base + 'status', timeout=5) as response:
                self.assertEqual(json.load(response)['last']['status'], 'cancelled')
            with self.assertRaises(HTTPError) as idle:
                post('scan/stop')
            self.assertEqual(idle.exception.code, 409)
            idle.exception.close()
            release.set()
            self.assertTrue(post('scan')['ok'])
            self.inventory.worker.join(timeout=3)
            self.assertEqual(self.inventory.status()['last']['status'], 'complete')
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class ProbeProcessTests(unittest.TestCase):
    def test_stop_terminates_and_reaps_real_child_within_three_seconds(self):
        cancelled = threading.Event()
        processes = []
        timers = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            processes.append(child)
            timer = threading.Timer(0.2, cancelled.set)
            timers.append(timer)
            timer.start()
            return child

        def check_cancelled():
            if cancelled.is_set():
                raise ScanCancelled('test stop')

        started = time.monotonic()
        try:
            with patch('upgrademe.subprocess.Popen', side_effect=launch):
                with self.assertRaises(ScanCancelled):
                    _probe_output([sys.executable, '-c', 'import time; time.sleep(30)'], check_cancelled)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
        finally:
            for timer in timers:
                timer.cancel()
                timer.join(timeout=1)
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

    def test_output_and_error_reporting_still_work(self):
        self.assertEqual(_probe_output([sys.executable, '-c', "print('ready')"]).strip(), 'ready')
        with self.assertRaisesRegex(ValueError, 'probe error'):
            _probe_output([sys.executable, '-c', "import sys; sys.stderr.write('probe error'); sys.exit(1)"])

    def test_already_cancelled_does_not_launch_process(self):
        def cancelled():
            raise ScanCancelled('test stop')
        with patch('upgrademe.subprocess.Popen') as launch:
            with self.assertRaises(ScanCancelled):
                _probe_output(['ffprobe'], cancelled)
        launch.assert_not_called()
