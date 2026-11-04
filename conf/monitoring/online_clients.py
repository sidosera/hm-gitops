"""Feed aggregate Xray online gauges to node-exporter's textfile collector."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time

OUTPUT = Path('/textfile/xray_online.prom')
CLI = '/tools/xray'
SERVER = 'node-xray-stats.hm.svc.cluster.local:10085'


def collect():
    response = subprocess.run([CLI, 'api', 'statsonlineiplist',
                               '--server=' + SERVER,
                               '-timeout', '3', '-all'], check=True, capture_output=True,
                              text=True, timeout=5)
    clients = set()
    profiles = {}
    for user in json.loads(response.stdout).get('users', []):
        # Relay/probe sessions are infrastructure, not end-user clients.
        if user.get('email') in {'spb-relay@local', 'probe-e2e@local'}:
            continue
        addresses = set()
        for item in user.get('ips', []):
            try:
                addresses.add(str(ipaddress.ip_address(item['ip'])))
            except (ValueError, KeyError):
                continue
        clients.update(addresses)
        # Only known, non-identifying profile names become labels.
        profile = {'primary@local': 'primary', 'backup@local': 'backup'}.get(user.get('email'))
        if profile:
            profiles[profile] = len(addresses)
    lines = ['# HELP xray_active_client_ips Distinct active source IPs; ingress masking makes this a lower bound.',
             '# TYPE xray_active_client_ips gauge', f'xray_active_client_ips {len(clients)}',
             '# HELP xray_active_profile_clients Active source IPs per credential profile.',
             '# TYPE xray_active_profile_clients gauge']
    for profile in ('primary', 'backup'):
        lines.append(f'xray_active_profile_clients{{profile="{profile}"}} {profiles.get(profile, 0)}')
    return lines


def main():
    while True:
        try:
            lines = collect()
            success = 1
        except Exception as error:
            print('Online stats unavailable: ' + type(error).__name__, flush=True)
            lines, success = [], 0
        lines += ['# HELP xray_online_scrape_success Whether the online API request succeeded.',
                  '# TYPE xray_online_scrape_success gauge', f'xray_online_scrape_success {success}']
        temp = OUTPUT.with_suffix('.tmp')
        temp.write_text('\n'.join(lines) + '\n')
        os.replace(temp, OUTPUT)
        time.sleep(30)


if __name__ == '__main__':
    main()
