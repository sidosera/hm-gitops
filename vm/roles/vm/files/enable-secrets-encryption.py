#!/usr/bin/env python3
"""Idempotent single-server K3s migration. Never prints credentials or key material."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
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
        with sqlite3.connect('file:' + str(SERVER / 'db/state.db') + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot) as target:
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
    backup(recipient, 'encrypted')
    print('Secrets encryption enabled; existing Secrets rewritten; application UUIDs unchanged', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('age_recipient')
    args = parser.parse_args()
    migrate(args.age_recipient)
