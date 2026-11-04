"""Fleet discovery, host selection and compact dashboard contracts."""
import json
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
def load(name):
    return json.loads((ROOT / 'conf/monitoring' / (name+'.json')).read_text())

class DashboardFilterTests(unittest.TestCase):
    def test_only_two_compact_native_dashboards(self):
        inventory, proxy = load('inventory'), load('proxy')
        self.assertEqual(inventory['title'], 'Hosts')
        self.assertEqual(proxy['title'], 'Proxy')
        self.assertEqual([p['type'] for p in inventory['panels']], ['stat']+['timeseries']*3)
        self.assertEqual([p['type'] for p in proxy['panels']], ['table']+['timeseries']*2)
        self.assertFalse((ROOT/'conf/monitoring/overview.json').exists())
        self.assertFalse((ROOT/'conf/monitoring/vpn.json').exists())
        for d in [inventory,proxy]:
            self.assertEqual(d['tags'], ['HM'])
            for v in d['templating']['list']:
                self.assertEqual(v['type'],'query')
                if v['name']!='cluster':self.assertEqual(v['allValue'],'.*')
            for p in d['panels']:
                for t in p['targets']:self.assertNotIn('or vector(0)',t['expr'])
            for value in ['spb-01','uk-ldn-1','ru-spb-1','87.106.74.217','212.116.115.29']:
                self.assertNotIn(value,json.dumps(d))

    def test_inventory_is_independent_of_services(self):
        d=load('inventory'); variables={v['name']:v for v in d['templating']['list']}
        self.assertEqual(set(variables),{'cluster','zone','host'})
        self.assertIn('host_label',variables['host']['query']['query'])
        self.assertIn('(?<value>',variables['host']['regex'])
        self.assertIn('(?<text>',variables['host']['regex'])
        for p in d['panels']:
            for t in p['targets']:
                self.assertIn('${zone:regex}',t['expr'])
                self.assertIn('${host:regex}',t['expr'])
                self.assertNotIn('hm:service_instance',t['expr'])

    def test_down_hosts_remain_visible_and_clickable(self):
        p=load('inventory')['panels'][0]
        self.assertEqual(p['repeat'],'zone')
        self.assertIn('hm:node:info',p['targets'][0]['expr'])
        self.assertIn('hm:node:up',p['targets'][0]['expr'])
        self.assertEqual(p['fieldConfig']['defaults']['mappings'][0]['options']['0']['text'],'Down')
        self.assertIn('var-host=',p['fieldConfig']['defaults']['links'][0]['url'])

    def test_host_charts_measure_execution_available_ram_and_mirrored_network(self):
        d=load('inventory'); byid={p['id']:p for p in d['panels']}
        self.assertIn('user|nice|system|irq|softirq',byid[5]['targets'][0]['expr'])
        self.assertIn('MemAvailable',byid[6]['targets'][0]['expr'])
        network=byid[7]['targets']
        self.assertTrue(network[1]['expr'].startswith('-8 *'))
        for q in network:
            self.assertIn('sum by (cluster,zone,host,machine)',q['expr'])
            self.assertNotIn('{{device}}',q['legendFormat'])
