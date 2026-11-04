"""Least-privilege monitoring and management isolation contracts."""
import ipaddress
import json
from pathlib import Path
import subprocess
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]

class MonitoringSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources=list(yaml.safe_load_all(subprocess.check_output(['kubectl','kustomize',str(ROOT/'cluster/clusters/hm-cluster')],text=True)))

    def test_retired_collectors_and_permissions_are_absent(self):
        for obj in self.resources:
            self.assertNotIn('kube-state-metrics',obj['metadata']['name'])
        roles=[v for v in self.resources if v['kind'] in ('Role','ClusterRole')]
        self.assertEqual(len(roles),1)
        self.assertEqual(roles[0]['rules'][0]['resources'],['nodes'])
        self.assertEqual(roles[0]['rules'][0]['verbs'],['get','list','watch'])
        self.assertNotIn('nodes/proxy', str(roles))
        prom=yaml.safe_load((ROOT/'conf/monitoring/prometheus.yml').read_text())
        self.assertNotIn('kube-state-metrics',{v['job_name'] for v in prom['scrape_configs']})
        self.assertNotIn('service-rules',str(prom))

    def test_stats_api_is_reserved_to_exporters_and_native_http_metrics_are_removed(self):
        for obj in self.resources:
            if obj['kind']=='NetworkPolicy' and obj['metadata']['name'] in ('proxy-service-ingress','spb-gateway-ingress'):
                stats=next(v for v in obj['spec']['ingress'] if any(p['port']==10085 for p in v['ports']))
                self.assertEqual(stats['ports'],[{'protocol':'TCP','port':10085}])
                self.assertEqual(stats['from'],[{'podSelector':{'matchLabels':{'app.kubernetes.io/name':'xray-exporter'}}}])
            if obj['kind']=='Service':
                self.assertNotIn(11111,[v['port'] for v in obj['spec'].get('ports',[])])

    def test_host_management_is_not_an_application_address_dependency(self):
        inventory=yaml.safe_load((ROOT/'cluster/clusters/hm-cluster/hosts.yml').read_text())
        for zone in ['uk-ldn-1','ru-spb-1']:
            text=(ROOT/'services/gateway/overlays'/zone/'config.json').read_text()
            for address,machine in inventory['vm_node_settings'].items():
                self.assertNotIn(address, text)
                self.assertNotIn(machine['ipv6'], text)
        guard=(ROOT/'vm/roles/vm/templates/nodeport-guard.sh.j2').read_text()
        self.assertIn('--dports 6443,9100,10250 -j DROP', guard)
        self.assertIn('--dports 22,6443,9100,10250 -m addrtype --dst-type LOCAL -j DROP', guard)

    def test_all_git_secrets_are_encrypted_and_runtime_is_hardened(self):
        for obj in self.resources:
            if obj['kind']=='Secret':
                self.assertIn('sops',obj)
                for field in ['data','stringData']:
                    self.assertTrue(all(v.startswith('ENC[') for v in obj.get(field,{}).values()))
            if obj['kind'] not in ('Deployment','DaemonSet'):continue
            spec=obj['spec']['template']['spec']
            if obj['metadata']['name']!='prometheus':self.assertFalse(spec['automountServiceAccountToken'])
            self.assertNotIn('hostPID',spec)
            for container in spec['containers'] + spec.get('initContainers', []):
                security=container['securityContext']
                self.assertTrue(security['runAsNonRoot'])
                self.assertTrue(security['readOnlyRootFilesystem'])
                self.assertFalse(security['allowPrivilegeEscalation'])
                self.assertEqual(security['capabilities']['drop'],['ALL'])
                self.assertIn('@sha256:',container['image'])

    def test_public_routes_preserve_observability_and_both_vpn_transports(self):
        routes={v['metadata']['name']:v for v in self.resources if v['kind'] in ('IngressRoute','IngressRouteTCP')}
        self.assertIn('/status/', routes['grafana']['spec']['routes'][0]['match'])
        self.assertIn('/assets/', routes['proxy-service-https']['spec']['routes'][0]['match'])
        self.assertTrue(routes['proxy-service']['spec']['tls']['passthrough'])
        self.assertFalse(any(v['kind']=='Ingress' for v in self.resources))
        self.assertFalse(any(v['metadata']['name'].startswith('www-service') for v in self.resources))

    def test_archived_data_cannot_be_pruned_by_cleanup(self):
        archive=next(v for v in self.resources if v['kind']=='PersistentVolumeClaim' and v['metadata']['name']=='metrics-dashboard-data')
        self.assertEqual(archive['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'],'disabled')
