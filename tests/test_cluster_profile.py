"""Verify both init adapters and K3s roles consume the same cluster policy."""
from pathlib import Path
import shlex
import os
import sys
import tempfile
import subprocess
import unittest
import yaml
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'vm/roles/vm/tasks'
PROFILE = yaml.safe_load((ROOT / 'cluster/clusters/hm-cluster/profile.yml').read_text())['vm_cluster_profile']


def tasks(name):
    return yaml.safe_load((TASKS / name).read_text())


class ClusterProfileTests(unittest.TestCase):
    def setUp(self):
        self.env = NativeEnvironment()
        self.env.filters['quote'] = shlex.quote
        self.env.filters['combine'] = lambda a, b: {**a, **b}
        self.env.filters['to_nice_yaml'] = yaml.safe_dump

    def test_boot_and_runtime_thp_modes_match_on_both_init_systems(self):
        policy = tasks('kernel-policy.yml')
        openrc = next(t['ansible.builtin.blockinfile']['block'] for t in policy if 'ansible.builtin.blockinfile' in t)
        systemd = next(t['ansible.builtin.copy']['content'] for t in policy if t.get('name', '').startswith('Persist transparent'))
        runtime = next(t['ansible.builtin.shell'] for t in policy if 'ansible.builtin.shell' in t)
        for mode in ['never', 'madvise']:
            with self.subTest(mode=mode):
                context = {'hm_machine_profile': {**PROFILE['host'], 'transparent_hugepages': mode}}
                scripts = [self.env.from_string(s).render(context) for s in [openrc, runtime]]
                for script in scripts:
                    subprocess.run(['/bin/sh', '-n'], input=script, text=True, check=True)
                    self.assertEqual(script.count("printf '%s\\n' " + mode), 2)
                lines = self.env.from_string(systemd).render(context).splitlines()
                self.assertEqual(len(lines), 2)
                for line in lines:
                    self.assertEqual(shlex.split(line)[-1], mode)

    def test_agent_resource_profile_does_not_override_identity_or_credentials(self):
        template = next(t['ansible.builtin.copy']['content'] for t in tasks('k3s-agent.yml') if t.get('name') == 'Write worker configuration')
        base = {'server': 'https://control-plane:6443', 'token': 'test-only', 'node-name': 'new-worker', 'node-taint': []}
        result = yaml.safe_load(self.env.from_string(template).render(config=base, hm_role_profile=PROFILE['roles']['agent']))
        self.assertEqual({k: result[k] for k in base}, base)
        self.assertEqual(result['kubelet-arg'], PROFILE['roles']['agent']['k3s_config']['kubelet-arg'])
        self.assertEqual(result['kube-proxy-arg'], PROFILE['roles']['agent']['k3s_config']['kube-proxy-arg'])
        self.assertEqual(PROFILE['roles']['control_plane']['k3s_config'], {})

    def test_invalid_role_or_credential_override_fails_before_host_access(self):
        inventory = yaml.safe_load((ROOT / 'cluster/clusters/hm-cluster/hosts.yml').read_text())
        for invalid in ['role', 'credentials']:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                hosts = yaml.safe_load(yaml.safe_dump(inventory))
                profile = yaml.safe_load(yaml.safe_dump(PROFILE))
                if invalid == 'role':
                    hosts['vm_node_settings'][hosts['vm_agent_hosts'][0]]['role'] = 'control_plane'
                else:
                    profile['roles']['agent']['k3s_config']['token'] = 'not-allowed'
                (folder / 'hosts.yml').write_text(yaml.safe_dump(hosts))
                (folder / 'profile.yml').write_text(yaml.safe_dump({'vm_cluster_profile': profile}))
                env = {**os.environ, 'ANSIBLE_CONFIG': str(ROOT / 'vm/ansible.cfg'),
                       'ANSIBLE_LOCAL_TEMP': str(folder / 'ansible-local')}
                result = subprocess.run([str(Path(sys.executable).parent / 'ansible-playbook'),
                    '-i', str(ROOT / 'vm/inventory/localhost.yml'),
                    str(ROOT / 'vm/playbooks/bootstrap.yml'),
                    '-e', 'vm_hosts_file=' + str(folder / 'hosts.yml'), '--limit', 'localhost'],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                expected = 'Each host role must match' if invalid == 'role' else 'Role configuration must not override'
                self.assertIn(expected, result.stdout + result.stderr)
                self.assertNotIn('PLAY [VM]', result.stdout)

    def test_common_policy_is_applied_before_either_kubernetes_role(self):
        install=tasks('install.yml')
        expanded=[child for task in install for child in (task.get('block') or [task])]
        order = [t.get('ansible.builtin.include_tasks') for t in expanded]
        for role in ['k3s.yml', 'k3s-agent.yml']:
            self.assertLess(order.index('low-memory.yml'), order.index(role))
        policy_order = [t.get('ansible.builtin.include_tasks') for t in tasks('low-memory.yml')]
        self.assertEqual(policy_order[0], 'kernel-policy.yml')
        dependencies = tasks('openrc-dependencies.yml')[0]['ansible.builtin.blockinfile']['block']
        self.assertIn('cgroups localmount', dependencies)


if __name__ == '__main__':
    unittest.main()
