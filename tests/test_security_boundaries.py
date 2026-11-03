"""Security regressions for private exports and fixed collector execution."""
import base64
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

profiles = load('profiles', 'scripts/xray_share_url.py')

class SecurityBoundaryTests(unittest.TestCase):
    def test_public_key_matches_rfc7748_vector(self):
        private = bytes.fromhex('77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a')
        expected = bytes.fromhex('8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a')
        key = base64.urlsafe_b64encode(private).rstrip(b'=').decode()
        self.assertEqual(base64.urlsafe_b64decode(profiles.derive_public_key(key)+'='), expected)

    def test_export_is_private_and_never_follows_symlinks_or_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'profile'
            profiles.write_private_profile(target, 'test-profile')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                profiles.write_private_profile(target, 'replacement')
            link = Path(directory) / 'link'
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                profiles.write_private_profile(link, 'replacement')
            self.assertEqual(target.read_text(), 'test-profile\n')

    def test_environment_cannot_redirect_collector_execution_or_output(self):
        with patch.dict(os.environ, {'XRAY_CLI':'/tmp/evil', 'METRICS_OUTPUT':'/tmp/evil-output', 'XRAY_STATS_SERVER':'attacker:123'}):
            collector = load('hardened_collector', 'conf/monitoring/online_clients.py')
            response = subprocess.CompletedProcess([], 0, '{"users":[]}')
            with patch.object(collector.subprocess, 'run', return_value=response) as run:
                collector.collect()
            self.assertEqual(run.call_args.args[0][0], '/tools/xray')
            self.assertIn('--server=node-xray-stats.hm.svc.cluster.local:10085', run.call_args.args[0])
            self.assertEqual(str(collector.OUTPUT), '/textfile/xray_online.prom')


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
