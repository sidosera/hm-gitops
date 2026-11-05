#!/usr/bin/env python3
"""Export VLESS profiles from the SOPS-encrypted source; never commit exports."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.parse
import yaml
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def build_vless_url(user_id: str, host: str, port: int, query: dict, name: str) -> str:
    authority = f'[{host}]' if ':' in host else host
    return f'vless://{user_id}@{authority}:{port}?{urllib.parse.urlencode(query)}#{urllib.parse.quote(name, safe="")}'


def export_profiles(config: dict, hosts: list[str], public_key: str, https_host: str, email: str | None = None) -> dict[str, str]:
    profiles = {}
    for inbound in config['inbounds']:
        stream = inbound['streamSettings']
        if email is not None:
            selected = [c for c in inbound['settings']['clients'] if c.get('email') == email]
            if not selected:
                continue
            inbound = {**inbound, 'settings': {**inbound['settings'], 'clients': selected}}
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


def derive_public_key(private_key: str) -> str:
    raw = base64.urlsafe_b64decode(private_key + '=' * (-len(private_key) % 4))
    public = X25519PrivateKey.from_private_bytes(raw).public_key()
    return base64.urlsafe_b64encode(public.public_bytes(Encoding.Raw, PublicFormat.Raw)).rstrip(b'=').decode()


def write_private_profile(path: Path, url: str) -> None:
    # Refuse existing files, including symlinks; never truncate an unexpected target.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(url + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_default = Path(__file__).resolve().parent.parent
    parser.add_argument('--repo', type=Path, default=repo_default)
    parser.add_argument('--email', help='Select a client by its server-side email label')
    parser.add_argument('--zone', default='uk-ldn-1', help='Gateway zone to export')
    parser.add_argument('--host', action='append', help='Direct VPS address; repeat for both address families')
    parser.add_argument('--https-host', default='www.hackamonth.io')
    parser.add_argument('--output-dir', type=Path, help='Write private profile files instead of stdout')
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = repo / 'conf/xray/credentials-secret.sops.yaml'
    decrypted = subprocess.run(['sops', '--decrypt', str(source)], check=True,
                               capture_output=True, text=True).stdout
    secret = yaml.safe_load(decrypted)
    key = args.zone + '.gateway.json'
    if key not in secret['data']:
        raise SystemExit('No Gateway credentials configured for this zone')
    config = json.loads(base64.b64decode(secret['data'][key]))
    # The UK public TCP route serves reality-legacy; avoid exporting the private parallel listener.
    public_tag = 'reality-legacy' if any(i.get('tag') == 'reality-legacy' for i in config['inbounds']) else 'gateway'
    config['inbounds'] = [i for i in config['inbounds'] if i.get('tag') == public_tag or i.get('streamSettings', {}).get('network') == 'xhttp']
    reality = next(i['streamSettings']['realitySettings'] for i in config['inbounds']
                   if i['streamSettings'].get('security') == 'reality')
    public_key = derive_public_key(reality['privateKey'])
    hosts = args.host or []
    if not hosts:
        inventory = yaml.safe_load((repo / 'cluster/clusters/hm-cluster/hosts.yml').read_text())
        machines = [(address, machine) for address, machine in inventory['vm_node_settings'].items() if machine.get('zone') == args.zone]
        if len(machines) != 1:
            raise SystemExit('Specify --host when a zone has multiple or no Gateway machines')
        address, machine = machines[0]
        hosts.append(address)
        ipv6 = machine.get('ipv6')
        if ipv6:
            hosts.append(ipv6)
    profiles = export_profiles(config, hosts, public_key, args.https_host, args.email)
    if not profiles:
        raise SystemExit('No public client profiles match the requested email')
    if args.output_dir:
        output = args.output_dir.resolve()
        if output == repo or repo in output.parents:
            raise SystemExit('Profile exports must be outside the repository')
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = output.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SystemExit('Profile directory must be owned by you and accessible only to you (chmod 700)')
        for name, url in profiles.items():
            write_private_profile(output / f'{name}.txt', url)
        print(f'Exported {len(profiles)} private profiles to {output}')
    else:
        print('\n'.join(profiles.values()))


if __name__ == '__main__':
    main()
