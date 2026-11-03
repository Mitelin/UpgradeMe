"""UpgradeMe: read-only video inventory with a persistent incremental cache."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts',
                    '.mts', '.mpg', '.mpeg', '.webm', '.vob', '.flv', '.ogv', '.3gp', '.asf', '.divx'}
IGNORED_DIRS = {'@eadir', '#recycle', '$recycle.bin', '.snapshot', '.ripper_state',
                '.ripper_quarantine', '.simpleripper_quarantine', '.simpleripper_locks'}
EPISODE = re.compile(r'(?i)(?:\b|[_. -])(?:s\d{1,3}e\d{1,4}|\d{1,3}x\d{1,4})(?:\b|[_. -])')
SEASON = re.compile(
    r'(?i)^(?:(?:(?:season|series|sez[oó]na|s[eé]rie)[ ._-]*\d+|s\d{1,3})(?:[ ._-]+.+)?|specials)$')
LOG = logging.getLogger('upgrademe')


def stamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def canonical(value):
    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError('Zadejte absolutní cestu ke složce na serveru.')
    return os.path.normcase(os.path.normpath(value.strip()))


def within(path, parent):
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def group_for(path, root, kind):
    """Return a real folder path, never an invented title, for an expandable show."""
    p, base = Path(path), Path(root)
    parts = p.relative_to(base).parts
    # Episodes may live inside their own folder below a season. Ignore library
    # categories above the show and use the nearest season within this root.
    season_index = next((i for i in range(len(parts) - 2, -1, -1)
                         if SEASON.fullmatch(parts[i])), None)
    is_series = kind == 'series' or (kind == 'auto' and (
        EPISODE.search(p.stem) or season_index is not None))
    if not is_series:
        return None
    return str(base.joinpath(*parts[:season_index]) if season_index is not None else p.parent)


class ScanCancelled(Exception):
    pass


def _probe_output(command, check_cancelled=None):
    if check_cancelled:
        check_cancelled()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding='utf-8', errors='replace',
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        deadline = time.monotonic() + 45
        try:
            while True:
                if check_cancelled:
                    check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 45)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if check_cancelled:
                check_cancelled()
            if process.returncode:
                raise ValueError((stderr.strip() or 'ffprobe selhal')[:1200])
            return stdout
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def probe_video(path, executable, check_cancelled=None):
    command = [executable, '-v', 'error', '-show_entries',
               'stream=codec_type,width,height:stream_disposition=attached_pic', '-of', 'json', path]
    streams = json.loads(_probe_output(command, check_cancelled)).get('streams', [])
    videos = [s for s in streams if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')
              and int(s.get('width', 0)) > 0 and int(s.get('height', 0)) > 0]
    if not videos:
        raise ValueError('Soubor nemá čitelnou video stopu.')
    stream = max(videos, key=lambda s: int(s['width']) * int(s['height']))
    return int(stream['width']), int(stream['height'])


class Inventory:
    def __init__(self, data_dir, ffprobe='ffprobe', probe=None, settle_seconds=120,
                 maintenance_trigger=None, shared_scan_lock=None):
        if maintenance_trigger and not shared_scan_lock:
            raise ValueError('Režim údržby vyžaduje společný zámek skenu.')
        if shared_scan_lock and os.name != 'posix':
            raise ValueError('Společný zámek údržby vyžaduje POSIX systém (Linux).')
        self.maintenance_trigger = Path(maintenance_trigger) if maintenance_trigger else None
        self.shared_scan_lock = Path(shared_scan_lock) if shared_scan_lock else None
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'upgrademe.sqlite3'
        self.ffprobe = ffprobe
        self.probe = probe or probe_video
        self.settle_seconds = settle_seconds
        self.scan_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.stop = threading.Event()
        self.scan_cancel = threading.Event()
        self.active = None
        self.worker = None
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS roots (
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, root_id INTEGER NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    width INTEGER, height INTEGER, error TEXT, seen_run INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS files_root ON files(root_id);
                CREATE TABLE IF NOT EXISTS exceptions (
                    path TEXT NOT NULL, scope TEXT NOT NULL, note TEXT NOT NULL,
                    PRIMARY KEY(path, scope));
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, started TEXT NOT NULL,
                    finished TEXT, status TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            db.execute("UPDATE runs SET status='interrupted', finished=? WHERE status='running'", (stamp(),))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def roots(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM roots ORDER BY path')]

    def browse_folders(self, path=None):
        roots = self.roots()
        locations = [{'name': 'Sledováno: ' + (Path(r['path']).name or r['path']),
                      'path': r['path']} for r in roots]
        locations.append({'name': 'Domovská složka', 'path': canonical(str(Path.home()))})
        if os.name == 'nt':
            import ctypes
            drives = ctypes.windll.kernel32.GetLogicalDrives()
            locations.extend({'name': f'Disk {chr(65 + i)}:', 'path': f'{chr(97 + i)}:\\'}
                             for i in range(26) if drives & (1 << i))
        else:
            locations.append({'name': 'Souborový systém /', 'path': '/'})
            locations.extend({'name': p, 'path': p} for p in ('/mnt', '/media', '/srv') if Path(p).is_dir())
        if path is None:
            return {'path': None, 'parent': None, 'breadcrumbs': [], 'folders': [],
                    'locations': locations, 'selectable': False, 'reason': 'Vyberte disk nebo složku.'}
        path = canonical(path)
        folders = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            folders.append({'name': entry.name, 'path': canonical(entry.path)})
                    except OSError:
                        continue
        except OSError as exc:
            raise ValueError('Složku nelze otevřít. Ověřte připojení a oprávnění: ' + path) from exc
        folders.sort(key=lambda item: item['name'].casefold())
        p = Path(path)
        reason = ''
        if any(within(path, r['path']) or within(r['path'], path) for r in roots):
            reason = 'Tato složka je již sledovaná nebo se překrývá se sledovanou složkou.'
        breadcrumbs = [{'name': part.name or str(part), 'path': str(part)}
                       for part in [*reversed(p.parents), p]]
        return {'path': path, 'parent': str(p.parent) if p.parent != p else None,
                'breadcrumbs': breadcrumbs, 'folders': folders, 'locations': locations,
                'selectable': not reason, 'reason': reason}

    def add_root(self, path, kind):
        path = canonical(path)
        if kind not in {'auto', 'movie', 'series'}:
            raise ValueError('Neznámý typ knihovny.')
        if not Path(path).is_dir():
            raise ValueError('Složka na tomto serveru není dostupná.')
        if not self.scan_lock.acquire(blocking=False):
            raise ValueError('Počkejte na dokončení skenu.')
        try:
            with self.db() as db:
                for root in db.execute('SELECT path FROM roots'):
                    if within(path, root['path']) or within(root['path'], path):
                        raise ValueError('Tato složka se překrývá s již sledovanou složkou.')
                db.execute('INSERT INTO roots(path,kind) VALUES (?,?)', (path, kind))
        finally:
            self.scan_lock.release()

    def remove_root(self, root_id):
        if not self.scan_lock.acquire(blocking=False):
            raise ValueError('Počkejte na dokončení skenu.')
        try:
            with self.db() as db:
                db.execute('DELETE FROM files WHERE root_id=?', (int(root_id),))
                db.execute('DELETE FROM roots WHERE id=?', (int(root_id),))
        finally:
            self.scan_lock.release()

    def exception(self, path, scope, note='', delete=False):
        path = canonical(path)
        if scope not in {'file', 'folder'}:
            raise ValueError('Neznámý rozsah výjimky.')
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError('Poznámka smí mít nejvýše 4000 znaků.')
        with self.db() as db:
            if delete:
                db.execute('DELETE FROM exceptions WHERE path=? AND scope=?', (path, scope))
            else:
                if not any(within(path, r['path']) for r in self.roots()):
                    raise ValueError('Výjimka musí patřit do sledované složky.')
                db.execute('INSERT INTO exceptions VALUES(?,?,?) ON CONFLICT(path,scope) '
                           'DO UPDATE SET note=excluded.note', (path, scope, note.strip()))

    def listing(self):
        with self.db() as db:
            roots = {r['id']: dict(r) for r in db.execute('SELECT * FROM roots')}
            exceptions = [dict(r) for r in db.execute('SELECT * FROM exceptions ORDER BY path')]
            by_key = {(e['path'], e['scope']): e for e in exceptions}
            files = []
            for row in db.execute('SELECT * FROM files WHERE height < 1080 OR error IS NOT NULL'):
                item = dict(row)
                root = roots.get(item['root_id'])
                if not root:
                    continue
                path = item['path']
                excluded = by_key.get((path, 'file'))
                for parent in Path(path).parents:
                    if excluded:
                        break
                    excluded = by_key.get((str(parent), 'folder'))
                item.update(name=Path(path).name, root=root['path'],
                            group=group_for(path, root['path'], root['kind']), exception=excluded)
                files.append(item)
            total = db.execute('SELECT count(*) FROM files').fetchone()[0]
        return {'files': files, 'exceptions': exceptions, 'total': total}

    def status(self):
        with self.state_lock:
            active = dict(self.active) if self.active else None
        with self.db() as db:
            row = db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            last = dict(row) if row else None
            if last and last['result']:
                last['result'] = json.loads(last['result'])
        now = datetime.now().astimezone()
        upcoming = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if now >= upcoming:
            upcoming += timedelta(days=1)
        return {'active': active, 'last': last,
                'schedule_mode': 'maintenance' if self.maintenance_trigger else 'daily',
                'next_scan': None if self.maintenance_trigger else upcoming.isoformat(),
                'timezone': time.tzname, 'ffprobe_available': shutil.which(self.ffprobe) is not None,
                'roots': self.roots()}

    def start_scan(self, retry_errors=False, background=True, trigger_id=None):
        if not self.scan_lock.acquire(blocking=False):
            return False
        with self.state_lock:
            self.scan_cancel.clear()
            self.active = {'phase': 'Příprava', 'seen': 0, 'probed': 0, 'cached': 0,
                           'deferred': 0, 'errors': 0, 'current': '', 'stopping': False}
        if background:
            self.worker = threading.Thread(target=self._scan, args=(retry_errors, trigger_id), daemon=True)
            self.worker.start()
        else:
            self._scan(retry_errors, trigger_id)
        return True

    def cancel_scan(self):
        with self.state_lock:
            if self.active is None or self.active.get('finishing'):
                return False
            self.scan_cancel.set()
            self.active.update(stopping=True, phase='Zastavování skenu')
        return True

    def _check_cancelled(self):
        if self.scan_cancel.is_set():
            raise ScanCancelled('Sken byl zastaven uživatelem.')
        if self.stop.is_set():
            raise InterruptedError('Sken byl zastaven při ukončení aplikace.')

    def _progress(self, stats, **extra):
        with self.state_lock:
            self.active = {**stats, **extra, 'stopping': self.scan_cancel.is_set()}
            if self.scan_cancel.is_set():
                self.active['phase'] = 'Zastavování skenu'

    def _acquire_shared_lock(self, stats):
        if not self.shared_scan_lock:
            return None
        import fcntl
        handle = self.shared_scan_lock.open('a+b')
        acquired = False
        try:
            while True:
                self._check_cancelled()
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    self._progress(stats, phase='Příprava', current='')
                    return handle
                except BlockingIOError:
                    self._progress(stats, phase='Čekání na dokončení Media Maintenance', current='')
                    self.scan_cancel.wait(0.5)
        finally:
            if not acquired:
                handle.close()

    def _scan(self, retry_errors, trigger_id=None):
        stats = dict(seen=0, probed=0, cached=0, deferred=0, errors=0, removed=0)
        warnings = []
        run_id = None
        shared_handle = None
        flush_pending = None
        status = 'complete'
        started = time.monotonic()
        try:
            with self.db() as db:
                run_id = db.execute('INSERT INTO runs(started,status) VALUES(?,?)',
                                    (stamp(), 'running')).lastrowid
            shared_handle = self._acquire_shared_lock(stats)
            self._check_cancelled()
            if self.probe is probe_video and not shutil.which(self.ffprobe):
                raise ValueError('ffprobe nebyl nalezen. Nainstalujte FFmpeg nebo nastavte --ffprobe.')
            for root in self.roots():
                self._check_cancelled()
                # Never prune a root after a failed/incomplete traversal (e.g. disconnected NAS).
                walk_errors = []
                if not Path(root['path']).is_dir():
                    warnings.append('Nedostupná složka; cache zachována: ' + root['path'])
                    continue
                with self.db() as db:
                    cached = {r['path']: dict(r) for r in db.execute(
                        'SELECT * FROM files WHERE root_id=?', (root['id'],))}
                pending = []
                root_seen_before = stats['seen']
                last_flush = time.monotonic()

                def flush():
                    nonlocal last_flush
                    if not pending:
                        return
                    with self.db() as db:
                        db.executemany('''INSERT INTO files VALUES(?,?,?,?,?,?,?,?)
                            ON CONFLICT(path) DO UPDATE SET root_id=excluded.root_id,
                            size=excluded.size,mtime_ns=excluded.mtime_ns,width=excluded.width,
                            height=excluded.height,error=excluded.error,seen_run=excluded.seen_run''', pending)
                    pending.clear()
                    last_flush = time.monotonic()

                flush_pending = flush
                for directory, dirs, names in os.walk(root['path'], followlinks=False, onerror=walk_errors.append):
                    self._check_cancelled()
                    dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS
                               and not Path(directory, d).is_symlink()]
                    for name in names:
                        self._check_cancelled()
                        p = Path(directory, name)
                        if p.suffix.lower() not in VIDEO_EXTENSIONS or p.is_symlink():
                            continue
                        path = canonical(str(p))
                        stats['seen'] += 1
                        old = cached.get(path)
                        try:
                            stat = p.stat()
                            signature = (stat.st_size, stat.st_mtime_ns)
                            if old and signature == (old['size'], old['mtime_ns']) and not (retry_errors and old['error']):
                                stats['cached'] += 1
                                pending.append((path, root['id'], *signature, old['width'], old['height'], old['error'], run_id))
                            elif time.time() - stat.st_mtime < self.settle_seconds:
                                stats['deferred'] += 1
                                # Store a nonmatching signature so a settled file is probed next run.
                                pending.append((path, root['id'], -1, -1, None, None,
                                                'Soubor se právě mění; kontrola při dalším skenu.', run_id))
                            else:
                                self._progress(stats, phase='Čtení rozlišení', current=path)
                                width = height = error = None
                                try:
                                    stats['probed'] += 1
                                    # Use the spelling returned by the filesystem, not the normalized cache key.
                                    if self.probe is probe_video:
                                        width, height = self.probe(str(p), self.ffprobe, self._check_cancelled)
                                    else:
                                        width, height = self.probe(str(p), self.ffprobe)
                                    self._check_cancelled()
                                    after = p.stat()
                                    if signature != (after.st_size, after.st_mtime_ns):
                                        raise ValueError('Soubor se během analýzy změnil; kontrola při dalším skenu.')
                                except (ScanCancelled, InterruptedError):
                                    raise
                                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                                    error = str(exc)[:1200]
                                    width = height = None
                                    stats['errors'] += 1
                                    # A concurrent replacement must be retried even if its signature reverts.
                                    if 'během analýzy' in error:
                                        signature = (-1, -1)
                                pending.append((path, root['id'], *signature, width, height, error, run_id))
                        except (ScanCancelled, InterruptedError):
                            raise
                        except OSError as exc:
                            walk_errors.append(exc)
                            stats['errors'] += 1
                        if len(pending) >= 200 or time.monotonic() - last_flush >= 2:
                            self._check_cancelled()
                            flush()
                        self._progress(stats, phase='Procházení složek', current=path)
                flush()
                if cached and stats['seen'] == root_seen_before:
                    walk_errors.append(OSError('Dříve naplněná složka je náhle prázdná; ověřte připojení NAS.'))
                if walk_errors:
                    warnings.extend(str(e)[:600] for e in walk_errors[:20])
                    warnings.append('Neúplné čtení složky; chybějící záznamy nebyly smazány: ' + root['path'])
                else:
                    with self.db() as db:
                        stats['removed'] += db.execute('DELETE FROM files WHERE root_id=? AND seen_run<>?',
                                                       (root['id'], run_id)).rowcount
            self._check_cancelled()
            if warnings or stats['errors'] or stats['deferred']:
                status = 'warning'
        except (ScanCancelled, InterruptedError) as exc:
            status = 'cancelled' if isinstance(exc, ScanCancelled) else 'interrupted'
            warnings.append(str(exc))
            if flush_pending:
                try:
                    flush_pending()
                except sqlite3.Error as error:
                    status = 'failed'
                    warnings.append('Nelze uložit průběžné výsledky: ' + str(error))
                    LOG.exception('Saving partial scan results failed')
        except Exception as exc:
            status = 'failed'
            warnings.append(str(exc))
            LOG.exception('Scan failed')
        finally:
            with self.state_lock:
                self.active['finishing'] = True
                if self.scan_cancel.is_set() and status in ('complete', 'warning'):
                    status = 'cancelled'
                    warnings.append('Sken byl zastaven uživatelem.')
            stats.update(warnings=warnings, seconds=round(time.monotonic() - started, 2))
            try:
                if run_id is not None:
                    with self.db() as db:
                        db.execute('UPDATE runs SET finished=?,status=?,result=? WHERE id=?',
                                   (stamp(), status, json.dumps(stats, ensure_ascii=False), run_id))
                        if trigger_id and status != 'interrupted':
                            db.execute(
                                "INSERT INTO settings VALUES('maintenance_event',?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (trigger_id,))
            finally:
                if shared_handle is not None:
                    shared_handle.close()
                with self.state_lock:
                    self.active = None
                self.scan_lock.release()
            LOG.info('Scan %s: %s', status, stats)

    def schedule_tick(self, now=None):
        if self.maintenance_trigger:
            return self._maintenance_tick()
        now = now or datetime.now().astimezone()
        if now.hour < 5 or not self.roots():
            return False
        day = now.date().isoformat()
        with self.db() as db:
            last = db.execute("SELECT value FROM settings WHERE key='scheduled_day'").fetchone()
        if last and last[0] >= day:
            return False
        if not self.start_scan():
            return False
        with self.db() as db:
            db.execute("INSERT INTO settings VALUES('scheduled_day',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))
        return True

    def _maintenance_tick(self):
        if not self.roots():
            return False
        try:
            event = json.loads(self.maintenance_trigger.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id'].strip():
            raise ValueError('Neplatný signál dokončení Media Maintenance: chybí identifikátor běhu.')
        with self.db() as db:
            last = db.execute("SELECT value FROM settings WHERE key='maintenance_event'").fetchone()
        if last and last[0] == event['id']:
            return False
        return self.start_scan(trigger_id=event['id'])

    def scheduler(self):
        while not self.stop.is_set():
            try:
                self.schedule_tick()
            except Exception:
                LOG.exception('Scheduler failed')
            self.stop.wait(20)


def handler_for(inventory):
    static_dir = Path(__file__).parent / 'static'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            LOG.debug(format, *args)

        def send(self, code, value, content_type='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            host = urlsplit('//' + self.headers.get('Host', '')).hostname
            return host in self.server.allowed_hosts

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {'error': 'Nepovolený hostitel.'})
            route = urlsplit(self.path).path
            if route == '/api/status':
                return self.send(200, inventory.status())
            if route == '/api/library':
                return self.send(200, inventory.listing())
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if route in assets:
                file, content_type = assets[route]
                return self.send(200, (static_dir / file).read_bytes(), content_type)
            self.send(404, {'error': 'Nenalezeno.'})

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, {'error': 'Nepovolený hostitel.'})
            origin = self.headers.get('Origin')
            if self.headers.get('X-UpgradeMe') != '1' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                return self.send(403, {'error': 'Požadavek musí přijít z UI UpgradeMe.'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if size < 0 or size > 16384:
                    raise ValueError('Požadavek je příliš velký.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Neplatný požadavek.')
                route = urlsplit(self.path).path
                if route == '/api/folders/list':
                    return self.send(200, inventory.browse_folders(data.get('path')))
                elif route == '/api/roots/add':
                    inventory.add_root(data['path'], data.get('kind', 'auto'))
                elif route == '/api/roots/remove':
                    inventory.remove_root(data['id'])
                elif route == '/api/exceptions/save':
                    inventory.exception(data['path'], data['scope'], data.get('note', ''))
                elif route == '/api/exceptions/delete':
                    inventory.exception(data['path'], data['scope'], delete=True)
                elif route == '/api/scan':
                    if not inventory.roots():
                        raise ValueError('Nejprve přidejte sledovanou složku.')
                    if not inventory.start_scan(bool(data.get('retry_errors', False))):
                        return self.send(409, {'error': 'Sken již běží.'})
                elif route == '/api/scan/stop':
                    if not inventory.cancel_scan():
                        return self.send(409, {'error': 'Sken už neběží nebo právě dokončuje ukládání.'})
                else:
                    return self.send(404, {'error': 'Nenalezeno.'})
                self.send(200, {'ok': True})
            except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
                self.send(400, {'error': str(exc)})
            except Exception:
                LOG.exception('Request failed')
                self.send(500, {'error': 'Interní chyba. Podrobnosti jsou v logu serveru.'})

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, inventory, allowed_hosts=()):
        self.allowed_hosts = {'localhost', '127.0.0.1', address[0], *allowed_hosts}
        super().__init__(address, handler_for(inventory))


@contextmanager
def process_lock(data_dir):
    """OS-held lock is automatically released on a crash; shared data has one owner."""
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    handle = open(Path(data_dir) / 'upgrademe.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SystemExit('UpgradeMe již používá tuto datovou složku.')
    try:
        yield
    finally:
        handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5066)
    parser.add_argument('--data-dir', default=str(Path(__file__).parent / 'runtime'))
    parser.add_argument('--ffprobe', default='ffprobe')
    parser.add_argument('--allowed-host', action='append', default=[])
    parser.add_argument('--maintenance-trigger', help='JSON completion event; replaces the daily 05:00 schedule')
    parser.add_argument('--shared-scan-lock', help='Shared POSIX flock file used by scans and maintenance')
    args = parser.parse_args()
    if args.maintenance_trigger and not args.shared_scan_lock:
        parser.error('--maintenance-trigger requires --shared-scan-lock')
    if args.shared_scan_lock and os.name != 'posix':
        parser.error('--shared-scan-lock requires a POSIX system (Linux)')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    with process_lock(args.data_dir):
        inventory = Inventory(args.data_dir, args.ffprobe,
                              maintenance_trigger=args.maintenance_trigger,
                              shared_scan_lock=args.shared_scan_lock)
        server = Server((args.host, args.port), inventory, args.allowed_host)
        threading.Thread(target=inventory.scheduler, daemon=True).start()
        LOG.info('UpgradeMe: http://%s:%s — %s', args.host, args.port,
                 'after Media Maintenance' if args.maintenance_trigger else f'daily scan at 05:00 ({time.tzname})')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            inventory.stop.set()
            server.server_close()
            if inventory.worker:
                inventory.worker.join(timeout=50)


if __name__ == '__main__':
    main()
