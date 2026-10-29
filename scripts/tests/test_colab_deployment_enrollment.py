"""Recording an unenrolled bridge cannot unlock generic production mutation."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class ColabEnrollmentBoundaryTests(unittest.TestCase):
    def test_generic_deployment_refuses_before_rendering_or_ssh(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/deploy-digitalocean.yml').read_text())
        steps = workflow['jobs']['deploy']['steps']
        step = next(s for s in steps if s.get('name') == 'Load and validate host contract')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'outputs'
            output.touch()
            env = os.environ.copy()
            env.update(SERVICE='colab-mcp', DEPLOY_ENVIRONMENT='production',
                       CALLER_HOLDS_PRODUCTION_HOST_LOCK='true', GITHUB_OUTPUT=str(output))
            result = subprocess.run(['bash', '-c', step['run']], cwd=ROOT,
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('not enrolled', result.stderr)
            self.assertEqual(output.read_text(), '')

    def test_descriptor_does_not_claim_a_production_phase(self):
        value = yaml.safe_load((ROOT / 'digitalocean/services/colab-mcp/service.yaml').read_text())
        self.assertEqual(value['phases'], [])
        self.assertIs(value['verify']['required'], True)


if __name__ == '__main__':
    unittest.main()
