"""Exercise the workflow's real SSH command against a local fake transport."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/deploy-axxium-minimal.yml'


class SshReceiptTests(unittest.TestCase):
    def test_foreground_command_retains_private_result_for_success_and_failure(self):
        lines = WORKFLOW.read_text().splitlines()
        command = next(line.strip() for line in lines if line.strip().startswith('ssh open-hax-target "umask 077; env PYTHONDONTWRITEBYTECODE=1 python3'))
        for returncode in (0, 2):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                scripts = root / 'controller/scripts'
                scripts.mkdir(parents=True)
                (scripts / 'axxium_minimal_release.py').write_text(
                    f"import sys\nprint('controller-receipt')\nprint('private-diagnostic', file=sys.stderr)\nsys.exit({returncode})\n")
                program = 'ssh() { shift; bash -c "$1"; };\n' + command
                env = dict(os.environ, controller=str(root / 'controller'), PLAN_SHA256='a'*64)
                result = subprocess.run(['bash', '-c', program], cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, returncode)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, '')
                self.assertEqual((root / 'release-receipt.json').read_text(), 'controller-receipt\n')
                self.assertEqual((root / 'controller/release-status').read_text(), str(returncode)+'\n')
                self.assertEqual((root / 'controller/release.stderr').read_text(), 'private-diagnostic\n')
                for name in ('release.stdout', 'release.stderr', 'release-status'):
                    self.assertEqual((root / 'controller' / name).stat().st_mode & 0o777, 0o600)
                self.assertFalse((root / 'controller/release-status.tmp').exists())


if __name__ == '__main__':
    unittest.main()
