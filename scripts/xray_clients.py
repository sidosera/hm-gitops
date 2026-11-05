#!/usr/bin/env python3
"""Manage accounts in the encrypted Git source; open a pull request without printing UUIDs or changing the cluster."""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE_LABELS = {
    'probe-e2e@local': 'probe-e2e@service-account',
    'spb-relay@local': 'spb-relay@service-account',
}


def public_inbounds(config):
    tag = 'reality-legacy' if any(i.get('tag') == 'reality-legacy' for i in config['inbounds']) else 'gateway'
    return [i for i in config['inbounds'] if i.get('protocol') == 'vless' and
            (i.get('tag') == tag or i.get('streamSettings', {}).get('network') == 'xhttp')]


def add_client(configs, email, zones):
    if not (re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', email) or
            re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)) or email.endswith('@service-account'):
        raise ValueError('Provide a client name or email; @service-account is reserved')
    result = copy.deepcopy(configs)
    if not zones or any(z not in result for z in zones):
        raise ValueError('Select existing gateway zones with --zone')
    # Find existing identity across every gateway, including zones not selected now.
    identities = {c['id'] for config in result.values() for i in config['inbounds']
                  for c in i.get('settings', {}).get('clients', []) if c.get('email') == email}
    if len(identities) > 1:
        raise ValueError('Email has multiple existing UUIDs; resolve explicitly instead of rotating credentials')
    ident = next(iter(identities), None) or str(uuid.uuid4())
    for zone in set(zones):
        inbounds = public_inbounds(result[zone])
        if not inbounds:
            raise ValueError('Selected zone has no supported public VLESS listener')
        for inbound in inbounds:
            clients = inbound['settings']['clients']
            if any(c.get('email') == email for c in clients):
                continue
            client = {'id': ident, 'email': email}
            if inbound.get('streamSettings', {}).get('security') == 'reality':
                client['flow'] = 'xtls-rprx-vision'
            clients.append(client)
    return result


def migrate_service_labels(configs):
    result = copy.deepcopy(configs)
    for config in result.values():
        for inbound in config['inbounds']:
            for client in inbound.get('settings', {}).get('clients', []):
                label = client.get('email')
                if label in SERVICE_LABELS:
                    client['email'] = SERVICE_LABELS[label]
    return result


def prepare_pr():
    if not shutil.which('but'):
        raise SystemExit('Install GitButler CLI (but) to create a PR, or use --local-only.')
    try:
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, check=True).stdout
        branch = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], cwd=ROOT, capture_output=True, check=True).stdout.strip()
        if status or branch != b'main':
            raise SystemExit('Start from a clean main checkout; existing work will not be committed.')
        subprocess.run(['but', 'pull', '--check'], cwd=ROOT, capture_output=True, check=True)
        local = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, check=True).stdout
        remote = subprocess.run(['git', 'rev-parse', 'origin/main'], cwd=ROOT, capture_output=True, check=True).stdout
        if local != remote:
            raise SystemExit('main is not current with origin/main. Update with but pull and retry.')
    except subprocess.CalledProcessError:
        raise SystemExit('Could not verify clean, current main. Check GitButler and remote access; no changes written.')


