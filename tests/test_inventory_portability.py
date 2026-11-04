"""Replacing physical machines changes inventory endpoints, not service identities."""
import copy
import importlib.util
from pathlib import Path
import shlex
import subprocess
import unittest
import yaml
import jinja2

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('inventory', ROOT/'scripts/cluster_inventory.py')
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class InventoryPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.hosts = yaml.safe_load((ROOT/'cluster/clusters/hm-cluster/hosts.yml').read_text())
        self.profile = yaml.safe_load((ROOT/'cluster/clusters/hm-cluster/profile.yml').read_text())

    def replacement(self):
        result = copy.deepcopy(self.hosts)
        mappings = {address: f'203.0.113.{index}' for index,address in enumerate(result['vm_node_settings'],10)}
        result['vm_node_settings'] = {mappings[address]: {**machine, 'name':mappings[address],
            'ipv6':f'2001:db8::{index}'} for index,(address,machine) in enumerate(result['vm_node_settings'].items(),1)}
        for field in ('vm_hosts','vm_agent_hosts'):
            result[field] = [mappings[address] for address in result.get(field,[])]
        return result

    def test_replacing_every_vm_preserves_private_addresses_and_public_service_names(self):
        replaced = self.replacement()
        inventory.validate(self.hosts, self.profile)
        inventory.validate(replaced, self.profile)
        old = inventory.public_discovery(self.hosts)
        new = inventory.public_discovery(replaced)
        self.assertEqual([m['private_ipv4'] for m in self.hosts['vm_node_settings'].values()],
                         [m['private_ipv4'] for m in replaced['vm_node_settings'].values()])
        self.assertEqual([m['private_ipv6'] for m in self.hosts['vm_node_settings'].values()],
                         [m['private_ipv6'] for m in replaced['vm_node_settings'].values()])
        for before,after in zip(old,new):
            if before['kind']=='EndpointSlice':
                self.assertNotEqual(before['endpoints'], after['endpoints'])
                self.assertEqual({k:v for k,v in before.items() if k!='endpoints'},
                                 {k:v for k,v in after.items() if k!='endpoints'})
            else:
                self.assertEqual(before, after)

    def test_duplicate_or_non_private_transport_identity_is_rejected(self):
        for field,value in [('private_ipv4','203.0.113.1'),('private_ipv6','2001:db8::1')]:
            invalid = copy.deepcopy(self.hosts)
            next(iter(invalid['vm_node_settings'].values()))[field] = value
            with self.assertRaises(ValueError): inventory.validate(invalid,self.profile)
        invalid = copy.deepcopy(self.hosts)
        machines = list(invalid['vm_node_settings'].values())
        machines[1]['private_ipv4'] = machines[0]['private_ipv4']
        with self.assertRaises(ValueError): inventory.validate(invalid,self.profile)

    def test_metrics_use_node_discovery_and_stable_tls_identity(self):
        config = yaml.safe_load((ROOT/'conf/monitoring/prometheus.yml').read_text())
        job = next(j for j in config['scrape_configs'] if j['job_name']=='node-exporter')
        self.assertNotIn('static_configs',job)
        self.assertEqual(job['kubernetes_sd_configs'],[{'role':'node'}])
        self.assertEqual(job['tls_config']['server_name'],'node-exporter.hm.svc.cluster.local')
        self.assertNotIn('kubelet', {j['job_name'] for j in config['scrape_configs']})
        labels={rule.get('target_label'):rule.get('source_labels') for rule in job['relabel_configs']}
        self.assertEqual(labels['__address__'],['__meta_kubernetes_node_address_InternalIP'])
        self.assertEqual(labels['host'],['__meta_kubernetes_node_annotation_hm_gitops_host'])
        self.assertEqual(labels['machine'],['__meta_kubernetes_node_label_hm_gitops_machine'])

    def test_bootstrap_fetches_current_kubeconfig_over_pinned_ssh(self):
        text=(ROOT/'.github/workflows/flux-bootstrap.yml').read_text()
        self.assertNotIn('GITOPS_KUBECONFIG_B64',text)
        self.assertIn("'sudo -n cat /etc/rancher/k3s/k3s.yaml' > \"$KUBECONFIG\"",text)
        self.assertIn('-L 127.0.0.1:16443:127.0.0.1:6443',text)
        self.assertIn('--tls-server-name="$GITOPS_API_HOST"',text)
        self.assertNotIn('insecure-skip-tls-verify',text)
        self.assertIn('-o StrictHostKeyChecking=yes',text)

    def test_services_and_dashboards_do_not_contain_physical_addresses(self):
        paths=list((ROOT/'services').rglob('*.json'))+[ROOT/'conf/monitoring/prometheus.yml']
        paths += list((ROOT/'conf/monitoring').glob('*.json'))
        for path in paths:
            for address,machine in self.hosts['vm_node_settings'].items():
                self.assertNotIn(address,path.read_text(),str(path))
                self.assertNotIn(machine['ipv6'],path.read_text(),str(path))

    def test_private_transport_is_required_before_either_kubernetes_role(self):
        tasks=yaml.safe_load((ROOT/'vm/roles/vm/tasks/install.yml').read_text())
        expanded=[child for task in tasks for child in (task.get('block') or [task])]
        order=[t.get('ansible.builtin.include_tasks') for t in expanded]
        for role in ['k3s.yml','k3s-agent.yml']:
            self.assertLess(order.index('private-network.yml'),order.index(role))
        deps=(ROOT/'vm/roles/vm/tasks/openrc-dependencies.yml').read_text()
        self.assertIn('wg-quick.hm0',deps)
        tasks=(ROOT/'vm/roles/vm/tasks/private-network.yml').read_text()
        self.assertIn('Requires=wg-quick@hm0.service',tasks)
        self.assertNotIn('PersistentKeepalive',tasks)
        self.assertNotIn('timer',tasks)

    def test_management_guard_covers_public_interfaces_and_local_pod_bypass(self):
        env=jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters['quote']=shlex.quote
        template=(ROOT/'vm/roles/vm/templates/nodeport-guard.sh.j2').read_text()
        script=env.from_string(template).render(
            ansible_facts={'default_ipv4':{'interface':'eth0'},'default_ipv6':{'interface':'eth1'}},
            hm_network=self.profile['vm_cluster_profile']['network'])
        subprocess.run(['/bin/sh','-n'],input=script,text=True,check=True)
        for tool in ['/usr/sbin/iptables','/usr/sbin/ip6tables']:
            for interface in ['eth0','eth1']:
                self.assertIn(f'{tool} -w 5 -t raw -I PREROUTING 1 -i {interface} -p tcp -m multiport --dports 6443,9100,10250 -j DROP',script)
        self.assertIn('-i cni0 ! -d 10.77.0.0/24',script)
        self.assertIn('-i cni0 ! -d fd77::/64',script)
        self.assertIn('--dports 22,6443,9100,10250 -m addrtype --dst-type LOCAL -j DROP',script)

    def test_public_probe_dns_changes_only_transport_destination_not_website_dns(self):
        spec=importlib.util.spec_from_file_location('provision',ROOT/'scripts/provision_vpn_probe.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        inbound={'streamSettings':{'realitySettings':{
            'privateKey':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
            'serverNames':['example.org'],'shortIds':['0011223344556677']}}}
        config=module.client_config(inbound,'test-only-probe','vpn-entry-test.hm.svc.cluster.local')
        self.assertEqual(config['outbounds'][0]['protocol'],'blackhole')
        public=next(o for o in config['outbounds'] if o['tag']=='public-vpn')
        self.assertEqual(public['settings']['vnext'][0]['port'],443)
        self.assertEqual(public['streamSettings']['sockopt']['domainStrategy'],'ForceIPv4')
        rules=config['routing']['rules']
        self.assertEqual(rules[0],{'type':'field','ip':['10.43.0.10'],'port':'53','outboundTag':'cluster-dns'})
        self.assertEqual(rules[1]['outboundTag'],'public-vpn')
