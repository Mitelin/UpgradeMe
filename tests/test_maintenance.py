import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deploy.notify_maintenance import notify
from upgrademe import Inventory


class MaintenanceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.trigger = self.base / 'maintenance.json'
        self.lock = self.base / 'maintenance.lock'
        self.media = self.base / 'media'
        self.media.mkdir()
        (self.media / 'test.mkv').write_bytes(b'test')
        self.probed = threading.Event()

        def probe(path, executable):
            self.probed.set()
            return 640, 360

        self.inventory = Inventory(self.base / 'data', probe=probe, settle_seconds=0)
        self.inventory.add_root(str(self.media), 'auto')
        # Test the scheduler independently of the POSIX-only interprocess lock.
        self.inventory.maintenance_trigger = self.trigger

    def tearDown(self):
        self.inventory.stop.set()
        if self.inventory.worker:
            self.inventory.worker.join(timeout=5)
        self.temp.cleanup()

    def finish_worker(self):
        self.inventory.worker.join(timeout=5)
        self.assertFalse(self.inventory.worker.is_alive())


class MaintenanceScheduleTests(MaintenanceFixture):
    def test_user_stop_consumes_current_event_but_not_future_maintenance(self):
        notify(self.trigger)

        def cancel(path, executable):
            self.inventory.cancel_scan()
            return 640, 360

        self.inventory.probe = cancel
        self.assertTrue(self.inventory.schedule_tick())
        self.finish_worker()
        self.assertEqual(self.inventory.status()['last']['status'], 'cancelled')
        self.assertFalse(self.inventory.stop.is_set())
        self.assertFalse(self.inventory.schedule_tick())
        restarted = Inventory(self.base / 'data', probe=lambda path, exe: (640, 360),
                              settle_seconds=0)
        restarted.maintenance_trigger = self.trigger
        self.assertFalse(restarted.schedule_tick())
        notify(self.trigger)
        self.assertTrue(restarted.schedule_tick())
        restarted.worker.join(timeout=5)
        self.assertEqual(restarted.status()['last']['status'], 'complete')

    def test_no_event_means_no_daily_scan_and_status_has_no_clock_schedule(self):
        self.assertFalse(self.inventory.schedule_tick())
        self.assertIsNone(self.inventory.status()['next_scan'])
        self.assertEqual(self.inventory.status()['schedule_mode'], 'maintenance')
        self.assertFalse(self.probed.is_set())

    def test_success_and_failure_events_each_trigger_once(self):
        for result in ('success', 'exit-code', 'timeout'):
            with self.subTest(result=result), patch.dict(os.environ, {'SERVICE_RESULT': result}):
                event = notify(self.trigger)
                self.assertEqual(event['result'], result)
                self.assertTrue(self.inventory.schedule_tick())
                self.finish_worker()
                self.assertFalse(self.inventory.schedule_tick())
        self.assertEqual(self.inventory.status()['last']['id'], 3)

    def test_pending_event_survives_restart_and_is_not_repeated_after_completion(self):
        event = notify(self.trigger)
        self.assertTrue(self.inventory.schedule_tick())
        self.finish_worker()
        restarted = Inventory(self.base / 'data', probe=self.inventory.probe, settle_seconds=0)
        restarted.maintenance_trigger = self.trigger
        self.assertFalse(restarted.schedule_tick())
        next_event = notify(self.trigger)
        self.assertNotEqual(event['id'], next_event['id'])
        self.assertTrue(restarted.schedule_tick())
        restarted.worker.join(timeout=5)
        self.assertFalse(restarted.worker.is_alive())
        self.assertFalse(restarted.schedule_tick())

    def test_busy_scan_does_not_consume_completion_event(self):
        notify(self.trigger)
        with self.inventory.scan_lock:
            self.assertFalse(self.inventory.schedule_tick())
        self.assertTrue(self.inventory.schedule_tick())
        self.finish_worker()
        self.assertFalse(self.inventory.schedule_tick())

    def test_no_roots_keeps_event_pending(self):
        notify(self.trigger)
        self.inventory.remove_root(self.inventory.roots()[0]['id'])
        self.assertFalse(self.inventory.schedule_tick())
        self.inventory.add_root(str(self.media), 'auto')
        self.assertTrue(self.inventory.schedule_tick())
        self.finish_worker()

    def test_invalid_events_fail_explicitly(self):
        for event in ({}, [], {'id': ''}, {'id': 1}):
            with self.subTest(event=event):
                self.trigger.write_text(json.dumps(event), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.inventory.schedule_tick()
        self.trigger.write_text('{', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            self.inventory.schedule_tick()

    def test_notifier_replaces_event_atomically_and_leaves_no_temporary_files(self):
        first = notify(self.trigger)
        second = notify(self.trigger)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(json.loads(self.trigger.read_text(encoding='utf-8')), second)
        self.assertEqual(list(self.base.glob('.maintenance.json.*')), [])
        with self.assertRaises(FileNotFoundError):
            notify(self.base / 'missing' / 'event.json')

    def test_maintenance_configuration_requires_shared_lock(self):
        with self.assertRaisesRegex(ValueError, 'zámek'):
            Inventory(self.base / 'invalid', maintenance_trigger=self.trigger)
        if os.name != 'posix':
            with self.assertRaisesRegex(ValueError, 'POSIX'):
                Inventory(self.base / 'invalid', shared_scan_lock=self.lock)


@unittest.skipUnless(os.name == 'posix', 'POSIX flock integration')
class SharedLockTests(MaintenanceFixture):
    def setUp(self):
        super().setUp()
        self.inventory.shared_scan_lock = self.lock

    def wait_until_waiting(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            active = self.inventory.status()['active']
            if active and active['phase'].startswith('Čekání'):
                return
            time.sleep(0.02)
        self.fail('Scanner did not report waiting for maintenance')

    def test_scan_waits_without_probing_then_runs_when_maintenance_releases_lock(self):
        import fcntl
        with self.lock.open('a+b') as maintenance:
            fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
            notify(self.trigger)
            self.assertTrue(self.inventory.schedule_tick())
            self.wait_until_waiting()
            self.assertFalse(self.probed.is_set())
            self.assertFalse(self.inventory.schedule_tick())
        self.finish_worker()
        self.assertTrue(self.probed.is_set())
        self.assertEqual(self.inventory.status()['last']['status'], 'complete')
        self.assertFalse(self.inventory.schedule_tick())

    def test_user_stop_cancels_wait_without_stopping_maintenance_or_scheduler(self):
        import fcntl
        with self.lock.open('a+b') as maintenance:
            fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
            notify(self.trigger)
            self.assertTrue(self.inventory.schedule_tick())
            self.wait_until_waiting()
            self.assertTrue(self.inventory.cancel_scan())
            self.finish_worker()
            self.assertFalse(self.probed.is_set())
            self.assertFalse(self.inventory.stop.is_set())
            self.assertEqual(self.inventory.status()['last']['status'], 'cancelled')
            self.assertFalse(self.inventory.schedule_tick())
            with self.lock.open('a+b') as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_manual_scan_holds_lock_and_releases_it_after_probing(self):
        import fcntl
        release = threading.Event()

        def probe(path, executable):
            self.probed.set()
            if not release.wait(timeout=5):
                raise TimeoutError('Test did not release probe')
            return 640, 360

        self.inventory.probe = probe
        try:
            self.assertTrue(self.inventory.start_scan())
            self.assertTrue(self.probed.wait(timeout=5))
            with self.lock.open('a+b') as maintenance:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            release.set()
            self.finish_worker()
        with self.lock.open('a+b') as maintenance:
            fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_interrupted_wait_remains_pending_for_restart(self):
        import fcntl
        notify(self.trigger)
        with self.lock.open('a+b') as maintenance:
            fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(self.inventory.schedule_tick())
            self.wait_until_waiting()
            self.inventory.stop.set()
            self.finish_worker()
        self.assertEqual(self.inventory.status()['last']['status'], 'interrupted')
        self.assertFalse(self.probed.is_set())
        self.inventory.stop.clear()
        self.assertTrue(self.inventory.schedule_tick())
        self.finish_worker()
        self.assertTrue(self.probed.is_set())

    def test_lock_open_error_is_recorded_and_does_not_stick_in_running_state(self):
        self.inventory.shared_scan_lock = self.base / 'missing' / 'lock'
        with self.assertLogs('upgrademe', level='ERROR'):
            self.assertTrue(self.inventory.start_scan(background=False))
        self.assertEqual(self.inventory.status()['last']['status'], 'failed')
        self.assertIsNone(self.inventory.status()['active'])
        self.assertFalse(self.inventory.scan_lock.locked())
        self.assertFalse(self.probed.is_set())
