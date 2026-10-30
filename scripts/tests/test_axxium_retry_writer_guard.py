"""Test the actual canonical hold guard with isolated host-path fixtures."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / 'digitalocean/scripts/check-axxium-production-hold.sh'


class RetryWriterGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'srv'
        self.services = self.root / 'open-hax/services'
        self.axxium = self.services / 'axxium'
        self.colab = self.services / 'colab-mcp'
        self.axxium.mkdir(parents=True)
        self.colab.mkdir()
        self.marker = self.axxium / '.axxium-production-pending'
        self.other_marker = self.colab / '.colab-production-pending'
        # Use the real guard verbatim except for its host filesystem root.
        self.guard = GUARD.read_text().replace('/srv', str(self.root))

    def check(self, *args):
        return subprocess.run(['sh', '-s', '--', *args], input=self.guard, text=True,
                              capture_output=True, timeout=5).returncode

    def test_normal_writers_block_during_held_retry(self):
        self.marker.write_text('existing-operation-marker')
        # Repeated observations simulate competing writers while the original
        # hold is continuously present. They can act only after it is removed.
        for _ in range(5):
            self.assertEqual(self.check(), 74)
            self.assertEqual(self.marker.read_text(), 'existing-operation-marker')
        self.assertEqual(self.check('--require-axxium-hold'), 0)
        self.marker.unlink()
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.check('--require-axxium-hold'), 74)

    def test_recovery_does_not_bypass_colab_or_invalid_axxium_hold(self):
        self.marker.write_text('operation')
        self.other_marker.write_text('other-operation')
        self.assertEqual(self.check('--require-axxium-hold'), 74)
        self.other_marker.unlink()
        self.marker.unlink()
        self.marker.symlink_to('missing')
        self.assertEqual(self.check('--require-axxium-hold'), 74)
        self.assertEqual(self.check(), 74)
        self.assertEqual(self.check('--unknown'), 2)

    def test_current_production_writers_use_guard_before_effects(self):
        for name in ('deploy-digitalocean.yml', 'digitalocean-host.yml',
                     'deploy-axxium-minimal.yml', 'deploy-colab-qualified.yml',
                     'deploy-colab-transport-stage.yml'):
            with self.subTest(workflow=name):
                data = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
                jobs = [job for job in data['jobs'].values()
                        if any('check-axxium-production-hold.sh' in step.get('run', '')
                               for step in job.get('steps', []))]
                self.assertEqual(len(jobs), 1)
                steps = jobs[0]['steps']
                guard = next(i for i, step in enumerate(steps)
                             if 'check-axxium-production-hold.sh' in step.get('run', ''))
                self.assertFalse(any('--apply' in step.get('run', '') for step in steps[:guard]))

    def test_only_axxium_scope_may_request_recovery(self):
        data = yaml.safe_load((ROOT / '.github/workflows/deploy-stack.yml').read_text())
        admit = data['jobs']['authorize']['steps'][0]
        script = admit['run']
        self.assertIn('[ "$AXXIUM_RECOVER" = true ] && [ "$DEPLOY_SCOPE" != axxium ]', script)
        self.assertIn("inputs.axxium_recover_interrupted", str(data['jobs']['deploy']['with']))


if __name__ == '__main__':
    unittest.main()
