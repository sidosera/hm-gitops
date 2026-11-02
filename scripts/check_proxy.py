#!/usr/bin/env python3
"""Probe an exported VLESS profile from the network where this script runs.

Requires Xray and curl. The profile stays private and is never printed.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.parse


def client_config(url, port):
    uri = urllib.parse.urlsplit(url.strip())
    if uri.scheme != 'vless' or not uri.username or not uri.hostname:
        raise ValueError('Expected a VLESS profile')
    query = dict(urllib.parse.parse_qsl(uri.query))
    user = {'id': urllib.parse.unquote(uri.username), 'encryption': 'none'}
    if query.get('flow'):
        user['flow'] = query['flow']
    security = query.get('security')
    stream = {'network': 'xhttp' if query.get('type') == 'xhttp' else 'raw', 'security': security}
    if security == 'reality':
        stream['realitySettings'] = {'serverName': query['sni'], 'fingerprint': query.get('fp', 'chrome'),
                                    'password': query['pbk'], 'shortId': query['sid']}
    elif security == 'tls':
        stream['tlsSettings'] = {'serverName': query['sni'], 'fingerprint': query.get('fp', 'chrome'),
                                'alpn': query.get('alpn', 'h2').split(',')}
    else:
        raise ValueError('Expected TLS or REALITY security')
    if stream['network'] == 'xhttp':
        stream['xhttpSettings'] = {'host': query.get('host', query['sni']),
                                   'path': query['path'], 'mode': query.get('mode', 'packet-up')}
    return {'log': {'loglevel': 'error'},
            'inbounds': [{'listen': '127.0.0.1', 'port': port, 'protocol': 'socks',
                          'settings': {'auth': 'noauth', 'udp': False}}],
            'outbounds': [{'protocol': 'vless', 'settings': {'vnext': [
                {'address': uri.hostname, 'port': uri.port or 443, 'users': [user]}]},
                           'streamSettings': stream}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path)
    parser.add_argument('--xray', default='xray')
    parser.add_argument('--expected-exit-ip', default='87.106.74.217')
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = client_config(args.profile.read_text(), port)
    with tempfile.TemporaryDirectory(prefix='hm-probe-') as temp:
        path = Path(temp) / 'client.json'
        path.write_text(json.dumps(config))
        os.chmod(path, 0o600)
        with (Path(temp) / 'xray.log').open('w') as log:
            proc = subprocess.Popen([args.xray, 'run', '-c', str(path)], stdout=log, stderr=log)
            try:
                for _ in range(50):
                    if proc.poll() is not None:
                        raise SystemExit('Xray could not start; verify client version and profile support')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                checks = [('exit-ip', 'https://api.ipify.org', '200', True),
                          ('https', 'https://www.google.com/generate_204', '204', False),
                          ('download', 'https://speed.cloudflare.com/__down?bytes=1000000', '200', False)]
                report = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': []}
                passed = True
                for name, url, expected, keep_body in checks:
                    cmd = ['curl', '--silent', '--show-error', '--socks5-hostname', f'127.0.0.1:{port}',
                           '--connect-timeout', '8', '--max-time', '25', '-w', '\n%{http_code} %{size_download} %{time_total}\n']
                    if not keep_body:
                        cmd += ['-o', os.devnull]
                    result = subprocess.run(cmd + [url], capture_output=True, text=True)
                    lines = result.stdout.strip().splitlines()
                    status = lines[-1].split()[0] if lines else '000'
                    ok = result.returncode == 0 and status == expected
                    if keep_body and ok:
                        ok = lines[0] == args.expected_exit_ip
                    item = {'name': name, 'passed': ok, 'metrics': lines[-1] if lines else 'no response'}
                    if keep_body and ok:
                        item['exit_ip'] = lines[0]
                    if not ok:
                        item['error'] = result.stderr.strip()
                    report['checks'].append(item)
                    passed = passed and ok
                print(json.dumps(report, indent=2))
                return 0 if passed else 1
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__ == '__main__':
    raise SystemExit(main())
