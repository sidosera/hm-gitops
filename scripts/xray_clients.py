#!/usr/bin/env python3
"""Manage accounts in the encrypted Git source; never print UUIDs or change the cluster."""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
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
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or email.endswith('@service-account'):
        raise ValueError('Provide a real client email; @service-account is reserved')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add', help='Generate or reuse one UUID for an email; update selected public gateways')
    add.add_argument('--email', required=True)
    add.add_argument('--zone', action='append', required=True, help='Repeat to grant access to additional zones')
    sub.add_parser('migrate-service-labels', help='Rename existing service labels without changing any UUID')
    args = parser.parse_args()
    source = ROOT / 'conf/xray/credentials-secret.sops.yaml'
    try:
        decrypted = subprocess.run(['sops', '--decrypt', str(source)], capture_output=True, check=True).stdout
        document = yaml.safe_load(decrypted)
        configs = {k.removesuffix('.gateway.json'): json.loads(base64.b64decode(v))
                   for k, v in document['data'].items() if k.endswith('.gateway.json')}
        updated = add_client(configs, args.email, args.zone) if args.command == 'add' else migrate_service_labels(configs)
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
        print('Updated encrypted Git source. No credentials printed; commit and publish to deploy.')
    except (ValueError, KeyError):
        raise SystemExit('Invalid account request or source configuration; no changes written.')
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit('SOPS operation failed; check your existing age identity and sops installation. No changes written.')


if __name__ == '__main__':
    main()
