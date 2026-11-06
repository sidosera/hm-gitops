"""Boundaries for independent, authenticated public-listener probes."""
import json
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


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





if __name__ == '__main__':
    unittest.main()
