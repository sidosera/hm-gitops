"""Preserve application identities while encrypting and reconciling their storage."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('encryption_migration', ROOT / 'vm/roles/vm/files/enable-secrets-encryption.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
ENABLED = 'Encryption Status: Enabled\nCurrent Rotation Stage: reencrypt_finished'
START = 'Encryption Status: Disabled\nCurrent Rotation Stage: start'
NODES = json.dumps({'items': [{'metadata': {'labels': {'node-role.kubernetes.io/control-plane': 'true'}}}]})


class EncryptionLifecycleTests(unittest.TestCase):
    def test_enabled_cluster_is_idempotent_and_does_not_rotate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'
            config.write_text('secrets-encryption: true\nnode-name: stable\n')
            with patch.object(migration, 'CONFIG', config), patch.object(migration, 'status', return_value=ENABLED), \
                 patch.object(migration, 'command') as command, patch.object(migration, 'backup') as backup:
                migration.migrate('public-recipient')
                command.assert_not_called()
                backup.assert_not_called()
            self.assertEqual(yaml.safe_load(config.read_text())['node-name'], 'stable')

    def test_existing_cluster_finishes_reencryption_and_keeps_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml';config.write_text('secrets-encryption: true\nnode-name: stable\n')
            server = Path(directory) / 'server';(server / 'cred').mkdir(parents=True)
            (server / 'cred/encryption-config.json').write_text('{"resources": []}')
            with patch.object(migration, 'CONFIG', config), patch.object(migration, 'SERVER', server), \
                 patch.object(migration, 'status', side_effect=[START, START, ENABLED, ENABLED]), \
                 patch.object(migration, 'command', return_value=NODES) as command, \
                 patch.object(migration, 'backup') as backup, patch.object(migration, 'wait_ready'):
                migration.migrate('public-recipient')
                commands = [call.args for call in command.call_args_list]
                self.assertEqual(commands.count(('k3s', 'secrets-encrypt', 'rotate-keys')), 1)
                self.assertEqual(commands.count(('systemctl', 'restart', 'k3s')), 1)
                self.assertEqual(backup.call_count, 2)
            self.assertEqual(yaml.safe_load(config.read_text())['node-name'], 'stable')

    def test_existing_ha_cluster_is_not_migrated_as_single_server(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml';config.write_text('{}')
            nodes = json.loads(NODES);nodes['items'] *= 2
            with patch.object(migration, 'CONFIG', config), patch.object(migration, 'status', return_value=START), \
                 patch.object(migration, 'command', return_value=json.dumps(nodes)), patch.object(migration, 'backup') as backup:
                with self.assertRaises(RuntimeError):migration.migrate('public-recipient')
                backup.assert_not_called()

    def test_new_servers_enable_encryption_from_first_start(self):
        tasks = yaml.safe_load((ROOT / 'vm/roles/vm/tasks/k3s.yml').read_text())
        config = next(t for t in tasks if t.get('name') == 'Build k3s server config')['vars']['_base']
        self.assertTrue(config['secrets-encryption'])


class GitSecretContractTests(unittest.TestCase):
    def test_one_secret_with_scoped_mounts_and_automatic_rollout_revision(self):
        docs = list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(ROOT / 'cluster/clusters/hm-cluster')], text=True)))
        secret = next(d for d in docs if d['kind'] == 'Secret' and d['metadata']['name'] == 'xray-credentials')
        workloads = [d for d in docs if d['kind'] == 'Deployment' and d['metadata'].get('labels', {}).get('hm.gitops/xray') == 'true']
        self.assertEqual(len(workloads), 3)
        for deployment in workloads:
            pod = deployment['spec']['template']
            self.assertEqual(pod['metadata']['annotations']['hm.gitops/credentials-revision'], secret['sops']['lastmodified'])
            self.assertFalse(pod['spec']['automountServiceAccountToken'])
            sources = []
            for volume in pod['spec']['volumes']:
                if 'secret' in volume:
                    sources.append(volume['secret'])
                for source in volume.get('projected', {}).get('sources', []):
                    if 'secret' in source:sources.append(source['secret'])
            selected = [s for s in sources if s.get('name', s.get('secretName')) == 'xray-credentials']
            self.assertEqual(len(selected), 1)
            self.assertEqual(len(selected[0]['items']), 1)
            self.assertIn(selected[0]['items'][0]['key'], secret['data'])
        names = {d['metadata']['name'] for d in docs if d['kind'] == 'Secret'}
        self.assertFalse({'proxy-service-config-source', 'spb-gateway-config-source', 'ru-spb-1-vpn-probe-client'} & names)


if __name__ == '__main__':
    unittest.main()
