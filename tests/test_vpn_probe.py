"""Boundaries for independent, authenticated public-listener probes."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('provision_probe', ROOT / 'scripts/provision_vpn_probe.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class VPNProbeTests(unittest.TestCase):
    def test_required_sites_and_cadence_are_configured_in_probe_service(self):
        config = json.loads((ROOT / 'services/vpn-probe/base/targets.json').read_text())
        self.assertEqual({s['url'] for s in config['websites']}, {'https://myanimelist.net/', 'https://youtube.com/',
                         'https://facebook.com/', 'https://instagram.com/', 'https://x.com/'})
        self.assertEqual(config['interval_seconds'], 60)
        self.assertEqual(config['timeout_seconds'], 20)
        self.assertEqual(config['socks_proxy'], ['127.0.0.1', 10800])

    def test_only_spb_runs_probe_service_and_prometheus_only_scrapes_metrics(self):
        config = yaml.safe_load((ROOT / 'conf/monitoring/prometheus.yml').read_text())
        job = next(j for j in config['scrape_configs'] if j['job_name'] == 'vpn-client-probes')
        self.assertEqual(job['metrics_path'], '/metrics')
        self.assertTrue(job['honor_timestamps'])
        self.assertEqual(len(job['static_configs']), 1)
        self.assertEqual(job['static_configs'][0]['labels']['source_zone'], 'ru-spb-1')
        self.assertNotIn('relabel_configs', job)
        resources = yaml.safe_load((ROOT / 'cluster/clusters/hm-cluster/kustomization.yaml').read_text())['resources']
        self.assertNotIn('../../../services/vpn-probe/overlays/uk-ldn-1', resources)
        self.assertIn('../../../services/vpn-probe/overlays/ru-spb-1', resources)

    def test_regional_deployments_share_runtime_and_have_no_public_metrics_ports(self):
        images = []
        for region in ['uk-ldn-1', 'ru-spb-1']:
            output = subprocess.check_output(['kubectl', 'kustomize', str(ROOT / 'services/vpn-probe/overlays' / region)])
            docs = list(yaml.safe_load_all(output))
            deployment = next(d for d in docs if d['kind'] == 'Deployment')
            pod = deployment['spec']['template']['spec']
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(pod['nodeSelector']['topology.kubernetes.io/zone'], region)
            self.assertEqual(len(pod['containers']), 2)
            if region == 'ru-spb-1':
                self.assertEqual(pod['dnsPolicy'], 'None')
                self.assertEqual(pod['dnsConfig']['nameservers'], ['141.8.194.254', '141.8.197.254'])
            # SOCKS stays on loopback, so kubelet TCP probes to the pod IP cannot work.
            self.assertNotIn('tcpSocket', pod['containers'][0].get('readinessProbe', {}))
            for container in pod['containers']:
                self.assertEqual(container['resources']['limits']['memory'], '40Mi')
                self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
                self.assertTrue(all('hostPort' not in port for port in container.get('ports', [])))
            service = next(d for d in docs if d['kind'] == 'Service')
            self.assertNotIn(service['spec'].get('type', 'ClusterIP'), ['LoadBalancer', 'NodePort'])
            policy = next(d for d in docs if d['kind'] == 'NetworkPolicy')
            self.assertEqual(policy['spec']['ingress'][0]['from'][0]['podSelector']['matchLabels'], {'app.kubernetes.io/name': 'prometheus'})
            self.assertEqual(service['spec']['selector'], deployment['spec']['selector']['matchLabels'])
            images.append([c['image'] for c in pod['containers']])
        self.assertEqual(images[0], images[1])

    def test_account_update_is_additive_and_idempotent(self):
        old = {'inbounds': [{'tag': 'gateway', 'settings': {'clients': [{'id': 'existing', 'email': 'person@local'}]},
                            'streamSettings': {'security': 'reality'}}]}
        original = copy.deepcopy(old)
        updated, ident = provision.add_account(old, 'test-only-probe-id')
        repeated, other = provision.add_account(updated, 'replacement-must-not-be-used')
        self.assertEqual(old, original)
        self.assertEqual(updated['inbounds'][0]['settings']['clients'][0], old['inbounds'][0]['settings']['clients'][0])
        self.assertEqual(updated, repeated)
        self.assertEqual(ident, other)

    def test_client_connects_to_public_443_and_cannot_fall_back_to_direct(self):
        inbound = {'streamSettings': {'realitySettings': {'privateKey': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
                    'serverNames': ['example.org'], 'shortIds': ['0011223344556677']}}}
        client = provision.client_config(inbound, 'test-only-probe-id', '203.0.113.10')
        self.assertEqual(client['inbounds'][0]['listen'], '127.0.0.1')
        self.assertEqual(client['outbounds'][0]['protocol'], 'blackhole')
        self.assertNotIn('freedom', {o['protocol'] for o in client['outbounds']})
        remote = client['outbounds'][1]['settings']['vnext'][0]
        self.assertEqual((remote['address'], remote['port']), ('203.0.113.10', 443))
        self.assertEqual(client['routing']['rules'][0]['outboundTag'], 'public-vpn')

    def test_vpn_matrix_uses_one_minute_minimum_and_unknown_telemetry(self):
        d = json.loads((ROOT / 'conf/monitoring/vpn.json').read_text())
        main = next(p for p in d['panels'] if p['id'] == 30)
        expr = main['targets'][0]['expr']
        for selector in ['${source_zone:regex}', '${site:regex}', 'min_over_time(', '[1m]', 'probe_last_run_timestamp_seconds', 'time() - 60']:
            self.assertIn(selector, expr)
        self.assertEqual(main['type'], 'table')
        self.assertTrue(main['targets'][0]['instant'])
        self.assertEqual(main['transformations'][0]['options'], {'columnField': 'access', 'rowField': 'site', 'valueField': 'Value', 'emptyValue': 'null'})
        self.assertNotIn('target_zone', {v['name'] for v in d['templating']['list']})
        self.assertFalse(any(p['id'] == 20 for p in d['panels']))

    def test_observer_outage_keeps_zone_and_target_rows_visible(self):
        d = json.loads((ROOT / 'conf/monitoring/vpn.json').read_text())
        variables = {v['name']: v for v in d['templating']['list']}
        zone_query = variables['source_zone']['query']['query']
        self.assertIn('up{', zone_query)
        self.assertNotIn('probe_target_info', zone_query)
        self.assertIn('max_over_time(', variables['site']['query']['query'])
        matrix = next(p for p in d['panels'] if p['id'] == 30)
        self.assertIn('max_over_time(probe_target_info{', matrix['targets'][0]['expr'])
        self.assertIn('[24h]', matrix['targets'][0]['expr'])
        status = next(p for p in d['panels'] if p['id'] == 4)
        self.assertIn('up{', status['targets'][0]['expr'])
        self.assertNotIn('probe_success', status['targets'][0]['expr'])


if __name__ == '__main__':
    unittest.main()
