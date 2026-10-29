"""Pin the privilege and serialization boundaries of the fixed Axxium caller."""
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import github_environment_admission as native_admission


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())


class FixedAxxiumWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.entry = workflow('deploy-stack.yml')
        self.chain = workflow('deploy-stack-chain.yml')
        self.body = workflow('deploy-axxium-minimal.yml')

    def test_one_existing_host_queue_covers_the_complete_scope(self):
        job = self.entry['jobs']['deploy']
        self.assertEqual(job['concurrency'], {'group': 'digitalocean-production-host', 'cancel-in-progress': False})
        self.assertEqual(job['uses'], './.github/workflows/deploy-stack-chain.yml')
        selected = self.chain['jobs']['deploy-axxium']
        self.assertEqual(selected['if'], "inputs.scope == 'axxium'")
        self.assertEqual(selected['uses'], './.github/workflows/deploy-axxium-minimal.yml')
        self.assertIs(selected['with']['caller_holds_production_host_lock'], True)
        self.assertNotIn('concurrency', selected)
        self.assertTrue(all('concurrency' not in j for j in self.body['jobs'].values()))

    def test_no_new_mutation_trigger_and_no_host_secret_in_admission(self):
        triggers = self.body.get('on', self.body.get(True))
        self.assertEqual(set(triggers), {'workflow_call'})
        admit = self.body['jobs']['admit']
        self.assertNotIn('environment', admit)
        text = str(admit)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('ssh open-hax-target', text)
        self.assertIn('production_qualification.py', text)
        self.assertIn('refs/heads/main', text)
        self.assertIn('deploy-stack.yml@refs/heads/main', text)
        self.assertIn('jq -e', text)

    def test_production_capability_requires_admission_and_fresh_recheck(self):
        deploy = self.body['jobs']['deploy']
        self.assertEqual(deploy['needs'], 'admit')
        self.assertEqual(deploy['if'], "needs.admit.outputs.admitted == 'true'")
        self.assertEqual(deploy['environment'], 'production')
        steps = deploy['steps']
        recheck = next(i for i, s in enumerate(steps) if 'production_qualification.py' in s.get('run', ''))
        secret = next(i for i, s in enumerate(steps) if 'SSH_PRIVATE_KEY' in s.get('env', {}))
        effect = next(i for i, s in enumerate(steps) if '--apply' in s.get('run', ''))
        self.assertLess(recheck, secret)
        self.assertLess(secret, effect)
        self.assertIn('EXPECTED_PLAN', steps[recheck]['run'])
        self.assertIn('jq -e', steps[recheck]['run'])

    def test_artifact_cli_and_canonical_native_api_receive_the_same_job_token(self):
        for job in self.body['jobs'].values():
            step = next(s for s in job['steps'] if 'production_qualification.py' in s.get('run', ''))
            self.assertEqual(step['env']['GH_TOKEN'], '${{ github.token }}')
            self.assertEqual(step['env']['GITHUB_TOKEN'], step['env']['GH_TOKEN'])
            # Exercise the real shared API adapter without a network request.
            # A workflow exporting only gh's GH_TOKEN previously failed here.
            values = {name: 'fixture-native-token' for name, value in step['env'].items()
                      if value == '${{ github.token }}'}
            with mock.patch.dict(os.environ, values, clear=True), \
                    mock.patch.object(native_admission, 'urlopen', return_value=io.BytesIO(b'{}')) as request:
                self.assertEqual(native_admission.api('repos/open-hax/services'), {})
                self.assertEqual(request.call_args.args[0].get_header('Authorization'),
                                 'Bearer fixture-native-token')

    def test_controller_delivery_never_builds_or_copies_application_code(self):
        text = '\n'.join(s.get('run', '') for s in self.body['jobs']['deploy']['steps'])
        self.assertNotIn('docker load', text)
        self.assertNotIn('docker build', text)
        self.assertNotIn('rsync', text)
        for name in ['axxium_minimal_release.py', 'axxium_oidc_ingress.py', 'provision_axxium_colab_identity.py']:
            self.assertIn('scripts/' + name, text)
        self.assertIn('assert_pinned_host_key', text)
        self.assertIn('StrictHostKeyChecking yes', text)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', text)

    def test_checkout_cannot_leave_its_token_in_controller_tree(self):
        for job in self.body['jobs'].values():
            checkouts = [s for s in job['steps'] if str(s.get('uses', '')).startswith('actions/checkout@')]
            self.assertEqual(len(checkouts), 1)
            self.assertIs(checkouts[0]['with']['persist-credentials'], False)

    def test_generic_renderer_refuses_axxium_before_outputs_or_ssh(self):
        definition = workflow('deploy-digitalocean.yml')
        step = next(s for s in definition['jobs']['deploy']['steps']
                    if s.get('name') == 'Load and validate host contract')
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / 'outputs'
            output.touch()
            env = os.environ.copy()
            env.update(SERVICE='axxium', DEPLOY_ENVIRONMENT='production',
                       CALLER_HOLDS_PRODUCTION_HOST_LOCK='true', GITHUB_OUTPUT=str(output))
            result = subprocess.run(['bash', '-c', step['run']], cwd=ROOT,
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('qualified fixed production workflow', result.stderr)
            self.assertEqual(output.read_text(), '')

    def test_canonical_writers_observe_the_same_hold(self):
        for name in ['deploy-digitalocean.yml', 'digitalocean-host.yml', 'deploy-axxium-minimal.yml']:
            definition = workflow(name)
            guarded = [job for job in definition['jobs'].values()
                       if any('check-axxium-production-hold.sh' in s.get('run', '')
                              for s in job.get('steps', []))]
            self.assertEqual(len(guarded), 1, name)
            steps = guarded[0]['steps']
            credential = next(i for i, s in enumerate(steps) if 'SSH_PRIVATE_KEY' in s.get('env', {}))
            hold = next(i for i, s in enumerate(steps) if 'check-axxium-production-hold.sh' in s.get('run', ''))
            self.assertLess(credential, hold, name)
            if name == 'digitalocean-host.yml':
                self.assertEqual(steps[hold]['if'], "steps.target.outputs.operation == 'bootstrap'")


if __name__ == '__main__':
    unittest.main()
