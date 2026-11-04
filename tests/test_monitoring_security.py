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
        self.assertEqual(roles[0]['rules'][0]['resources'],['nodes/metrics'])
        self.assertEqual(roles[0]['rules'][0]['verbs'],['get'])
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

    def test_public_management_addresses_cannot_be_reached_through_vpn(self):
        inventory=yaml.safe_load((ROOT/'cluster/clusters/hm-cluster/hosts.yml').read_text())
        addresses=[]
        for ip,node in inventory['vm_node_settings'].items():
            addresses.append(ipaddress.ip_address(ip))
            if node.get('ipv6'):addresses.append(ipaddress.ip_address(node['ipv6'].split('/')[0]))
        for zone in ['uk-ldn-1','ru-spb-1']:
            config=json.loads((ROOT/'services/gateway/overlays'/zone/'config.json').read_text())
            self.assertNotIn('metrics',config)
            for outbound in config['outbounds']:
                if outbound['protocol']!='freedom':continue
                for address in addresses:
                    rule=next(v for v in outbound['settings']['finalRules'] if any(address in ipaddress.ip_network(net) for net in v.get('ip',[])))
                    self.assertEqual(rule['action'],'block')
                    def blocks(port):
                        return any(int(part.split('-')[0]) <= port <= int(part.split('-')[-1]) for part in rule['port'].split(','))
                    for port in [22,2379,2380,3000,6443,6444,9090,9100,9550,10085,10250,11111,32142]:
                        self.assertTrue(blocks(port),(zone,address,port))
                    self.assertFalse(blocks(80))
                    self.assertFalse(blocks(443))

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
            # Runtime containers; legacy website build init containers are a documented separate finding.
            for container in spec['containers']:
                security=container['securityContext']
                self.assertTrue(security['runAsNonRoot'])
                self.assertTrue(security['readOnlyRootFilesystem'])
                self.assertFalse(security['allowPrivilegeEscalation'])
                self.assertEqual(security['capabilities']['drop'],['ALL'])
                self.assertIn('@sha256:',container['image'])

    def test_archived_data_cannot_be_pruned_by_cleanup(self):
        archive=next(v for v in self.resources if v['kind']=='PersistentVolumeClaim' and v['metadata']['name']=='metrics-dashboard-data')
        self.assertEqual(archive['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'],'disabled')
