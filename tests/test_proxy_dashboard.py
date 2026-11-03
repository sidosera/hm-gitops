"""Proxy observability must stay service-scoped, private and topology-independent."""
import json
from pathlib import Path
import subprocess
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]

def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))

class ProxyDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = json.loads((ROOT / 'conf/monitoring/proxy.json').read_text())
        cls.resources = list(yaml.safe_load_all(subprocess.check_output(['kubectl','kustomize',str(ROOT/'cluster/clusters/hm-cluster')],text=True)))

    def test_queries_use_service_zone_and_separate_measurement_zones(self):
        for panel in panels(self.dashboard['panels']):
            for query in panel.get('targets', []):
                expr = query['expr']
                self.assertIn('${cluster:regex}',expr)
                self.assertIn('${zone:regex}',expr)
                self.assertIn('service="gateway"',expr)
                self.assertNotIn('node_',expr.replace('node_textfile_mtime_seconds',''))
                self.assertNotIn('or vector(0)',expr)
                self.assertNotIn('traefik_',expr)
                self.assertIn('zone',query.get('legendFormat','') or expr)
        for value in ('87.106.74.217','212.116.115.29','spb-01','uk-ldn-1','ru-spb-1'):
            self.assertNotIn(value,json.dumps(self.dashboard))

    def test_listener_filters_only_traffic_and_excludes_diagnostics(self):
        variables={v['name']:v for v in self.dashboard['templating']['list']}
        self.assertEqual(set(variables),{'cluster','zone','listener'})
        self.assertIn('${zone:regex}',variables['listener']['query']['query'])
        for panel in self.dashboard['panels']:
            for query in panel.get('targets',[]):
                if 'dimension="inbound"' in query['expr']:
                    self.assertIn('${listener:regex}',query['expr'])
                    self.assertIn('target!~"api-in|stats-api|probe-.*"',query['expr'])
        self.assertFalse(any('HTTP' in p['title'] or 'latency' in p['title'].lower() for p in panels(self.dashboard['panels'])))

    def test_collectors_share_hardened_base_and_run_only_on_control_plane(self):
        exporters=[d for d in self.resources if d['kind']=='Deployment' and d['metadata'].get('labels',{}).get('app.kubernetes.io/name')=='xray-exporter']
        self.assertEqual(len(exporters),2)
        images=set()
        for deployment in exporters:
            pod=deployment['spec']['template']; spec=pod['spec']; zone=pod['metadata']['labels']['hm.gitops/observed-zone']
            self.assertEqual(spec['nodeSelector'],{'hm.gitops/control-plane':'true'})
            self.assertFalse(spec['automountServiceAccountToken'])
            selector=deployment['spec']['selector']['matchLabels']
            self.assertEqual(selector['hm.gitops/observed-zone'],zone)
            c=spec['containers'][0];images.add(c['image'])
            self.assertIn('@sha256:',c['image']);self.assertIn('--log-path=',c['args'])
            self.assertNotIn('--user-traffic-metrics',c['args'])
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertFalse(any('secret' in v for v in spec.get('volumes',[])))
            service=next(d for d in self.resources if d['kind']=='Service' and d['metadata']['name']==deployment['metadata']['name'])
            self.assertEqual(service['spec']['selector'],selector)
            self.assertNotIn('nodePort',service['spec']['ports'][0])
        self.assertEqual(len(images),1)

    def test_every_dashboard_is_provisioned_and_linked(self):
        dashboards=next(d for d in self.resources if d['kind']=='ConfigMap' and d['metadata']['name'].startswith('grafana-dashboards-'))
        self.assertIn('proxy.json',dashboards['data'])
        for name in ('vpn','overview','inventory'):
            doc=json.loads((ROOT/'conf/monitoring'/f'{name}.json').read_text())
            self.assertTrue(any('/hm-proxy/' in link.get('url','') for link in doc['links']))
