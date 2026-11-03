#!/usr/bin/env python3
"""Idempotent single-server K3s migration. Never prints credentials or key material."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import tarfile
import tempfile
import time
import yaml

CONFIG = Path('/etc/rancher/k3s/config.yaml')
SERVER = Path('/var/lib/rancher/k3s/server')
BACKUPS = Path('/var/backups/hm-k3s')


def command(*args, timeout=180):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout


def status():
    # /readyz can succeed before the K3s runtime/node annotation is initialized.
    until = time.monotonic() + 45
    while True:
        try:
            return command('k3s', 'secrets-encrypt', 'status', timeout=15)
        except subprocess.SubprocessError:
            if time.monotonic() >= until:
                raise
            time.sleep(2)


def wait_ready():
    until = time.monotonic() + 180
    while time.monotonic() < until:
        try:
            if command('k3s', 'kubectl', 'get', '--raw=/readyz', timeout=10).strip() == 'ok':
                return
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(2)
    raise RuntimeError('Kubernetes API did not become ready')


def backup(recipient, label):
    """SQLite online backup in tmpfs; only age ciphertext is written to disk."""
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    destination = BACKUPS / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + label + '.tar.age')
    temporary = destination.with_suffix('.partial')
    with tempfile.TemporaryDirectory(prefix='hm-k3s-backup-', dir='/dev/shm') as directory:
        snapshot = Path(directory) / 'state.db'
        with closing(sqlite3.connect('file:' + str(SERVER / 'db/state.db') + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as encrypted:
                process = subprocess.Popen(['age', '-r', recipient], stdin=subprocess.PIPE, stdout=encrypted, stderr=subprocess.PIPE)
                try:
                    with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
                        archive.add(snapshot, arcname='server/db/state.db')
                        archive.add(CONFIG, arcname='config.yaml')
                        archive.add(SERVER / 'token', arcname='server/token')
                        encryption = SERVER / 'cred/encryption-config.json'
                        if encryption.exists():
                            archive.add(encryption, arcname='server/cred/encryption-config.json')
                    process.stdin.close()
                    if process.wait(timeout=60) != 0:
                        raise RuntimeError('Age backup encryption failed')
                    encrypted.flush()
                    os.fsync(encrypted.fileno())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    print('Encrypted recovery backup: ' + str(destination), flush=True)
    return destination


def atomic_config(config):
    path = CONFIG.with_suffix('.yaml.hm-new')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        yaml.safe_dump(config, output, sort_keys=False)
        output.flush()
        os.fsync(output.fileno())
    path.replace(CONFIG)


def rewrite_legacy_predecessors():
    """Rewrite only records proven to have plaintext predecessors, metadata only.

    Each patch tests resourceVersion and preserves data/type/UID. There is no
    --all mutation, key generation, deployment update or pod-template change.
    """
    database = SERVER / 'db/state.db'
    if not database.exists():
        return False
    query = """SELECT k.name,k.old_value FROM kine k
        JOIN (SELECT name,MAX(id) id FROM kine WHERE name LIKE ? GROUP BY name) latest
        ON k.id=latest.id WHERE k.deleted=0"""
    with closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as connection:
        rows = connection.execute(query, ('/registry/secrets/%',)).fetchall()
    names = [name for name, value in rows if value and not bytes(value).startswith(b'k8s:enc:')]
    if not names:
        return False
    if len(names) > 64:
        raise RuntimeError('Legacy predecessor rewrite exceeds the bounded single-server migration size')
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    for path in names:
        prefix, namespace, name = path.rsplit('/', 2)
        if prefix != '/registry/secrets':
            raise RuntimeError('Unexpected Secret storage path')
        before = json.loads(command('k3s', 'kubectl', '-n', namespace, 'get', 'secret', name, '-o', 'json'))
        operations = [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': before['metadata']['resourceVersion']}]
        if 'annotations' in before['metadata']:
            operations.append({'op': 'add', 'path': '/metadata/annotations/hm.gitops~1storage-encrypted-at', 'value': stamp})
        else:
            operations.append({'op': 'add', 'path': '/metadata/annotations', 'value': {'hm.gitops/storage-encrypted-at': stamp}})
        # No Secret values appear in argv/stdout. Only the version and annotation
        # are sent to the API; a concurrent update rejects the entire patch.
        subprocess.run(['k3s', 'kubectl', '-n', namespace, 'patch', 'secret', name,
                        '--type=json', '--patch-file=/dev/stdin'], input=json.dumps(operations),
                       text=True, capture_output=True, check=True, timeout=30)
        after = json.loads(command('k3s', 'kubectl', '-n', namespace, 'get', 'secret', name, '-o', 'json'))
        def identity(document):
            return hashlib.sha256(json.dumps({'data': document.get('data', {}), 'type': document.get('type'),
                'uid': document['metadata']['uid']}, sort_keys=True).encode()).digest()
        if identity(before) != identity(after):
            raise RuntimeError('Secret data, type or identity changed; stopping migration')
    with closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as connection:
        rows = connection.execute(query, ('/registry/secrets/%',)).fetchall()
    if any(value and not bytes(value).startswith(b'k8s:enc:') for name, value in rows):
        raise RuntimeError('A current Secret still has a plaintext predecessor')
    print(f'Secrets storage predecessors rewritten; {len(names)} metadata-only patches, UUIDs unchanged', flush=True)
    return True


def finish_sqlite_history():
    """Reclaim legacy pages online, after normal Kine history compaction.

    No service stop/restart, table-row edits, or database file replacement.
    The native SQLite VACUUM is transactional, with a five-second work budget
    and a one-second lock timeout; busy/timeout leaves a resumable marker.
    """
    database = SERVER / 'db/state.db'
    if not database.exists():
        return False
    marker = SERVER / '.hm-plaintext-secret-pages'
    def plaintext_count():
        with closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as connection:
            rows = connection.execute('SELECT value,old_value FROM kine WHERE name LIKE ?', ('/registry/secrets/%',)).fetchall()
        return sum(bool(value) and not bytes(value).startswith(b'k8s:enc:') for row in rows for value in row)
    count = plaintext_count()
    if count and not marker.exists():
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write('pending')
    if not marker.exists():
        return False
    until = time.monotonic() + 900
    print('Waiting for native Kine compaction of pre-encryption history', flush=True)
    while count:
        if time.monotonic() >= until:
            raise RuntimeError('Historical plaintext has not compacted yet; safe to rerun migration later')
        time.sleep(15)
        count = plaintext_count()
    with closing(sqlite3.connect(database, timeout=1)) as connection:
        if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'wal':
            raise RuntimeError('Online cleanup requires the existing WAL journal mode')
        if marker.read_text() != 'vacuumed':
            deadline = time.monotonic() + 5
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                connection.execute('VACUUM')
            finally:
                connection.set_progress_handler(None, 0)
            marker.write_text('vacuumed')
        until = time.monotonic() + 30
        while True:
            busy, _, _ = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if not busy:
                break
            if time.monotonic() >= until:
                raise RuntimeError('Checkpoint busy; leave API running and rerun cleanup later')
            time.sleep(1)
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite verification failed')
    marker.unlink()
    print('Historical plaintext removed from SQLite records; pages reclaimed online, API kept running', flush=True)
    return True


def migrate(recipient):
    config = yaml.safe_load(CONFIG.read_text()) or {}
    try:
        initial = status()
    except subprocess.SubprocessError:
        # A timed-out initialization may have written the provider config before
        # publishing the node annotation; restart safely resumes that state.
        encryption = SERVER / 'cred/encryption-config.json'
        if not encryption.exists():
            raise
        json.loads(encryption.read_text())
        initial = '' 
    if 'Encryption Status: Enabled' in initial and config.get('secrets-encryption') is True:
        rewritten = rewrite_legacy_predecessors()
        cleaned = finish_sqlite_history()
        if rewritten or cleaned:
            backup(recipient, 'encrypted')
        print('Secrets encryption already enabled; no keys changed', flush=True)
        return
    # Only this supported single-server procedure is automated. New server installs
    # enable encryption from their first start in the shared Ansible server config.
    nodes = json.loads(command('k3s', 'kubectl', 'get', 'nodes', '-o', 'json'))
    servers = [n for n in nodes['items'] if 'node-role.kubernetes.io/control-plane' in n['metadata'].get('labels', {})]
    if len(servers) != 1:
        raise RuntimeError('Existing multi-server clusters require the coordinated K3s HA migration procedure')
    backup(recipient, 'before-encryption')
    encryption_file = SERVER / 'cred/encryption-config.json'
    if not encryption_file.exists():
        print('Initializing K3s encryption configuration', flush=True)
        try:
            command('k3s', 'secrets-encrypt', 'enable')
        except subprocess.SubprocessError:
            if not encryption_file.exists():
                raise
            json.loads(encryption_file.read_text())
            print('Encryption configuration initialized; resuming activation after CLI timeout', flush=True)
    if config.get('secrets-encryption') is not True or not initial:
        config['secrets-encryption'] = True
        atomic_config(config)
        print('Restarting control plane with secrets encryption configured', flush=True)
        command('systemctl', 'restart', 'k3s')
        wait_ready()
    current = status()
    if 'Encryption Status: Enabled' not in current:
        if 'Current Rotation Stage: start' not in current:
            raise RuntimeError('Unexpected encryption stage; migration stopped without deleting any keys')
        print('Enabling encryption and rewriting existing Secrets', flush=True)
        command('k3s', 'secrets-encrypt', 'rotate-keys')
        until = time.monotonic() + 180
        while time.monotonic() < until:
            if 'Current Rotation Stage: reencrypt_finished' in status():
                break
            time.sleep(2)
        else:
            raise RuntimeError('Secrets reencryption did not finish')
        print('Restarting control plane to finish encryption activation', flush=True)
        command('systemctl', 'restart', 'k3s')
        wait_ready()
    final = status()
    if 'Encryption Status: Enabled' not in final:
        raise RuntimeError('Encryption did not become enabled')
    rewrite_legacy_predecessors()
    finish_sqlite_history()
    backup(recipient, 'encrypted')
    print('Secrets encryption enabled; existing Secrets rewritten; application UUIDs unchanged', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('age_recipient')
    args = parser.parse_args()
    migrate(args.age_recipient)
