import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_spb_config import build

ROOT = Path(__file__).resolve().parents[1]
# Public, deliberately unusable fixture identity; never connects to a real VPN.
PROFILE = ('vless://00000000-0000-4000-8000-000000000001@192.0.2.1:443?'
           'type=tcp&security=reality&sni=example.com&pbk=fixture&sid=abcd&flow=xtls-rprx-vision')
IDENTITY = {'client_id': '00000000-0000-4000-8000-000000000002', 'private_key': 'fixture', 'short_id': 'abcd'}

class RoutingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / 'conf/spb-01/routing.json').read_text())
        self.config = build(PROFILE, PROFILE, IDENTITY, self.policy)

    def route(self, host, inbound='gateway'):
        import ipaddress
        for r in self.config['routing']['rules']:
            if 'inboundTag' in r and inbound not in r['inboundTag']: continue
            if 'ip' in r:
                try: address = ipaddress.ip_address(host)
                except ValueError: continue
                if not any(address in ipaddress.ip_network(n) for n in r['ip']): continue
            if 'domain' in r:
                if not any(host == d.removeprefix('domain:') or host.endswith('.' + d.removeprefix('domain:')) for d in r['domain']): continue
            return r.get('outboundTag', r.get('balancerTag'))

    def test_known_russian_domains_and_subdomains_exit_in_russia(self):
        for domain in ['yandex.ru', 'passport.yandex.ru', 'vk.com', 'gosuslugi.ru', 'www.ozon.ru']:
            self.assertEqual(self.route(domain), 'ru-direct')

    def test_everything_else_including_unknown_ru_and_suffix_spoofs_goes_upstream(self):
        for domain in ['example.com', 'unknown.ru', 'yandex.ru.attacker.com', 'evilyandex.ru', '8.8.8.8']:
            self.assertEqual(self.route(domain), 'uk-egress')

    def test_private_and_metadata_ips_cannot_escape_through_a_probe(self):
        for inbound in ['gateway', 'probe-primary', 'probe-backup', 'probe-direct', 'probe-split']:
            for host in ['127.0.0.1', '169.254.169.254', '10.42.0.1', '192.168.1.1', '::1', 'fd00::1']:
                self.assertEqual(self.route(host, inbound), 'blocked')

    def test_no_direct_failover_or_unmatched_default(self):
        self.assertEqual(self.config['outbounds'][0]['tag'], 'uk-primary')
        b = self.config['routing']['balancers'][0]
        self.assertEqual(set(b['selector']), {'uk-primary', 'uk-backup'})
        self.assertEqual(b['fallbackTag'], 'uk-backup')
        self.assertEqual(self.config['routing']['domainStrategy'], 'AsIs')

    def test_diagnostic_socks_are_loopback_and_stats_pod_bound(self):
        self.assertEqual([x['tag'] for x in self.config['inbounds'] if x['listen'] != '127.0.0.1'], ['gateway', 'api-in'])
        self.assertEqual(self.config['api']['services'], ['StatsService'])

    def test_reject_broad_wildcards_in_direct_policy(self):
        self.policy['direct_domains'] = ['*.ru']
        with self.assertRaises(ValueError): build(PROFILE, PROFILE, IDENTITY, self.policy)

if __name__ == '__main__': unittest.main()