def open_client_pr(changed_files=None, title="Add VPN client", details=None):
    branch = 'codex/vpn-client-' + secrets.token_hex(6)
    # Client identities belong only in the encrypted file, never public GitHub metadata.
    message = """Add VPN client

## Summary

email → generated/reused UUID → SOPS-encrypted Secret → Flux after merge

Grants client access to the selected public gateways. Existing accounts and service credentials are preserved. Client identity is deliberately omitted from public PR metadata.

## Evidence

- Before: requested access was absent from the encrypted source.
- After: the account is present in the selected gateway configurations; UUIDs are not printed or committed in plaintext.

## Merge Danger

**Door:** two-way; remove the account in a subsequent encrypted Git change to revoke access.

**Blast Radius:** VPN access. Flux rolls Xray workloads after merge; active sessions may reconnect.
"""
    if details is not None:
        message = title + '\n\n' + details
    expected_files = set(changed_files or ['conf/xray/credentials-secret.sops.yaml'])
    try:
        status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=ROOT,
                                capture_output=True, check=True).stdout.decode().splitlines()
        if {line[3:] for line in status} != expected_files or any(line[:2] not in (' M', 'M ') for line in status):
            raise SystemExit('Unexpected working-tree changes; encrypted account prepared, but nothing committed.')
        subprocess.run(['but', 'commit', '-b', branch, '-m', title],
                       cwd=ROOT, capture_output=True, check=True)
        result = subprocess.run(['but', 'pr', 'new', branch, '-F', '-'],
                                input=message.encode(), cwd=ROOT, capture_output=True, check=True)
        print(result.stdout.decode().strip())
    except subprocess.CalledProcessError:
        raise SystemExit('PR creation failed; encrypted work is preserved. Inspect but status and retry but pr new <branch> -t.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add', help='Generate or reuse one UUID for a client name; open an encrypted account PR')
    add.add_argument('--name', '--email', dest='email', required=True, help='Client label; an email address is optional')
    add.add_argument('--zone', action='append', help='Limit access to these zones; default: all configured public gateways')
    add.add_argument('--local-only', action='store_true', help='Only prepare the encrypted file; skip commit and PR')
    config = sub.add_parser('config', help='Export a private client profile; preserve UUID when changing its address')
    config.add_argument('--name', '--email', dest='email', required=True)
    config.add_argument('--zone', required=True)
    config.add_argument('--host', help='New connection IP or DNS name; defaults to gateway inventory')
    config.add_argument('--output-dir', type=Path, required=True)
    server = sub.add_parser('server', help='Prepare a PR replacing a physical host address; preserve client UUIDs')
    server.add_argument('--host', required=True, help='Stable inventory slot, e.g. ru-spb-1/1')
    server.add_argument('--ip', required=True, help='New public IPv4 address')
    server.add_argument('--ipv6', help='New public IPv6; omitted means remove the old IPv6')
    server.add_argument('--ssh-host-key', required=True, help='New ed25519 public key, verified out of band')
    server.add_argument('--local-only', action='store_true')
    sub.add_parser('migrate-service-labels', help='Rename existing service labels without changing any UUID')
    args = parser.parse_args()
    if args.command == 'server':
        from xray_servers import replace_server
        from cluster_inventory import validate
        if not args.local_only:
            prepare_pr()
        inventory_file = ROOT / 'cluster/clusters/hm-cluster/hosts.yml'
        known_file = ROOT / 'vm/ssh_known_hosts'
        try:
            inventory, known = replace_server(yaml.safe_load(inventory_file.read_text()), known_file.read_text(),
                                              args.host, args.ip, args.ssh_host_key, args.ipv6)
            validate(inventory, yaml.safe_load(inventory_file.with_name('profile.yml').read_text()))
        except (ValueError, KeyError):
            raise SystemExit('Invalid server replacement; verify host slot, new addresses and independently verified SSH key. No changes written.')
        inventory_file.write_text(yaml.safe_dump(inventory, sort_keys=False))
        known_file.write_text(known)
        if not args.local_only:
            open_client_pr(['cluster/clusters/hm-cluster/hosts.yml', 'vm/ssh_known_hosts'], 'Replace VPN host',
                '## Summary\n\nNew physical address → inventory and pinned SSH key → existing Ansible bootstrap.\n\n'
                'Logical host, zone, private network identities and all client UUIDs remain unchanged.\n\n'
                '## Evidence\n\nInventory validation passed. No credential file is modified. No live host or DNS record is changed by this command.\n\n'
                '## Merge Danger\n\n**Door:** two-way inventory change; actual cutover requires a maintenance plan.\n\n'
                '**Blast Radius:** host replacement. Prepare key-based SSH on the new host, provision with Cluster Converge, '
                'verify VPN connectivity, then move public DNS or issue updated client profiles. '
                'Control-plane replacement also requires datastore recovery or an explicit cluster rebuild/rejoin; merging this PR does not perform that recovery.\n')
        else:
            print('Prepared inventory and pinned SSH key. No live host, DNS record or client UUID changed.')
        return
    if args.command == 'config':
        command = [sys.executable, str(ROOT / 'scripts/xray_share_url.py'), '--email', args.email,
                   '--zone', args.zone, '--output-dir', str(args.output_dir)]
        if args.host:
            command.extend(['--host', args.host])
        result = subprocess.run(command, cwd=ROOT, capture_output=True)
        if result.returncode:
            raise SystemExit('Profile export failed; verify account label, zone, SOPS identity and private output directory.')
        print(result.stdout.decode().strip())
        return
    create_pr = args.command == 'add' and not args.local_only
    if create_pr:
        prepare_pr()
    source = ROOT / 'conf/xray/credentials-secret.sops.yaml'
    try:
        decrypted = subprocess.run(['sops', '--decrypt', str(source)], capture_output=True, check=True).stdout
        document = yaml.safe_load(decrypted)
        configs = {k.removesuffix('.gateway.json'): json.loads(base64.b64decode(v))
                   for k, v in document['data'].items() if k.endswith('.gateway.json')}
        updated = add_client(configs, args.email, args.zone or sorted(configs)) if args.command == 'add' else migrate_service_labels(configs)
        if updated == configs:
            print('Already configured; no UUIDs or encrypted files changed.')
            return
        for zone, config in updated.items():
            if config != configs[zone]:
                document['data'][zone + '.gateway.json'] = base64.b64encode(json.dumps(config, indent=2).encode()).decode()
        encrypted = subprocess.run(['sops', '--encrypt', '--input-type', 'yaml', '--output-type', 'yaml',
                                    '--filename-override', str(source), '/dev/stdin'],
                                   input=yaml.safe_dump(document, sort_keys=False).encode(),
                                   cwd=ROOT, capture_output=True, check=True).stdout
        temporary = None
        try:
            # Only ciphertext touches disk. Atomic replacement avoids truncating the source.
            with tempfile.NamedTemporaryFile(dir=source.parent, delete=False) as handle:
                temporary = handle.name
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, source.stat().st_mode & 0o777)
            os.replace(temporary, source)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        if create_pr:
            open_client_pr()
        else:
            print('Updated encrypted Git source. No credentials printed; commit and publish to deploy.')
    except (ValueError, KeyError):
        raise SystemExit('Invalid account request or source configuration; no changes written.')
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit('SOPS operation failed; check your existing age identity and sops installation. No changes written.')


if __name__ == '__main__':
    main()
