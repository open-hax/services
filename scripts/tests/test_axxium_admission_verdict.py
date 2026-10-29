"""Execute both workflow admission branches without network or host access."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
PLAN = Path('digitalocean/services/axxium/minimal-production-plan.json')
COLLECTOR = ROOT / 'scripts/axxium_production_qualification.py'

STUB = r'''#!__PYTHON__
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
with Path(os.environ['TRACE']).open('a') as stream:
    stream.write(name + '\n')
if name == 'gh':
    sys.stdout.buffer.write(b'fixture-stage-archive')
elif name == 'python3':
    mode = os.environ['VERDICT_MODE']
    if mode == 'malformed':
        print('fixture-sensitive-error-body')
        sys.exit(1)
    if mode in ('accepted', 'nonzero_accepted'):
        value = {'admitted': True, 'plan_sha256': os.environ['EXPECTED_PLAN']}
        code = 0 if mode == 'accepted' else 1
    else:
        # Use the real collector's refusal JSON and exit status. A missing plan
        # refuses before any native API access; add an untrusted extra field to
        # prove the workflow projects keys instead of dumping the response.
        result = subprocess.run([__PYTHON_LITERAL__, __COLLECTOR_LITERAL__,
            '--plan', 'missing-plan.json', '--controller-sha', 'a' * 40,
            '--archive', 'stage.zip'], capture_output=True, text=True, check=False)
        value = json.loads(result.stdout)
        assert result.returncode == 1 and value['admitted'] is False
        value['error_body'] = 'fixture-sensitive-error-body'
        code = 0 if mode == 'zero_denied' else result.returncode
    print(json.dumps(value))
    sys.exit(code)
'''


class AdmissionVerdictTests(unittest.TestCase):
    def setUp(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/deploy-axxium-minimal.yml').read_text())
        self.steps = {
            name: next(step['run'] for step in job['steps']
                       if 'production_qualification.py' in step.get('run', ''))
            for name, job in workflow['jobs'].items()
        }

    def execute(self, body, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / PLAN
            plan.parent.mkdir(parents=True)
            plan.write_bytes((ROOT / PLAN).read_bytes())
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            source = STUB.replace('__PYTHON__', sys.executable).replace(
                '__PYTHON_LITERAL__', repr(sys.executable)).replace(
                '__COLLECTOR_LITERAL__', repr(str(COLLECTOR)))
            for name in ('gh', 'python3', 'ssh'):
                path = bin_dir / name
                path.write_text(source)
                path.chmod(0o700)
            output = root / 'outputs'
            output.touch()
            trace = root / 'trace'
            env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.defpath,
                   'GITHUB_OUTPUT': str(output), 'TRACE': str(trace),
                   'VERDICT_MODE': mode, 'CONTROLLER_SHA': 'a' * 40,
                   'EXPECTED_PLAN': hashlib.sha256(plan.read_bytes()).hexdigest()}
            # The SSH stub is a negative control for the next host-capable step.
            result = subprocess.run(['bash', '-c', body + '\nssh fixture-host\n'],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            return result, output.read_text(), trace.read_text().splitlines(), env['EXPECTED_PLAN']

    def test_actual_collector_refusal_is_projected_before_outputs_or_ssh(self):
        for name, body in self.steps.items():
            with self.subTest(job=name):
                result, output, trace, _ = self.execute(body, 'denied')
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stdout, 'Workflow hid the actual collector refusal JSON')
                self.assertEqual(json.loads(result.stdout), {
                    'admitted': False, 'reason': 'production plan unavailable'})
                self.assertNotIn('fixture-sensitive-error-body', result.stdout + result.stderr)
                self.assertEqual(output, '')
                self.assertEqual(trace, ['gh', 'python3'])

    def test_zero_exit_with_denial_still_reports_and_blocks(self):
        for name, body in self.steps.items():
            with self.subTest(job=name):
                result, output, trace, _ = self.execute(body, 'zero_denied')
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stdout, 'Workflow hid the denial verdict')
                self.assertEqual(json.loads(result.stdout)['admitted'], False)
                self.assertEqual(output, '')
                self.assertNotIn('ssh', trace)

    def test_malformed_or_nonzero_accepted_response_never_leaks_or_admits(self):
        for name, body in self.steps.items():
            for mode in ('malformed', 'nonzero_accepted'):
                with self.subTest(job=name, mode=mode):
                    result, output, trace, _ = self.execute(body, mode)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(result.stdout, 'Workflow omitted its safe refusal fallback')
                    self.assertEqual(json.loads(result.stdout), {
                        'admitted': False, 'reason': 'production evidence unavailable or malformed'})
                    self.assertNotIn('fixture-sensitive-error-body', result.stdout + result.stderr)
                    self.assertEqual(output, '')
                    self.assertNotIn('ssh', trace)

    def test_accepted_response_keeps_existing_output_and_next_step_behavior(self):
        for name, body in self.steps.items():
            with self.subTest(job=name):
                result, output, trace, expected = self.execute(body, 'accepted')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f'admitted=true\nplan_sha256={expected}\n' if name == 'admit' else '')
                self.assertEqual(trace, ['gh', 'python3', 'ssh'])


if __name__ == '__main__':
    unittest.main()
