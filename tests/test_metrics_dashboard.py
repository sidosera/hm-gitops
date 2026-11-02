import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dashboard', Path(__file__).resolve().parents[1] / 'conf/metrics-dashboard/app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class DashboardTests(unittest.TestCase):
    def test_kubernetes_units(self):
        self.assertEqual(app.quantity('200m'), .2)
        self.assertEqual(app.quantity('100000000n'), .1)
        self.assertEqual(app.quantity('512Mi'), 536870912)
        self.assertEqual(app.quantity('2097152Ki'), 2147483648)
        with self.assertRaises(ValueError):
            app.quantity('not a metric')

    def test_rates_do_not_spike_after_restart(self):
        self.assertEqual(app.delta_rate(150, 100, 10), 5)
        self.assertIsNone(app.delta_rate(1, 100, 10))
        self.assertIsNone(app.delta_rate(100, None, 10))

    def test_online_count_deduplicates_profiles_without_exposing_ips(self):
        response = {'users': [
            {'email': 'primary@local', 'ips': [{'ip': '192.0.2.10'}, {'ip': '2001:db8::1'}]},
            {'email': 'backup@local', 'ips': [{'ip': '192.0.2.10'}]}]}
        with patch.object(app.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response))):
            result = app.online_stats()
        self.assertEqual(result['clients'], 2)
        self.assertEqual(result['active_profiles'], 2)
        self.assertNotIn('192.0.2.10', json.dumps(result))
        self.assertNotIn('2001:db8', json.dumps(result))

    def test_empty_online_result_is_zero(self):
        with patch.object(app.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}')):
            self.assertEqual(app.online_stats()['clients'], 0)

    def test_failed_sources_are_unknown_not_false_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            store = app.Store(Path(directory))
            with patch.object(app, 'kube', side_effect=OSError), patch.object(app, 'get_json', side_effect=OSError), patch.object(app, 'online_stats', side_effect=OSError):
                app.collect(store)
            sample = store.snapshot()
            self.assertEqual(len(sample['errors']), 7)
            self.assertNotIn('clients', sample['proxy'])
            self.assertNotIn('memory_percent', sample['host'])
            self.assertEqual(len(store.history(1)), 1)
            store.db.close()

    def test_history_retention_and_rate_calculation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = app.Store(Path(directory))
            now = time.time()
            def sample(t):
                return {'time': t, 'host': {'cpu_percent': 2}, 'proxy': {'clients': 1}, 'rates': {}}
            store.save(sample(now - 8 * 86400), {'proxy_upload': 500})
            store.last_cleanup = 0
            store.save(sample(now), {'proxy_upload': 100})
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM samples').fetchone()[0], 1)
            self.assertIsNone(store.snapshot()['rates']['proxy_upload'])
            store.db.close()

    def test_authentication_fails_closed(self):
        handler = app.Handler.__new__(app.Handler)
        handler.headers = {}
        self.assertFalse(handler.authorized())
        handler.headers = {'Authorization': 'Basic invalid!'}
        self.assertFalse(handler.authorized())
        with tempfile.TemporaryDirectory() as directory:
            f = Path(directory) / 'auth.json'
            f.write_text(json.dumps({'credential_sha256': app.hashlib.sha256(b'admin:correct').hexdigest()}))
            with patch.object(app, 'AUTH_FILE', f):
                handler.headers = {'Authorization': 'Basic ' + base64.b64encode(b'admin:correct').decode()}
                self.assertTrue(handler.authorized())
                handler.headers = {'Authorization': 'Basic ' + base64.b64encode(b'admin:wrong').decode()}
                self.assertFalse(handler.authorized())


if __name__ == '__main__':
    unittest.main()
