"""The read-only Xray binary must be safely replaceable after a node reboot."""
import hashlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
DIGEST = 'e4bd9119f50ae7c41658b1abd0679ae06f98bd864a2907ff33f2f2cf7081b214'

class StatsClientInitTests(unittest.TestCase):
    def setUp(self):
        doc = yaml.safe_load((ROOT / 'cluster/clusters/hm-cluster/monitoring/node-exporter.yaml').read_text())
        self.code = doc['spec']['template']['spec']['initContainers'][0]['command'][-1]
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('xray', b'verified-test-binary')
        self.archive = archive.getvalue()

    def run_init(self, directory, valid_digest=True):
        code = self.code.replace('/tools', str(directory))
        if valid_digest:
            code = code.replace(DIGEST, hashlib.sha256(self.archive).hexdigest())
        with patch('urllib.request.urlopen', return_value=io.BytesIO(self.archive)):
            exec(compile(code, 'stats-client-init', 'exec'), {})

    def test_replaces_readonly_binary_and_survives_repeated_initialization(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = directory / 'xray';target.write_bytes(b'previous-binary');target.chmod(0o555)
            partial = directory / 'xray.next';partial.write_bytes(b'interrupted-write');partial.chmod(0o555)
            for _ in range(2):
                self.run_init(directory)
                self.assertEqual(target.read_bytes(), b'verified-test-binary')
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o555)
                self.assertFalse(partial.exists())
                self.assertFalse((directory / 'client.zip').exists())

    def test_checksum_failure_preserves_current_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp);target = directory / 'xray';target.write_bytes(b'current-binary');target.chmod(0o555)
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                self.run_init(directory, valid_digest=False)
            self.assertEqual(target.read_bytes(), b'current-binary')
