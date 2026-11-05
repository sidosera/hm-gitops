"""Physical replacements preserve logical identity; no live hosts are contacted."""
import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('servers',ROOT/'scripts/xray_servers.py')
servers=importlib.util.module_from_spec(spec);spec.loader.exec_module(servers)


class ServerReplacementTests(unittest.TestCase):
    def setUp(self):
        self.inventory=yaml.safe_load((ROOT/'cluster/clusters/hm-cluster/hosts.yml').read_text())
        self.known=(ROOT/'vm/ssh_known_hosts').read_text()
        self.key=' '.join(next(line for line in self.known.splitlines() if line and not line.startswith('#')).split()[1:3])
        self.old=next(iter(self.inventory['vm_agent_hosts']))
        self.slot=self.inventory['vm_node_settings'][self.old]['host']

    def test_replacement_only_changes_physical_addresses_and_pinned_trust(self):
        original=copy.deepcopy(self.inventory)
        updated,known=servers.replace_server(self.inventory,self.known,self.slot,'8.8.8.8',self.key)
        self.assertEqual(original,self.inventory)
        self.assertEqual(updated['vm_agent_hosts'],['8.8.8.8'])
        self.assertEqual(updated['vm_hosts'],original['vm_hosts'])
        new=updated['vm_node_settings']['8.8.8.8']
        old=copy.deepcopy(original['vm_node_settings'][self.old]);old.pop('ipv6',None);old['name']='8.8.8.8'
        self.assertEqual(new,old)
        self.assertNotIn(self.old+' ',known)
        self.assertIn('8.8.8.8 '+self.key,known)
        self.assertNotIn(self.old,updated['vm_node_settings'])

    def test_bad_identity_addresses_or_unknown_slot_are_rejected(self):
        for slot,address,key,ipv6 in [
            (self.slot,'127.0.0.1',self.key,None),
            (self.slot,self.inventory['vm_hosts'][0],self.key,None),
            ('missing/1','8.8.8.8',self.key,None),
            (self.slot,'8.8.8.8','ssh-ed25519 invalid',None),
            (self.slot,'8.8.8.8',self.key,'fd00::1')]:
            with self.assertRaises(ValueError):
                servers.replace_server(self.inventory,self.known,slot,address,key,ipv6)


if __name__=='__main__':unittest.main()
