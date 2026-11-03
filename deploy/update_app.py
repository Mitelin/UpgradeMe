"""Systemd update worker. Install a root-owned copy outside the Git checkout."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

LOG = logging.getLogger('upgrademe.update')


class Updater:
    def __init__(self, repo, data_dir, backup_dir, user, port=5066):
        self.repo = Path(repo)
        self.data_dir = Path(data_dir)
        self.backup_dir = Path(backup_dir)
        self.user = user
        self.port = port
        self.request = self.data_dir / 'update.request'

    def run(self, command, as_user=False):
        if as_user:
            command = ['runuser', '-u', self.user, '--', *command]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        if result.stdout.strip():
            LOG.info('%s', result.stdout.strip())
        return result.stdout.strip()

    def git(self, *args):
        return self.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.repo), *args], as_user=True)

    def publish(self, status, **details):
        target = self.data_dir / 'update-status.json'
        descriptor, name = tempfile.mkstemp(prefix='update-status-', dir=self.data_dir)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump({'status': status, **details}, handle)
                handle.flush()
                os.fchmod(handle.fileno(), 0o644)
                os.fsync(handle.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def health(self):
        with urlopen(f'http://127.0.0.1:{self.port}/api/status', timeout=3) as response:
            status = json.load(response)
        if not status.get('update', {}).get('enabled'):
            raise RuntimeError('The restarted application does not have updates enabled')
        if status['active'] is not None:
            raise RuntimeError('A scan started while an update was pending')
        return status

    def wait_ready(self, previous_instance):
        deadline = time.monotonic() + 30
        while True:
            try:
                status = self.health()
                if status['instance_id'] == previous_instance:
                    raise RuntimeError('The application did not restart')
                return
            except (URLError, TimeoutError, ConnectionError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

    @contextmanager
    def maintenance_lock(self):
        import fcntl
        # Use the existing inode; never replace the maintenance lock.
        descriptor = os.open(self.data_dir / 'media-maintenance.lock', os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'r+b') as handle:
            deadline = time.monotonic() + 300
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Media Maintenance is still running; try the update later')
                    time.sleep(0.5)
            yield

    def backup(self, revision):
        directory = self.backup_dir / ('upgrademe-update-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        directory.mkdir(mode=0o700, parents=True)
        (directory / 'revision').write_text(revision, encoding='ascii')
        with closing(sqlite3.connect(self.data_dir / 'upgrademe.sqlite3')) as source:
            with closing(sqlite3.connect(directory / 'upgrademe.sqlite3')) as target:
                source.backup(target)
        LOG.info('Backup: %s', directory)
        return str(directory)

    def install(self, old, target, instance):
        self.run(['systemctl', 'stop', 'upgrademe.service'])
        try:
            backup = self.backup(old)
            self.git('merge', '--ff-only', target)
            self.run(['/usr/bin/python3', '-m', 'py_compile', str(self.repo / 'upgrademe.py')],
                     as_user=True)
            self.publish('restarting', revision=target, backup=backup)
            self.run(['systemctl', 'start', 'upgrademe.service'])
            self.wait_ready(instance)
        except Exception:
            LOG.exception('Installation failed; restoring the previous code revision')
            self.run(['systemctl', 'stop', 'upgrademe.service'])
            # --keep refuses to discard unrelated local changes.
            self.git('reset', '--keep', old)
            self.run(['systemctl', 'start', 'upgrademe.service'])
            self.wait_ready(instance)
            raise
        return backup

    def update(self):
        if not self.request.exists():
            raise RuntimeError('No update was requested')
        try:
            self.publish('fetching')
            instance = self.health()['instance_id']
            if self.git('status', '--porcelain'):
                raise RuntimeError('Local Git changes must be resolved before updating')
            if self.git('branch', '--show-current') != 'main':
                raise RuntimeError('The application checkout must be on branch main')
            old = self.git('rev-parse', 'HEAD')
            self.git('fetch', 'origin', 'main')
            target = self.git('rev-parse', 'refs/remotes/origin/main^{commit}')
            self.git('merge-base', '--is-ancestor', old, target)
            self.publish('waiting')
            with self.maintenance_lock():
                if self.git('status', '--porcelain') or self.git('rev-parse', 'HEAD') != old:
                    raise RuntimeError('The checkout changed while the update was waiting')
                self.publish('installing')
                backup = self.install(old, target, instance)
                self.publish('complete', revision=target, backup=backup)
        except Exception as exc:
            LOG.exception('Application update failed')
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            self.publish('failed', error=(detail or str(exc))[-2000:])
            raise
        finally:
            self.request.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='/opt/upgrademe')
    parser.add_argument('--data-dir', default='/var/lib/upgrademe')
    parser.add_argument('--backup-dir', default='/var/backups')
    parser.add_argument('--user', default='upgrademe')
    parser.add_argument('--port', type=int, default=5066)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Updater(args.repo, args.data_dir, args.backup_dir, args.user, args.port).update()


if __name__ == '__main__':
    main()
