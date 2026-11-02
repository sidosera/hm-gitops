#!/usr/bin/env python3
"""Export VLESS profiles from the SOPS-encrypted source; never commit exports."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import yaml


def build_vless_url(user_id: str, host: str, port: int, query: dict, name: str) -> str:
    authority = f'[{host}]' if ':' in host else host
    return f'vless://{user_id}@{authority}:{port}?{urllib.parse.urlencode(query)}#{urllib.parse.quote(name, safe="")}'


def export_profiles(config: dict, hosts: list[str], public_key: str, https_host: str) -> dict[str, str]:
    profiles = {}
    for inbound in config['inbounds']:
        stream = inbound['streamSettings']
        if stream.get('security') == 'reality':
            reality = stream['realitySettings']
            client = inbound['settings']['clients'][0]
            for host in hosts:
                family = 'ipv6' if ':' in host else 'ipv4'
                for fingerprint in ('chrome', 'firefox', 'safari'):
                    name = f'hm-reality-{family}-{fingerprint}'
                    query = {'encryption': 'none', 'security': 'reality',
                             'sni': reality['serverNames'][0], 'fp': fingerprint,
                             'pbk': public_key, 'sid': reality['shortIds'][0],
                             'type': 'tcp', 'flow': client['flow']}
                    profiles[name] = build_vless_url(client['id'], host, 443, query, name)
        elif stream.get('network') == 'xhttp':
            name = 'hm-https-backup'
            query = {'encryption': 'none', 'security': 'tls', 'sni': https_host,
                     'fp': 'chrome', 'alpn': 'h2', 'type': 'xhttp',
                     'host': https_host, 'path': stream['xhttpSettings']['path'],
                     'mode': 'packet-up'}
            profiles[name] = build_vless_url(inbound['settings']['clients'][0]['id'],
                                            https_host, 443, query, name)
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_default = Path(__file__).resolve().parent.parent
    parser.add_argument('--repo', type=Path, default=repo_default)
    parser.add_argument('--host', action='append', help='Direct VPS address; repeat for both address families')
    parser.add_argument('--https-host', default='www.hackamonth.io')
    parser.add_argument('--xray', default='xray', help='Xray executable used to derive the public key')
    parser.add_argument('--output-dir', type=Path, help='Write private profile files instead of stdout')
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = repo / 'conf/proxy-service/config.sops.yaml'
    decrypted = subprocess.run(['sops', '--decrypt', str(source)], check=True,
                               capture_output=True, text=True).stdout
    secret = yaml.safe_load(decrypted)
    config = json.loads(secret['stringData']['config.json'])
    reality = next(i['streamSettings']['realitySettings'] for i in config['inbounds']
                   if i['streamSettings'].get('security') == 'reality')
    key_output = subprocess.run([args.xray, 'x25519', '-i', reality['privateKey']],
                                check=True, capture_output=True, text=True).stdout
    match = re.search(r'(?:Password \(PublicKey\)|Password|PublicKey|Public key):\s*(\S+)', key_output)
    if not match:
        raise SystemExit('Cannot read public key from this Xray version')
    hosts = args.host or []
    if not hosts:
        inventory = yaml.safe_load((repo / 'cluster/clusters/edge-01/hosts.yml').read_text())
        hosts.extend(inventory['vm_hosts'])
        if inventory.get('vm_node_ipv6'):
            hosts.append(inventory['vm_node_ipv6'])
    profiles = export_profiles(config, hosts, match.group(1), args.https_host)
    if args.output_dir:
        output = args.output_dir.resolve()
        if output == repo or repo in output.parents:
            raise SystemExit('Profile exports must be outside the repository')
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name, url in profiles.items():
            path = output / f'{name}.txt'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(url + '\n')
        print(f'Exported {len(profiles)} private profiles to {output}')
    else:
        print('\n'.join(profiles.values()))


if __name__ == '__main__':
    main()
