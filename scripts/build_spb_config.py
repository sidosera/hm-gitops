#!/usr/bin/env python3
"""Build SPB Xray config from private profiles; never print credentials."""
import argparse
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
from check_proxy import client_config


def build(primary, backup, identity, policy):
    domains = policy['direct_domains']
    if not domains or any(not re.fullmatch(r'[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}', d) for d in domains):
        raise ValueError('Direct domains must be explicit DNS suffixes, without wildcards')
    blocked = [str(ipaddress.ip_network(n)) for n in policy['blocked_networks']]
    outbounds = []
    for tag, profile in [('uk-primary', primary), ('uk-backup', backup)]:
        outbound = copy.deepcopy(client_config(profile, 0)['outbounds'][0])
        outbound['tag'] = tag
        outbounds.append(outbound)
    # The first outbound also supplies Xray's unmatched fallback. Never put freedom first.
    outbounds += [{'tag': 'ru-direct', 'protocol': 'freedom', 'settings': {}},
                  {'tag': 'blocked', 'protocol': 'blackhole', 'settings': {}}]
    inbound = {'tag': 'gateway', 'listen': '0.0.0.0', 'port': 14443, 'protocol': 'vless',
               'settings': {'clients': [{'id': identity['client_id'], 'email': 'primary@local',
                                        'flow': 'xtls-rprx-vision'}], 'decryption': 'none'},
               'streamSettings': {'network': 'raw', 'security': 'reality', 'realitySettings': {
                   'show': False, 'target': policy['camouflage_server'] + ':443',
                   'serverNames': [policy['camouflage_server']],
                   'privateKey': identity['private_key'], 'shortIds': [identity['short_id']]}},
               'sniffing': {'enabled': True, 'destOverride': ['http', 'tls', 'quic'], 'routeOnly': True}}
    inbounds = [inbound]
    for tag, port in [('probe-primary', 10811), ('probe-backup', 10812),
                      ('probe-split', 10813), ('probe-direct', 10814)]:
        inbounds.append({'tag': tag, 'listen': '127.0.0.1', 'port': port, 'protocol': 'socks',
                         'settings': {'auth': 'noauth', 'udp': False}})
    inbounds.append({'tag': 'api-in', 'listen': '0.0.0.0', 'port': 10085,
                     'protocol': 'dokodemo-door', 'settings': {'address': '127.0.0.1'}})
    rules = [{'type': 'field', 'inboundTag': ['api-in'], 'outboundTag': 'api'},
             {'type': 'field', 'ip': blocked, 'outboundTag': 'blocked'},
             {'type': 'field', 'domain': ['domain:localhost'], 'outboundTag': 'blocked'},
             {'type': 'field', 'inboundTag': ['probe-primary'], 'outboundTag': 'uk-primary'},
             {'type': 'field', 'inboundTag': ['probe-backup'], 'outboundTag': 'uk-backup'},
             {'type': 'field', 'inboundTag': ['probe-direct'], 'outboundTag': 'ru-direct'},
             {'type': 'field', 'domain': ['domain:' + d for d in domains], 'outboundTag': 'ru-direct'},
             {'type': 'field', 'network': 'tcp,udp', 'balancerTag': 'uk-egress'}]
    return {'log': {'loglevel': 'warning'}, 'inbounds': inbounds, 'outbounds': outbounds,
            'routing': {'domainStrategy': 'AsIs', 'rules': rules,
                        'balancers': [{'tag': 'uk-egress', 'selector': ['uk-primary', 'uk-backup'],
                                       'fallbackTag': 'uk-backup', 'strategy': {'type': 'leastPing'}}]},
            'observatory': {'subjectSelector': ['uk-primary', 'uk-backup'],
                            'probeUrl': 'https://www.google.com/generate_204',
                            'probeInterval': '30s', 'enableConcurrency': True},
            'api': {'tag': 'api', 'services': ['StatsService']}, 'stats': {},
            'policy': {'levels': {'0': {'statsUserOnline': True}},
                       'system': {'statsInboundUplink': True, 'statsInboundDownlink': True,
                                  'statsOutboundUplink': True, 'statsOutboundDownlink': True}}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['primary-profile', 'backup-profile', 'identity', 'policy', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    config = build(a.primary_profile.read_text(), a.backup_profile.read_text(),
                   json.loads(a.identity.read_text()), json.loads(a.policy.read_text()))
    fd = os.open(a.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')


if __name__ == '__main__':
    main()
