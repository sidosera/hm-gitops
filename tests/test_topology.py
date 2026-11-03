"""Stable physical identity and zone-based scheduling boundaries."""
from pathlib import Path
import ipaddress
import json
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = ROOT / 'cluster/clusters/hm-cluster'


class TopologyTests(unittest.TestCase):
    def test_ip_inventory_and_host_slots_are_independent(self):
        inventory = yaml.safe_load((CLUSTER / 'hosts.yml').read_text())
        self.assertEqual(inventory['hm_cluster_name'], 'hm-cluster')
        self.assertEqual(set(inventory['hm_pops']), {'uk-ldn', 'ru-spb'})
        for address, machine in inventory['vm_node_settings'].items():
            self.assertEqual(machine['name'], str(ipaddress.ip_address(address)))
            self.assertEqual(machine['host'], machine['zone'] + '/1')
            self.assertIn(machine['zone'], inventory['hm_pops'][machine['pop']]['zones'])
            self.assertIn('kubernetes_name', machine)
            self.assertNotIn('region', machine)

    def test_all_scrapes_carry_cluster_and_zone_and_real_host_identity(self):
        config = yaml.safe_load((ROOT / 'conf/monitoring/prometheus.yml').read_text())
        for job in config['scrape_configs']:
            for group in job.get('static_configs', []):
                labels = group['labels']
                self.assertEqual(labels['cluster'], 'hm-cluster')
                self.assertIn(labels['zone'], ['uk-ldn-1', 'ru-spb-1'])
                self.assertNotIn('region', labels)
                if 'node' in labels:
                    self.assertEqual(labels['host'], labels['zone'] + '/1')
                    ipaddress.ip_address(labels['machine'])
                if labels.get('probe_scope') == 'public-client':
                    self.assertNotEqual(labels['source_zone'], labels['target_zone'])

    def test_dashboards_scope_every_query_to_cluster_and_use_zone_filters(self):
        for name in ['overview', 'inventory', 'vpn']:
            dashboard = json.loads((ROOT / f'conf/monitoring/{name}.json').read_text())
            names = {v['name'] for v in dashboard['templating']['list']}
            self.assertIn('cluster', names)
            self.assertFalse({'region', 'entry_region', 'source_region', 'target_region', 'node'} & names)
            def walk(items):
                for panel in items:
                    yield panel
                    yield from walk(panel.get('panels', []))
            for panel in walk(dashboard['panels']):
                for target in panel.get('targets', []):
                    self.assertIn('${cluster:regex}', target['expr'])
                    self.assertNotIn('\\"', target['expr'])
        inventory = json.loads((ROOT / 'conf/monitoring/inventory.json').read_text())
        self.assertEqual({v['name'] for v in inventory['templating']['list']}, {'cluster', 'zone', 'host'})

    def test_cluster_sync_name_and_path_match_inventory(self):
        sync = yaml.safe_load((CLUSTER / 'flux-system/sync.yaml').read_text())
        self.assertEqual(sync['metadata']['name'], 'hm-cluster')
        self.assertEqual(sync['spec']['path'], './cluster/clusters/hm-cluster')
        self.assertFalse((ROOT / 'cluster/clusters/edge-01').exists())
        subprocess.check_output(['kubectl', 'kustomize', str(CLUSTER)])



if __name__ == '__main__':
    unittest.main()
