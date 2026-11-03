#!/usr/bin/env python3
"""Prepare SOPS-encrypted accounts for VPN Probe. Run only after access approval.

Requires SOPS_AGE_KEY_FILE, sops, PyYAML and cryptography. Never prints credentials.
No cluster/API writes: resulting GitOps changes must be reviewed and published.
"""
import argparse
import base64
import copy
import datetime
import json
import os
from pathlib import Path
import subprocess
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBE_EMAIL = 'probe-e2e@local'
ENTRIES = {'uk-ldn-1': ('87.106.74.217', 'reality-legacy'), 'ru-spb-1': ('212.116.115.29', 'gateway')}


def add_account(config, candidate):
    result = copy.deepcopy(config)
    existing = {c['id'] for inbound in result['inbounds']
                for c in inbound.get('settings', {}).get('clients', [])
                if c.get('email') == PROBE_EMAIL}
    if len(existing) > 1:
        raise ValueError('Inconsistent probe accounts; inspect encrypted source')
    ident = next(iter(existing), candidate)
    for inbound in result['inbounds']:
        if inbound.get('streamSettings', {}).get('security') == 'reality':
            users = inbound['settings']['clients']
            if not any(c.get('email') == PROBE_EMAIL for c in users):
                users.append({'id': ident, 'flow': 'xtls-rprx-vision', 'email': PROBE_EMAIL})
    return result, ident


def client_config(inbound, ident, address):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    rs = inbound['streamSettings']['realitySettings']
    key = X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(rs['privateKey'] + '=' * (-len(rs['privateKey']) % 4)))
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode().rstrip('=')
    outbound = {'tag': 'public-vpn', 'protocol': 'vless', 'settings': {'vnext': [
        {'address': address, 'port': 443, 'users': [{'id': ident, 'encryption': 'none', 'flow': 'xtls-rprx-vision'}]}]},
        'streamSettings': {'network': 'raw', 'security': 'reality', 'realitySettings': {
            'serverName': rs['serverNames'][0], 'fingerprint': 'chrome', 'password': public, 'shortId': rs['shortIds'][0]}}}
    return {'log': {'loglevel': 'warning'}, 'inbounds': [{'tag': 'probe', 'listen': '127.0.0.1',
            'port': 10800, 'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{'tag': 'blocked', 'protocol': 'blackhole'}, outbound],
        'routing': {'rules': [{'type': 'field', 'inboundTag': ['probe'], 'outboundTag': 'public-vpn'}]}}


def encrypt(document, destination):
    # Plaintext remains in process pipes, outside tool output and Git.
    return subprocess.run(['sops', '--encrypt', '--input-type', 'yaml', '--output-type', 'yaml',
                           '--filename-override', str(destination), '/dev/stdin'],
                          input=yaml.safe_dump(document, sort_keys=False).encode(),
                          capture_output=True, check=True, cwd=ROOT).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Create/update the approved encrypted accounts and manifests')
    args = parser.parse_args()
    if not args.write:
        parser.error('Explicit --write is required after approving probe credential access')
    if not os.environ.get('SOPS_AGE_KEY_FILE'):
        parser.error('Set SOPS_AGE_KEY_FILE to the existing private age identity')
    staged, entries = {}, {}
    for zone, (address, tag) in ENTRIES.items():
        source = ROOT / 'services/gateway/overlays' / zone / 'credentials-secret.sops.yaml'
        document = yaml.safe_load(subprocess.check_output(['sops', '--decrypt', str(source)]))
        config, ident = add_account(json.loads(document['stringData']['credentials.json']), str(uuid.uuid4()))
        chosen = next(i for i in config['inbounds'] if i['tag'] == tag)
        entries[zone] = client_config(chosen, ident, address)
        document['stringData']['credentials.json'] = json.dumps(config, indent=2)
        staged[source] = encrypt(document, source)
    for source, target in [('ru-spb-1', 'uk-ldn-1'), ('uk-ldn-1', 'ru-spb-1')]:
        overlay = ROOT / 'services/vpn-probe/overlays' / source
        path = overlay / 'client-secret.sops.yaml'
        doc = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'vpn-probe-client', 'namespace': 'hm'},
               'type': 'Opaque', 'stringData': {'config.json': json.dumps(entries[target], indent=2)}}
        staged[path] = encrypt(doc, path)
        kpath = overlay / 'kustomization.yaml'
        kustomization = yaml.safe_load(kpath.read_text())
        if 'client-secret.sops.yaml' not in kustomization['resources']:
            kustomization['resources'].append('client-secret.sops.yaml')
        staged[kpath] = yaml.safe_dump(kustomization, sort_keys=False).encode()
    revision = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for zone in ENTRIES:
        path = ROOT / 'services/gateway/overlays' / zone / 'deployment-patch.yaml'
        doc = yaml.safe_load(path.read_text())
        doc['spec']['template'].setdefault('metadata', {}).setdefault('annotations', {})['hm.gitops/credentials-revision'] = revision
        staged[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    for path, data in staged.items():
        path.write_bytes(data)
    print('Prepared encrypted probe accounts and client secrets. No credentials printed; no cluster changes made.')


if __name__ == '__main__':
    main()
