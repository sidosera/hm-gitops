"""Security boundaries for public API access and the shared Ansible policy."""
from pathlib import Path
import shlex
import unittest
import jinja2
from ansible.plugins.filter.core import to_bool
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'vm/roles/vm/tasks'


def render(script, agent=False):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters['quote'] = shlex.quote
    env.filters['bool'] = to_bool
    defaults = yaml.safe_load((ROOT / 'vm/roles/vm/defaults/main.yml').read_text())
    return env.from_string(script).render(defaults, hm_is_agent=agent,
        hm_cluster_peers=['203.0.113.1', '203.0.113.2'],
        hm_cluster_peer_ipv6=['2001:db8::1', '2001:db8::2'],
        hm_machine_profile={'firewall_allowed_tcp': [22, 80, 443, 6443]},
        ansible_host='203.0.113.1', hm_node_ipv6='2001:db8::1')


class APIFirewallTests(unittest.TestCase):
    def script(self):
        return yaml.safe_load((TASKS / 'api-firewall.yml').read_text())[0]['ansible.builtin.shell']

    def test_server_allows_peers_and_interface_scoped_pods_before_removing_public_rules(self):
        lines = [shlex.split(s) for s in render(self.script()).splitlines() if s.strip().startswith('ufw')]
        deletes = [i for i, args in enumerate(lines) if 'delete' in args]
        allows = [i for i, args in enumerate(lines) if 'allow' in args and 'delete' not in args]
        self.assertLess(max(allows), min(deletes))
        self.assertIn(['ufw', 'allow', 'from', '203.0.113.2', 'to', 'any', 'port', '6443', 'proto', 'tcp'], lines)
        self.assertIn(['ufw', 'allow', 'from', '2001:db8::2', 'to', 'any', 'port', '6443', 'proto', 'tcp'], lines)
        for i in allows:
            args = lines[i]
            self.assertIn('from', args)
            source = args[args.index('from') + 1]
            self.assertNotIn(source, ['any', '0.0.0.0/0', '::/0'])
            if '/' in source:
                self.assertIn('on', args)
                self.assertIn(args[args.index('on') + 1], ['cni0', 'flannel-wg', 'flannel-wg-v6'])
        self.assertNotIn('reset', render(self.script()))

    def test_worker_only_removes_public_api_rules(self):
        lines = [shlex.split(s) for s in render(self.script(), agent=True).splitlines() if s.strip().startswith('ufw')]
        self.assertEqual(lines, [['ufw', '--force', 'delete', 'allow', '6443/tcp'], ['ufw', '--force', 'delete', 'allow', '6443']])

    def test_full_provisioning_cannot_reopen_api_from_an_old_public_profile(self):
        security = yaml.safe_load((TASKS / 'security.yml').read_text())
        script = security[0]['block'][0]['ansible.builtin.shell']
        for agent in [False, True]:
            rendered = render(script, agent=agent)
            self.assertNotIn('ufw allow 6443', rendered)
        defaults = yaml.safe_load((ROOT / 'vm/group_vars/all.yml').read_text())
        self.assertNotIn(6443, defaults['vm_firewall_allowed_tcp'])
        self.assertTrue(any(task.get('ansible.builtin.include_tasks') == 'api-firewall.yml' for task in security))


if __name__ == '__main__':
    unittest.main()
