import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
class RoutingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'services/gateway/overlays/ru-spb-1/config.json').read_text())

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

    def test_no_direct_failover_or_dns_resolution_for_unknown_destinations(self):
        b = self.config['routing']['balancers'][0]
        self.assertEqual(set(b['selector']), {'uk-primary', 'uk-backup'})
        self.assertEqual(b['fallbackTag'], 'uk-backup')
        self.assertEqual(self.config['routing']['domainStrategy'], 'AsIs')
        self.assertEqual(self.config['routing']['rules'][-1]['balancerTag'], 'uk-egress')
        self.assertEqual(self.config['routing']['rules'][-1]['network'], 'tcp,udp')

    def test_diagnostic_socks_are_loopback_and_stats_pod_bound(self):
        inbounds = self.config['inbounds']
        self.assertTrue(all(x['listen'] == '127.0.0.1' for x in inbounds if x['protocol'] == 'socks'))
        self.assertEqual([x['tag'] for x in inbounds if x['listen'] != '127.0.0.1'], ['api-in'])
        self.assertEqual(self.config['api']['services'], ['StatsService'])

    def test_direct_policy_uses_only_explicit_domain_suffixes(self):
        rule = next(x for x in self.config['routing']['rules'] if x.get('outboundTag') == 'ru-direct' and 'domain' in x)
        self.assertTrue(rule['domain'])
        self.assertTrue(all(x.startswith('domain:') and '*' not in x and x != 'domain:ru' for x in rule['domain']))

if __name__ == '__main__': unittest.main()
