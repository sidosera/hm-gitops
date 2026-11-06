"""Gateway destination isolation; native cryptography/export tests live with hmctl."""
import ipaddress
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class GatewayFinalDestinationTests(unittest.TestCase):
    def test_resolved_private_destinations_are_blocked_by_both_egress_paths(self):
        for zone in ('uk-ldn-1', 'ru-spb-1'):
            config = json.loads((ROOT / 'services/gateway/overlays' / zone / 'config.json').read_text())
            for outbound in config['outbounds']:
                if outbound['protocol'] != 'freedom':
                    continue
                rules = outbound['settings']['finalRules']
                for address in ('127.0.0.1','169.254.169.254','10.43.0.1','172.16.1.1','192.168.1.1','::1','fd00::1','fe80::1'):
                    match = next(rule for rule in rules if any(ipaddress.ip_address(address) in ipaddress.ip_network(net) for net in rule.get('ip', [])))
                    self.assertEqual(match['action'], 'block')
                    self.assertNotIn('port', match)
                    self.assertNotIn('network', match)
