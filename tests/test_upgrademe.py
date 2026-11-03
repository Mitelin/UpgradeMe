import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from upgrademe import Inventory, Server, canonical, group_for, probe_video


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.media = self.base / 'media'
        self.media.mkdir()
        self.calls = []

        def probe(path, executable):
            self.calls.append(path)
            return 720, 576

        self.inventory = Inventory(self.base / 'data', probe=probe, settle_seconds=0)
        self.inventory.add_root(str(self.media), 'auto')

    def tearDown(self):
        self.inventory.stop.set()
        if self.inventory.worker:
            self.inventory.worker.join(timeout=5)
        self.temp.cleanup()

    def video(self, name='film.mkv', contents=b'video'):
        p = self.media / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(contents)
        return p

    def scan(self, retry=False):
        self.assertTrue(self.inventory.start_scan(retry_errors=retry, background=False))
        self.assertNotEqual(self.inventory.status()['last']['status'], 'failed')

    def test_unchanged_is_not_probed_and_source_is_unchanged(self):
        p = self.video()
        before = (p.read_bytes(), p.stat().st_mtime_ns)
        self.scan(); self.scan()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.inventory.status()['last']['result']['cached'], 1)
        self.assertEqual(before, (p.read_bytes(), p.stat().st_mtime_ns))

    def test_size_change_is_probed_even_if_mtime_is_preserved(self):
        p = self.video(); self.scan(); stat = p.stat()
        p.write_bytes(b'new longer content'); os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.scan(); self.assertEqual(len(self.calls), 2)

    def test_mtime_change_is_probed_even_if_size_is_same(self):
        p = self.video(); stat = p.stat()
        # Keep both mtimes in the past, outside the recently-modified guard.
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns - 10_000_000_000))
        self.scan(); stat = p.stat()
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
        self.scan(); self.assertEqual(len(self.calls), 2)

    def test_probe_receives_original_unicode_filename(self):
        self.video('Příběh Ōtsutsuki.mkv'); self.scan()
        self.assertEqual(Path(self.calls[0]).name, 'Příběh Ōtsutsuki.mkv')

    def test_results_are_committed_during_scan_before_root_finishes(self):
        self.video('one.mkv'); self.video('two.mkv')
        observed = []
        def inspecting(path, executable):
            observed.append(self.inventory.listing()['total'])
            return 640, 480
        self.inventory.probe = inspecting
        with patch('upgrademe.time.monotonic', side_effect=range(0, 300, 3)):
            self.scan()
        self.assertEqual(observed, [0, 1])

    def test_deep_new_file_discovered_without_reprobing_old_file(self):
        self.video(); self.scan(); self.video('A/Season 01/A.S01E01.mkv'); self.scan()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.inventory.listing()['total'], 2)

    def test_missing_file_removed_after_successful_nonempty_scan(self):
        gone = self.video('gone.mkv'); self.video('stays.mkv'); self.scan()
        gone.unlink(); self.scan()
        self.assertEqual(self.inventory.listing()['total'], 1)
        self.assertEqual(self.inventory.status()['last']['result']['removed'], 1)

    def test_unavailable_root_preserves_cache(self):
        self.video(); self.scan(); self.media.rename(self.base / 'disconnected')
        self.scan(); self.assertEqual(self.inventory.listing()['total'], 1)
        self.assertEqual(self.inventory.status()['last']['status'], 'warning')

    def test_suddenly_empty_root_preserves_cache(self):
        p = self.video(); self.scan(); p.unlink(); self.scan()
        self.assertEqual(self.inventory.listing()['total'], 1)
        self.assertIn('prázdná', ' '.join(self.inventory.status()['last']['result']['warnings']))

    def test_partial_walk_error_prevents_pruning(self):
        self.video(); self.scan()

        def failed_walk(path, followlinks, onerror):
            onerror(PermissionError('permission denied'))
            return iter([(str(self.media), [], [])])

        with patch('upgrademe.os.walk', failed_walk):
            self.scan()
        self.assertEqual(self.inventory.listing()['total'], 1)

    def test_folder_exception_applies_to_future_files_and_not_sibling_prefix(self):
        one = self.video('Show/Season 1/a.mkv'); two = self.video('Show2/a.mkv'); self.scan()
        self.inventory.exception(str(one.parent.parent), 'folder', 'Originál již neexistuje')
        self.video('Show/Season 2/b.mkv'); self.scan()
        files = self.inventory.listing()['files']
        self.assertEqual(sum(bool(f['exception']) for f in files), 2)
        self.assertIsNone(next(f for f in files if f['path'] == canonical(str(two)))['exception'])

    def test_file_exception_overrides_folder_and_deleting_reveals_parent(self):
        p = self.video(); self.scan()
        self.inventory.exception(str(self.media), 'folder', 'Složka')
        self.inventory.exception(str(p), 'file', 'Konkrétní díl')
        self.assertEqual(self.inventory.listing()['files'][0]['exception']['note'], 'Konkrétní díl')
        self.inventory.exception(str(p), 'file', delete=True)
        self.assertEqual(self.inventory.listing()['files'][0]['exception']['note'], 'Složka')
        self.inventory.exception(str(self.media), 'folder', delete=True)
        self.assertIsNone(self.inventory.listing()['files'][0]['exception'])

    def test_cache_and_exceptions_survive_restart(self):
        p = self.video(); self.scan(); self.inventory.exception(str(p), 'file', 'Poznámka')
        restarted = Inventory(self.base / 'data', probe=self.inventory.probe, settle_seconds=0)
        restarted.start_scan(background=False)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(restarted.listing()['files'][0]['exception']['note'], 'Poznámka')

    def test_error_is_not_sd_and_only_retried_explicitly(self):
        self.video()

        def broken(path, exe):
            self.calls.append(path)
            raise ValueError('Poškozené video')

        self.inventory.probe = broken
        self.scan(); self.scan()
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.inventory.listing()['files'][0]['height'])
        self.scan(retry=True); self.assertEqual(len(self.calls), 2)

    def test_recently_modified_file_is_deferred(self):
        self.video(); self.inventory.settle_seconds = 120; self.scan()
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(self.inventory.status()['last']['result']['deferred'], 1)
        self.inventory.settle_seconds = 0; self.scan(); self.assertEqual(len(self.calls), 1)

    def test_change_during_probe_is_not_accepted(self):
        p = self.video()

        def changing(path, exe):
            p.write_bytes(b'Changed while ffprobe reads')
            return 720, 576

        self.inventory.probe = changing; self.scan()
        item = self.inventory.listing()['files'][0]
        self.assertIsNone(item['height']); self.assertEqual(item['size'], -1)

    def test_roots_must_not_overlap_and_removal_does_not_touch_media(self):
        p = self.video('child/film.mkv')
        with self.assertRaises(ValueError):
            self.inventory.add_root(str(p.parent), 'movie')
        with self.assertRaises(ValueError):
            self.inventory.add_root(str(self.base), 'movie')
        self.scan(); self.inventory.remove_root(self.inventory.roots()[0]['id'])
        self.assertTrue(p.exists()); self.assertEqual(self.inventory.listing()['total'], 0)

    def test_parallel_scan_and_root_edit_rejected(self):
        self.inventory.scan_lock.acquire()
        try:
            self.assertFalse(self.inventory.start_scan())
            with self.assertRaises(ValueError):
                self.inventory.remove_root(1)
        finally:
            self.inventory.scan_lock.release()

    def test_nas_recycle_and_non_video_are_skipped(self):
        self.video('#recycle/gone.mkv'); self.video('@eaDir/file.mkv')
        self.video('description.nfo'); self.video('real.MKV'); self.scan()
        self.assertEqual(len(self.calls), 1)

    def test_scheduler_five_am_and_persistent_daily_catchup(self):
        with patch.object(self.inventory, 'start_scan', return_value=True) as start:
            self.assertFalse(self.inventory.schedule_tick(datetime(2026, 10, 23, 4, 59)))
            self.assertTrue(self.inventory.schedule_tick(datetime(2026, 10, 23, 5, 0)))
            self.assertFalse(self.inventory.schedule_tick(datetime(2026, 10, 23, 5, 1)))
            self.assertEqual(start.call_count, 1)
        restarted = Inventory(self.base / 'data', probe=self.inventory.probe)
        with patch.object(restarted, 'start_scan', return_value=True) as start:
            self.assertFalse(restarted.schedule_tick(datetime(2026, 10, 23, 18)))
            self.assertTrue(restarted.schedule_tick(datetime(2026, 10, 24, 10)))
            self.assertEqual(start.call_count, 1)

    def test_schedule_retries_when_manual_scan_is_busy(self):
        with patch.object(self.inventory, 'start_scan', return_value=False):
            self.assertFalse(self.inventory.schedule_tick(datetime(2026, 10, 23, 5)))
        with patch.object(self.inventory, 'start_scan', return_value=True):
            self.assertTrue(self.inventory.schedule_tick(datetime(2026, 10, 23, 5, 1)))

    def test_series_grouping(self):
        root = canonical(str(self.media))
        show = str(Path(root) / 'Show')
        self.assertEqual(group_for(str(Path(show) / 'Season 01' / 'Show.S01E02.mkv'), root, 'auto'), show)
        self.assertEqual(group_for(str(Path(show) / 'Episode 02.mkv'), root, 'series'), show)
        self.assertEqual(group_for(str(Path(show) / 'Season 01' / 'episode.mkv'), show, 'series'), show)
        self.assertEqual(group_for(str(Path(show) / 'S01E02.mkv'), show, 'auto'), show)
        self.assertIsNone(group_for(str(Path(root) / 'Film' / 'Film.mkv'), root, 'auto'))

    def test_series_grouping_ignores_category_ancestors(self):
        root = canonical(str(self.media))
        show = Path(root) / 'seriali' / 'czech' / 'Show'
        for kind in ('auto', 'series'):
            for relative in ('Show.S01E02.mkv', 'Season 01/Show.S01E02.mkv',
                             'S02/Show.S02E01.mkv'):
                with self.subTest(kind=kind, relative=relative):
                    self.assertEqual(group_for(str(show / relative), root, kind), str(show))
        self.assertEqual(group_for(str(show / 'Episode 02.mkv'), root, 'series'), str(show))
        self.assertIsNone(group_for(str(show / 'Show.S01E02.mkv'), root, 'movie'))

    def test_series_grouping_finds_season_above_episode_folder(self):
        root = canonical(str(self.media))
        show = Path(root) / 'serialy' / 'english' / 'parks and recreation'
        for season in ('season 04', 'Season 04 - description', 'S04', 'Série 04'):
            for filename in ('video.avi', 'parks.and.recreation.s04x01.mkv'):
                for kind in ('auto', 'series'):
                    with self.subTest(season=season, filename=filename, kind=kind):
                        path = show / season / 'parks and recreation s04x01 the road trip' / filename
                        self.assertEqual(group_for(str(path), root, kind), str(show))
                        self.assertIsNone(group_for(str(path), root, 'movie'))

    def test_series_season_search_stays_within_root_and_uses_nearest(self):
        root = self.media / 'Season 01' / 'Library'
        path = root / 'Film' / 'video.mkv'
        self.assertIsNone(group_for(str(path), str(root), 'auto'))
        show = root / 'Season 02' / 'Show'
        self.assertEqual(group_for(str(show / 'Season 03' / 'Episode' / 'video.mkv'),
                                   str(root), 'auto'), str(show))

    def test_listing_merges_nested_episode_folders_without_rescan(self):
        first = self.video('serialy/english/Show/Season 04/Show s04x01/video.avi')
        self.video('serialy/english/Show/Season 05/Show s05x01/video.avi')
        self.scan()
        restarted = Inventory(self.base / 'data', probe=self.inventory.probe, settle_seconds=0)
        listing = restarted.listing()
        self.assertEqual({item['group'] for item in listing['files']},
                         {canonical(str(first.parents[2]))})
        self.assertEqual(listing['total'], 2)
        self.assertEqual(len(self.calls), 2)

    def test_named_seasons_group_under_show(self):
        root = canonical(str(self.media))
        show = Path(root) / 'serialy' / 'czech' / 'survivor'
        for season in ('season 03 - africa', 'Season 04 - Marquesas',
                       'S03 - Africa', 'Sezóna 03 - Afrika', 'Série 03 - Afrika'):
            for kind in ('auto', 'series'):
                for library in (root, str(show)):
                    with self.subTest(season=season, kind=kind, library=library):
                        path = str(show / season / 'episode.mkv')
                        self.assertEqual(group_for(path, library, kind), str(show))
        for folder in ('Seasoned 03', 'Season 03abc', 'S03E01 - Africa'):
            with self.subTest(folder=folder):
                self.assertIsNone(group_for(str(show / folder / 'film.mkv'), root, 'auto'))
        self.assertIsNone(group_for(str(show / 'season 03 - africa' / 'film.mkv'), root, 'movie'))

    def test_listing_merges_named_seasons_without_rescan(self):
        first = self.video('serialy/czech/survivor/season 03 - africa/episode.mkv')
        self.video('serialy/czech/survivor/season 04 - marquesas/episode.mkv')
        self.scan()
        restarted = Inventory(self.base / 'data', probe=self.inventory.probe, settle_seconds=0)
        listing = restarted.listing()
        self.assertEqual({item['group'] for item in listing['files']},
                         {canonical(str(first.parent.parent))})
        self.assertEqual(listing['total'], 2)
        self.assertEqual(len(self.calls), 2)

    def test_listing_groups_cached_episodes_without_rescan(self):
        first = self.video('seriali/czech/First/First.S01E01.mkv')
        second = self.video('seriali/czech/Second/Season 01/Second.S01E01.mkv')
        self.scan()
        restarted = Inventory(self.base / 'data', probe=self.inventory.probe, settle_seconds=0)
        listing = restarted.listing()
        groups = {item['path']: item['group'] for item in listing['files']}
        self.assertEqual(groups, {canonical(str(first)): canonical(str(first.parent)),
                                  canonical(str(second)): canonical(str(second.parent.parent))})
        self.assertEqual(listing['total'], 2)
        self.assertEqual(len(self.calls), 2)

    def test_outside_exception_rejected(self):
        with self.assertRaises(ValueError):
            self.inventory.exception(str(self.base / 'other'), 'folder')

    def test_browser_start_has_locations_without_scanning_media(self):
        result = self.inventory.browse_folders()
        self.assertIsNone(result['path'])
        self.assertTrue(result['locations'])
        self.assertFalse(result['selectable'])
        self.assertEqual(self.calls, [])

    def test_browser_lists_only_direct_folders_and_preserves_names(self):
        self.video('Seriál 2/Season 01/file.mkv')
        self.video('Film 1/file.mkv')
        self.video('root.mkv')
        result = self.inventory.browse_folders(str(self.media))
        self.assertEqual([f['name'] for f in result['folders']], ['Film 1', 'Seriál 2'])
        self.assertEqual(result['parent'], canonical(str(self.base)))
        self.assertEqual(result['breadcrumbs'][-1]['path'], canonical(str(self.media)))
        self.assertFalse(result['selectable'])
        self.assertEqual(self.calls, [])

    def test_browser_empty_folder_can_be_selected(self):
        other = self.base / 'Nová knihovna'; other.mkdir()
        result = self.inventory.browse_folders(str(other))
        self.assertTrue(result['selectable']); self.assertEqual(result['folders'], [])
        self.inventory.add_root(result['path'], 'series')
        self.assertFalse(self.inventory.browse_folders(str(other))['selectable'])

    def test_browser_rejects_overlapping_parent_and_child(self):
        child = self.media / 'child'; child.mkdir()
        for folder in (self.base, self.media, child):
            self.assertFalse(self.inventory.browse_folders(str(folder))['selectable'])

    def test_browser_bad_paths_have_actionable_error(self):
        for folder in ('relative', str(self.base / 'missing')):
            with self.assertRaises(ValueError):
                self.inventory.browse_folders(folder)
        with patch('upgrademe.os.scandir', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError, 'oprávnění'):
                self.inventory.browse_folders(str(self.media))


