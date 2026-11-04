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

    def test_traffic_is_client_facing_and_no_host_or_session_metrics(self):
        d=self.dashboard
        self.assertEqual({v['name'] for v in d['templating']['list']},{'cluster','zone','source_zone'})
        for p in d['panels'][1:]:
            for t in p['targets']:
                self.assertIn('service="gateway"',t['expr'])
                self.assertIn('${zone:regex}',t['expr'])
                self.assertIn('up{',t['expr'])
                self.assertNotIn('node_',t['expr'])
        client=d['panels'][2]['targets'][0]['expr']
        self.assertIn('dimension="user"',client)
        self.assertIn('topk(100,',client)
        self.assertIn('@ end()',client)
        self.assertNotIn('inbound',client)
        self.assertIn('dimension="outbound"',d['panels'][1]['targets'][1]['expr'])

    def test_connectivity_has_history_and_explicit_missing_data(self):
        p=self.dashboard['panels'][0]
        self.assertEqual(p['type'],'table')
        self.assertEqual([t['id'] for t in p['transformations']],['timeSeriesTable','merge','organize'])
        for t in p['targets']:
            self.assertTrue(t['range'])
            self.assertFalse(t['instant'])
            for text in ['${source_zone:regex}','min_over_time(', '[1m]', 'time() - 60', 'probe_last_run_timestamp_seconds','max_over_time(probe_target_info{','[24h]','/ 0']:
                self.assertIn(text,t['expr'])
            self.assertNotIn('${zone:regex}',t['expr'])
        for override in p['fieldConfig']['overrides']:
            cell=next(v['value'] for v in override['properties'] if v['id']=='custom.cellOptions')
            self.assertEqual(cell['type'],'sparkline')
            self.assertTrue(cell['hideValue'])
            self.assertFalse(cell['spanNulls'])

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
            self.assertIn('--user-traffic-metrics',c['args'])
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertFalse(any('secret' in v for v in spec.get('volumes',[])))
            service=next(d for d in self.resources if d['kind']=='Service' and d['metadata']['name']==deployment['metadata']['name'])
            self.assertEqual(service['spec']['selector'],selector)
            self.assertNotIn('nodePort',service['spec']['ports'][0])
        self.assertEqual(len(images),1)

    def test_only_hosts_and_proxy_are_provisioned_in_hm_folder(self):
        dashboards=next(d for d in self.resources if d['kind']=='ConfigMap' and d['metadata']['name'].startswith('grafana-dashboards-'))
        self.assertEqual(set(dashboards['data']),{'inventory.json','proxy.json'})
        provider=yaml.safe_load((ROOT/'conf/monitoring/dashboards.yaml').read_text())['providers'][0]
        self.assertEqual(provider['folder'],'HM')
        for name in ('inventory','proxy'):
            doc=json.loads((ROOT/'conf/monitoring'/f'{name}.json').read_text())
            self.assertEqual({link['title'] for link in doc['links']},{'Hosts','Proxy'})

    def test_no_connected_ip_poller_or_stats_cli_download(self):
        node=next(d for d in self.resources if d['kind']=='DaemonSet' and d['metadata']['name']=='node-exporter')['spec']['template']['spec']
        self.assertEqual([c['name'] for c in node['containers']],['node-exporter'])
        self.assertNotIn('initContainers',node)
        self.assertNotIn('textfile',str(node))
        self.assertFalse(any(d['metadata']['name']=='node-xray-stats' for d in self.resources))

    def test_user_credentials_are_relabelled_before_storage(self):
        config=yaml.safe_load((ROOT/'conf/monitoring/prometheus.yml').read_text())
        job=next(j for j in config['scrape_configs'] if j['job_name']=='xray')
        rules=job['metric_relabel_configs']
        self.assertEqual(rules[0]['action'],'drop')
        self.assertIn('spb-relay@local',rules[0]['regex'])
        self.assertIn('probe-e2e@local',rules[0]['regex'])
        self.assertEqual(rules[1]['action'],'hashmod')
        self.assertEqual(rules[2]['source_labels'],['dimension','__tmp_client_hash'])
        self.assertEqual(rules[2]['target_label'],'target')
        self.assertEqual(rules[2]['replacement'],'client-$1')
        self.assertEqual(rules[3]['action'],'labeldrop')
