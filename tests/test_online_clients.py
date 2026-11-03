import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('online', Path(__file__).resolve().parents[1] / 'conf/monitoring/online_clients.py')
online = importlib.util.module_from_spec(spec)
spec.loader.exec_module(online)


class OnlineClientTests(unittest.TestCase):
    def collect(self, users):
        response = subprocess.CompletedProcess([], 0, json.dumps({'users': users}))
        with patch.object(online.subprocess, 'run', return_value=response):
            return '\n'.join(online.collect())

    def test_deduplicates_profiles_without_exporting_addresses(self):
        text = self.collect([{'email': 'primary@local', 'ips': [{'ip': '192.0.2.1'}]},
                             {'email': 'backup@local', 'ips': [{'ip': '192.0.2.1'}, {'ip': '2001:db8::1'}]}])
        self.assertIn('xray_active_client_ips 2', text)
        self.assertIn('xray_active_profile_clients{profile="backup"} 2', text)
        self.assertNotIn('192.0.2.1', text)
        self.assertNotIn('2001:db8', text)

    def test_relay_and_probe_sessions_are_not_counted_as_end_users(self):
        text = self.collect([{'email': 'spb-relay@local', 'ips': [{'ip': '192.0.2.1'}]},
                             {'email': 'probe-e2e@local', 'ips': [{'ip': '192.0.2.2'}]}])
        self.assertIn('xray_active_client_ips 0', text)

    def test_empty_sessions_are_zero(self):
        self.assertIn('xray_active_client_ips 0', self.collect([]))

    def test_unknown_profile_does_not_create_label(self):
        text = self.collect([{'email': 'private-user@example.test', 'ips': [{'ip': '192.0.2.1'}]}])
        self.assertIn('xray_active_client_ips 1', text)
        self.assertNotIn('private-user', text)

    def test_failed_api_propagates_instead_of_returning_zero(self):
        with patch.object(online.subprocess, 'run', side_effect=subprocess.TimeoutExpired('xray', 5)):
            with self.assertRaises(subprocess.TimeoutExpired):
                online.collect()


if __name__ == '__main__':
    unittest.main()