class HTTPTests(InventoryTests):
    # Inherit fixture helpers, not the parent test methods, via load_tests below.
    def setUp(self):
        super().setUp()
        self.server = Server(('127.0.0.1', 0), self.inventory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        super().tearDown()

    def request(self, route, data=None, headers=None):
        hdr = {'X-UpgradeMe': '1', 'Content-Type': 'application/json'}
        hdr.update(headers or {})
        req = Request(self.url + route, data=None if data is None else json.dumps(data).encode(), headers=hdr)
        return urlopen(req, timeout=5)

    def test_http_scan_and_exception_roundtrip(self):
        p = self.video(); self.scan()
        with self.request('/api/exceptions/save', {'path': str(p), 'scope': 'file', 'note': 'Není lepší originál'}) as r:
            self.assertEqual(r.status, 200)
        with self.request('/api/library') as r:
            self.assertEqual(json.load(r)['files'][0]['exception']['note'], 'Není lepší originál')
        with self.request('/api/exceptions/delete', {'path': str(p), 'scope': 'file'}) as r:
            self.assertEqual(r.status, 200)
        with self.request('/') as r:
            self.assertIn('SD videa', r.read().decode())
            self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])

    def test_http_cross_origin_and_unknown_host_rejected(self):
        with self.assertRaises(HTTPError) as e:
            self.request('/api/scan', {}, {'Origin': 'https://elsewhere.example'})
        self.assertEqual(e.exception.code, 403)
        with self.assertRaises(HTTPError) as e:
            self.request('/api/status', headers={'Host': 'attacker.example'})
        self.assertEqual(e.exception.code, 403)

    def test_http_invalid_root_and_path_traversal(self):
        with self.assertRaises(HTTPError) as e:
            self.request('/api/roots/add', {'path': 'relative/path'})
        self.assertEqual(e.exception.code, 400)
        with self.assertRaises(HTTPError) as e:
            self.request('/../upgrademe.py')
        self.assertEqual(e.exception.code, 404)

    def test_http_folder_picker_browse_select_and_duplicate(self):
        other = self.base / 'Vybraná složka'; other.mkdir()
        with self.request('/api/folders/list', {'path': str(other)}) as r:
            chosen = json.load(r)
        self.assertTrue(chosen['selectable'])
        with self.request('/api/roots/add', {'path': chosen['path'], 'kind': 'movie'}) as r:
            self.assertEqual(r.status, 200)
        with self.request('/api/folders/list', {'path': chosen['path']}) as r:
            self.assertFalse(json.load(r)['selectable'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/folders/list', {'path': 'relative'})
        self.assertEqual(error.exception.code, 400)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/ffprobe not installed')
class RealVideoTests(unittest.TestCase):
    def test_real_sd_hd_fullhd_and_incremental_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); media = base / 'media'; media.mkdir()

            def make(name, dimensions):
                p = media / name
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                f'color=c=blue:s={dimensions}:r=1', '-t', '1', '-c:v', 'mpeg4', str(p)],
                               check=True, capture_output=True, timeout=30)
                return p

            paths = [make('SD.mkv', '720x576'), make('HD.mkv', '1280x720'),
                     make('FullHD.mkv', '1920x1080'), make('CropHD.mkv', '1280x544')]
            before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths}
            calls = []

            def counting(path, exe):
                calls.append(path); return probe_video(path, exe)

            app = Inventory(base / 'data', probe=counting, settle_seconds=0)
            app.add_root(str(media), 'movie'); app.start_scan(background=False)
            self.assertEqual(len(calls), 4)
            items = {f['name'].lower(): f for f in app.listing()['files']}
            self.assertEqual((items['sd.mkv']['width'], items['sd.mkv']['height']), (720, 576))
            self.assertEqual(items['hd.mkv']['height'], 720)
            self.assertEqual(items['crophd.mkv']['height'], 544)
            self.assertNotIn('fullhd.mkv', items)
            app.start_scan(background=False); self.assertEqual(len(calls), 4)
            after = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths}
            self.assertEqual(before, after)
            make('SD.mkv', '1920x1080'); app.start_scan(background=False)
            self.assertEqual(len(calls), 5)
            self.assertNotIn('sd.mkv', {f['name'].lower(): f for f in app.listing()['files']})


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(InventoryTests))
    for name in HTTPTests.__dict__:
        if name.startswith('test_http_'):
            suite.addTest(HTTPTests(name))
    suite.addTests(loader.loadTestsFromTestCase(RealVideoTests))
    return suite
