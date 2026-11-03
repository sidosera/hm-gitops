"""Dashboard domain boundaries and fleet discovery contracts."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))


def load(name):
    return json.loads((ROOT / 'conf/monitoring' / name).read_text())


class DashboardFilterTests(unittest.TestCase):
    def test_filters_discover_fleet_dimensions(self):
        for name in ['overview.json', 'inventory.json', 'vpn.json']:
            for variable in load(name)['templating']['list']:
                self.assertEqual(variable['type'], 'query')
                self.assertTrue(variable['query']['query'].startswith(('label_values(', 'query_result(')))
                if variable['name'] != 'cluster':
                    self.assertEqual(variable['allValue'], '.*')

    def test_services_select_current_identities_not_historical_names(self):
        variable = next(v for v in load('overview.json')['templating']['list'] if v['name'] == 'service_name')
        self.assertIn('query_result(count by (service)', variable['query']['query'])
        self.assertIn('service=', variable['regex'])

    def test_generic_service_queries_are_consistently_scoped(self):
        for panel in panels(load('overview.json')['panels']):
            for target in panel.get('targets', []):
                self.assertIn('${service_name:regex}', target['expr'])
                self.assertIn('${zone:regex}', target['expr'])
                for metric in ['xray_', 'probe_', 'node_memory_', 'node_cpu_', 'node_filesystem_', 'node_network_']:
                    self.assertNotIn(metric, target['expr'])

    def test_inventory_is_independent_of_services(self):
        inventory = load('inventory.json')
        variables = {v['name']: v for v in inventory['templating']['list']}
        self.assertEqual(set(variables), {'cluster', 'zone', 'host'})
        self.assertIn('${zone:regex}', variables['host']['query']['query'])
        for panel in panels(inventory['panels']):
            for target in panel.get('targets', []):
                self.assertIn('${zone:regex}', target['expr'])
                self.assertIn('${host:regex}', target['expr'])
                self.assertNotIn('hm:service_instance', target['expr'])

    def test_vpn_separates_entry_paths_and_measurement_locations(self):
        vpn = load('vpn.json')
        by_id = {p['id']: p for p in panels(vpn['panels'])}
        for target in by_id[3]['targets']:
            self.assertIn('${entry_zone:regex}', target['expr'])
        self.assertNotIn(20, by_id)  # Retired Blackbox transport diagnostics.
        for target in by_id[30]['targets']:
            self.assertIn('${source_zone:regex}', target['expr'])
            self.assertNotIn('${entry_zone:regex}', target['expr'])
        for target in by_id[21]['targets']:
            self.assertIn('by(cluster,zone,target)', target['expr'])
            self.assertNotIn('${entry_zone:regex}', target['expr'])

    def test_unavailable_hosts_and_services_have_explicit_status(self):
        inventory = load('inventory.json')
        status = next(p for p in inventory['panels'] if p['title'] == 'Host availability')
        self.assertIn('hm:node:up', status['targets'][0]['expr'])
        self.assertEqual(status['fieldConfig']['defaults']['mappings'][0]['options']['0']['text'], 'Down')
        for variable in inventory['templating']['list']:
            self.assertIn('hm:node:info', variable['query']['query'])
        for name in ['overview.json', 'vpn.json', 'proxy.json']:
            summary = next(p for p in load(name)['panels'] if p['id'] == 2)
            self.assertTrue(any('hm:service_instance:down' in q['expr'] for q in summary['targets']))
            self.assertIn('Down', summary['title'])

    def test_no_current_topology_constants_in_dashboards(self):
        for name in ['overview.json', 'inventory.json', 'vpn.json']:
            dashboard = json.dumps(load(name))
            for constant in ['spb-01', 'node|spb-node', 'uk|spb', 'checked from SPB', 'UK measurement', '87.106.74.217', '212.116.115.29']:
                self.assertNotIn(constant, dashboard)

    def test_overviews_remain_compact_and_missing_data_is_not_zero(self):
        for name in ['overview.json', 'vpn.json']:
            dashboard = load(name)
            visible = [p for p in dashboard['panels'] if p['type'] not in ['row', 'text']]
            self.assertLessEqual(len(visible), 5)
            self.assertTrue(all(p['collapsed'] for p in dashboard['panels'] if p['type'] == 'row'))
            for panel in panels(dashboard['panels']):
                for target in panel.get('targets', []):
                    self.assertNotIn('or vector(0)', target['expr'])


if __name__ == '__main__':
    unittest.main()
