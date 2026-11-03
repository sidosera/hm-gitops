"""Check deployable Gateway overlays at Kubernetes and public-config boundaries."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class PublicGatewayConfigTests(unittest.TestCase):
    def test_public_configs_do_not_contain_authenticated_connections(self):
        def check(value):
            if isinstance(value, dict):
                self.assertNotEqual(value.get('protocol'), 'vless')
                for key, child in value.items():
                    self.assertNotIn(key, {'privateKey', 'clients', 'password', 'shortIds'})
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        for region in ['uk-ldn-1', 'ru-spb-1']:
            with self.subTest(region=region):
                check(json.loads((ROOT / 'services/gateway/overlays' / region / 'config.json').read_text()))

    def test_credentials_remain_sops_encrypted(self):
        secret = yaml.safe_load((ROOT / 'conf/xray/credentials-secret.sops.yaml').read_text())
        self.assertIn('sops', secret)
        self.assertEqual(secret['metadata']['name'], 'xray-credentials')
        self.assertEqual(set(secret['data']), {zone + '.' + role + '.json'
                         for zone in ['uk-ldn-1', 'ru-spb-1'] for role in ['gateway', 'probe']})
        self.assertTrue(all(value.startswith('ENC[') for value in secret['data'].values()))



@unittest.skipUnless(shutil.which('kubectl'), 'kubectl is required to render Kustomize overlays')
class GatewayOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = subprocess.check_output(['kubectl', 'kustomize', str(ROOT / 'cluster/clusters/hm-cluster')], text=True)
        cls.resources = {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(output)}

    def test_regional_resources_preserve_selectors_and_shared_runtime(self):
        images = []
        for region, name in [('uk-ldn-1', 'proxy-service'), ('ru-spb-1', 'spb-gateway')]:
            with self.subTest(region=region):
                deployment = self.resources['Deployment', name]
                pod = deployment['spec']['template']
                selector = {'app.kubernetes.io/name': name}
                self.assertEqual(deployment['spec']['selector']['matchLabels'], selector)
                self.assertEqual(self.resources['Service', name]['spec']['selector'], selector)
                self.assertEqual(pod['spec']['nodeSelector'], {'topology.kubernetes.io/zone': region})
                self.assertEqual(pod['metadata']['labels']['hm.gitops/xray'], 'true')
                xray = pod['spec']['containers'][0]
                self.assertEqual(xray['args'], ['run', '-confdir', '/etc/xray'])
                self.assertFalse(xray['securityContext']['allowPrivilegeEscalation'])
                names = [p['name'] for p in xray['ports']]
                self.assertEqual(len(names), len(set(names)))
                images.append(xray['image'])
                volume = next(v for v in pod['spec']['volumes'] if v['name'] == 'config')['projected']
                self.assertEqual(volume['sources'][0]['secret']['name'], 'xray-credentials')
                self.assertEqual(volume['sources'][0]['secret']['items'][0]['key'], region + '.gateway.json')
                paths = [item['path'] for source in volume['sources'] for obj in source.values() for item in obj['items']]
                self.assertEqual(paths, ['00-credentials.json', '10-config-tail.json'])
        self.assertEqual(images[0], images[1])
        self.assertIn('@sha256:', images[0])

    def test_source_selectors_are_not_rewritten_as_gateway_selectors(self):
        for name, expected in [('proxy-service', {'traefik', 'xray-exporter', 'node-exporter'}),
                               ('spb-gateway', {'prometheus', 'node-exporter'})]:
            policy = self.resources['NetworkPolicy', name + '-ingress']['spec']
            self.assertEqual(policy['podSelector']['matchLabels'], {'app.kubernetes.io/name': name})
            sources = {source['podSelector']['matchLabels']['app.kubernetes.io/name']
                       for rule in policy['ingress'] for source in rule.get('from', []) if 'podSelector' in source}
            self.assertEqual(sources, expected)

    def test_host_port_and_pod_ingress_stay_aligned(self):
        pod = self.resources['Deployment', 'spb-gateway']['spec']['template']['spec']
        reality = next(p for p in pod['containers'][0]['ports'] if p['name'] == 'reality')
        self.assertEqual((reality['hostPort'], reality['containerPort']), (443, 14443))
        policy = self.resources['NetworkPolicy', 'spb-gateway-ingress']['spec']
        public = [port['port'] for rule in policy['ingress'] if 'from' not in rule for port in rule['ports']]
        self.assertEqual(public, [reality['containerPort']])
        self.assertEqual(pod['containers'][0]['resources']['limits']['memory'], '80Mi')


if __name__ == '__main__':
    unittest.main()
