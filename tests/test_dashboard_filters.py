"""Regression boundaries for service/region selection and overview complexity."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))


class DashboardFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = json.loads((ROOT / 'conf/monitoring/overview.json').read_text())

    def test_service_values_are_service_identities_not_pod_regexes(self):
        variables = {v['name']: v for v in self.dashboard['templating']['list']}
        self.assertEqual(set(variables), {'service_name', 'region'})
        values = {v['value'] for v in variables['service_name']['options'] if v['value'] != '$__all'}
        self.assertEqual(values, {'gateway', 'website', 'observability'})
        self.assertEqual(variables['service_name']['current']['value'], 'gateway')

    def test_every_query_respects_both_selectors(self):
        for panel in panels(self.dashboard['panels']):
            for target in panel.get('targets', []):
                with self.subTest(panel=panel['title'], target=target['refId']):
                    self.assertIn('${service_name:regex}', target['expr'])
                    self.assertIn('${region:regex}', target['expr'])
                    self.assertNotIn('${service:raw}', target['expr'])
                    self.assertNotIn('${node:', target['expr'])

    def test_specialist_signals_are_collapsed(self):
        visible = [p for p in self.dashboard['panels'] if p['type'] not in ['row', 'text']]
        self.assertLessEqual(len(visible), 5)
        details = [p for p in self.dashboard['panels'] if p['type'] == 'row']
        self.assertTrue(details)
        self.assertTrue(all(p['collapsed'] for p in details))

    def test_services_exclude_physical_host_capacity(self):
        for panel in panels(self.dashboard['panels']):
            for target in panel.get('targets', []):
                for metric in ['node_memory_', 'node_cpu_', 'node_filesystem_', 'node_network_']:
                    self.assertNotIn(metric, target['expr'])

    def test_inventory_is_independent_of_services(self):
        inventory = json.loads((ROOT / 'conf/monitoring/inventory.json').read_text())
        variables = {v['name']: v for v in inventory['templating']['list']}
        self.assertEqual(set(variables), {'region', 'node'})
        self.assertIn('${region:regex}', variables['node']['query']['query'])
        for panel in panels(inventory['panels']):
            for target in panel.get('targets', []):
                self.assertIn('${region:regex}', target['expr'])
                self.assertIn('${node:regex}', target['expr'])
                self.assertNotIn('hm:service_instance', target['expr'])
                self.assertNotIn('service_name', target['expr'])

    def test_unsupported_signals_are_not_fabricated_as_zero(self):
        for panel in panels(self.dashboard['panels']):
            for target in panel.get('targets', []):
                self.assertNotIn('or vector(0)', target['expr'])


if __name__ == '__main__':
    unittest.main()
