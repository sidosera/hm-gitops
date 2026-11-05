#!/usr/bin/env python3
"""Prepare SOPS-encrypted accounts for VPN Probe in the canonical Xray Secret.

Requires SOPS_AGE_KEY_FILE, sops, PyYAML and cryptography. Never prints credentials.
No cluster/API writes: resulting GitOps changes must be reviewed and published.
"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBE_EMAIL = 'probe-e2e@service-account'
LEGACY_PROBE_EMAIL = 'probe-e2e@local'
ENTRIES = {zone: (f'vpn-entry-{zone}.hm.svc.cluster.local', tag)
           for zone, tag in [('uk-ldn-1', 'reality-legacy'), ('ru-spb-1', 'gateway')]}


def add_account(config, candidate=None):
    result = copy.deepcopy(config)
    existing = {c['id'] for inbound in result['inbounds']
                for c in inbound.get('settings', {}).get('clients', [])
                if c.get('email') in (PROBE_EMAIL, LEGACY_PROBE_EMAIL)}
    if len(existing) > 1:
        raise ValueError('Inconsistent probe accounts; inspect encrypted source')
    ident = next(iter(existing), None)
    if ident is None:
        ident = candidate or str(uuid.uuid4())
    for inbound in result['inbounds']:
        for client in inbound.get('settings', {}).get('clients', []):
            if client.get('email') == LEGACY_PROBE_EMAIL:
                client['email'] = PROBE_EMAIL
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
    config = {'log': {'loglevel': 'warning'}, 'inbounds': [{'tag': 'probe', 'listen': '127.0.0.1',
            'port': 10800, 'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [{'tag': 'blocked', 'protocol': 'blackhole'}, outbound],
        'routing': {'rules': [{'type': 'field', 'inboundTag': ['probe'], 'outboundTag': 'public-vpn'}]}}
    if address.endswith('.svc.cluster.local'):
        # Resolve entry discovery with CoreDNS; direct website checks keep the observer's DNS.
        config['dns'] = {'servers': ['10.43.0.10'], 'queryStrategy': 'UseIPv4'}
        outbound['streamSettings']['sockopt'] = {'domainStrategy': 'ForceIPv4'}
        config['routing']['rules'].insert(0, {'type':'field', 'ip':['10.43.0.10'], 'port':'53', 'outboundTag':'cluster-dns'})
        config['outbounds'].append({'tag':'cluster-dns', 'protocol':'freedom'})
    return config



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
        parser.error('Explicit --write is required')
    if not os.environ.get('SOPS_AGE_KEY_FILE'):
        parser.error('Set SOPS_AGE_KEY_FILE to the existing private age identity')
    source = ROOT / 'conf/xray/credentials-secret.sops.yaml'
    document = yaml.safe_load(subprocess.check_output(['sops', '--decrypt', str(source)]))
    original = copy.deepcopy(document['data'])
    entries = {}
    for zone, (address, tag) in ENTRIES.items():
        key = zone + '.gateway.json'
        old = json.loads(base64.b64decode(document['data'][key]))
        config, ident = add_account(old)
        chosen = next(i for i in config['inbounds'] if i['tag'] == tag)
        entries[zone] = client_config(chosen, ident, address)
        if config != old:
            document['data'][key] = base64.b64encode(json.dumps(config, indent=2).encode()).decode()
    for observer, entry in [('ru-spb-1', 'uk-ldn-1'), ('uk-ldn-1', 'ru-spb-1')]:
        key = observer + '.probe.json'
        old_client = json.loads(base64.b64decode(document['data'][key])) if key in document['data'] else {}
        if old_client != entries[entry]:
            document['data'][key] = base64.b64encode(json.dumps(entries[entry], indent=2).encode()).decode()
    if document['data'] == original:
        print('Probe accounts already match Git; no UUIDs, encrypted files or workloads changed.')
        return
    source.write_bytes(encrypt(document, source))
    print('Prepared encrypted probe accounts and client secrets. No credentials printed; no cluster changes made.')


if __name__ == '__main__':
    main()
