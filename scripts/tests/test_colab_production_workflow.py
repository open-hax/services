"""The existing host queue and native qualification precede host capability."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import github_environment_admission as native


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())


class WorkflowTests(unittest.TestCase):
    def test_fixed_scope_uses_only_the_existing_complete_host_queue(self):
        entry, chain = workflow('deploy-stack.yml'), workflow('deploy-stack-chain.yml')
        self.assertEqual(entry['jobs']['deploy']['concurrency'],
            {'group': 'digitalocean-production-host', 'cancel-in-progress': False})
        selected = chain['jobs']['deploy-colab-production']
        self.assertEqual(selected['if'], "inputs.scope == 'colab'")
        self.assertEqual(selected['uses'], './.github/workflows/deploy-colab-qualified.yml')
        self.assertIs(selected['with']['caller_holds_production_host_lock'], True)
        self.assertNotIn('concurrency', selected)

    def test_admission_has_no_ssh_or_host_secret_and_the_only_trigger_is_workflow_call(self):
        body = workflow('deploy-colab-qualified.yml')
        self.assertEqual(set(body.get('on', body.get(True))), {'workflow_call'})
        self.assertEqual(body['permissions'], {'contents': 'read', 'actions': 'read', 'pull-requests': 'read'})
        admit = body['jobs']['admit']
        self.assertNotIn('secrets.', str(admit))
        self.assertNotIn('ssh open-hax-target', str(admit))
        self.assertNotIn('environment', admit)
        text = '\n'.join(step.get('run', '') for step in admit['steps'])
        self.assertLess(text.index('validate_plan'), text.index('gh api'))
        self.assertIn('deploy-stack.yml@refs/heads/main', text)
        self.assertIn('colab_production_admission.py', text)

    def test_fresh_native_recheck_and_shared_hold_precede_first_host_mutation(self):
        deploy = workflow('deploy-colab-qualified.yml')['jobs']['deploy']
        self.assertEqual(deploy['needs'], 'admit')
        self.assertEqual(deploy['environment'], 'production')
        steps = deploy['steps']
        evidence = next(i for i, s in enumerate(steps) if 'colab_production_admission.py' in s.get('run', ''))
        credential = next(i for i, s in enumerate(steps) if 'SSH_PRIVATE_KEY' in s.get('env', {}))
        hold = next(i for i, s in enumerate(steps) if 'check-axxium-production-hold.sh' in s.get('run', ''))
        effect = next(i for i, s in enumerate(steps) if '--apply' in s.get('run', ''))
        self.assertLess(evidence, credential)
        self.assertLess(credential, hold)
        self.assertLess(hold, effect)
        self.assertTrue(all('concurrency' not in job for job in workflow('deploy-colab-qualified.yml')['jobs'].values()))

    def test_native_api_and_gh_cli_receive_the_same_read_only_job_token(self):
        for job in workflow('deploy-colab-qualified.yml')['jobs'].values():
            step = next(s for s in job['steps'] if 'colab_production_admission.py' in s.get('run', ''))
            self.assertEqual(step['env']['GH_TOKEN'], '${{ github.token }}')
            self.assertEqual(step['env']['GITHUB_TOKEN'], step['env']['GH_TOKEN'])
            with patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic-native-token'}, clear=True), \
                 patch.object(native, 'urlopen', return_value=io.BytesIO(b'{}')) as request:
                self.assertEqual(native.api('repos/open-hax/services'), {})
                self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer synthetic-native-token')

    def test_delivery_is_controller_only_without_source_build_or_new_auth_store(self):
        body = workflow('deploy-colab-qualified.yml')
        text = '\n'.join(s.get('run', '') for s in body['jobs']['deploy']['steps'])
        self.assertIn('StrictHostKeyChecking yes', text)
        self.assertIn('assert_pinned_host_key', text)
        self.assertIn('digitalocean/qualifications/colab-mcp/', text)
        self.assertNotIn('docker build', text)
        self.assertNotIn('docker load', text)
        self.assertNotIn('rsync', text)
        self.assertNotIn('provision_axxium_colab_identity.py --provision', text)
        for job in body['jobs'].values():
            checkouts = [s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@')]
            self.assertEqual(len(checkouts), 1)
            self.assertIs(checkouts[0]['with']['persist-credentials'], False)
            self.assertEqual(checkouts[0]['with']['ref'], '${{ github.sha }}')

    def guard(self, parent, prepare):
        # Change fixed paths only in this private test copy, never add a runtime
        # environment/path override to the canonical production guard.
        root = Path(parent)
        for name in ('srv', 'srv/open-hax', 'srv/open-hax/services'):
            (root / name).mkdir(exist_ok=True)
        prepare(root / 'srv/open-hax/services')
        text = (ROOT / 'digitalocean/scripts/check-axxium-production-hold.sh').read_text()
        text = text.replace('/srv', str(root / 'srv'))
        return subprocess.run(['sh', '-c', text], stdin=subprocess.DEVNULL, capture_output=True, timeout=5)

    def test_each_fixed_hold_or_dangling_alias_blocks_all_canonical_writers(self):
        for service, marker in [('axxium', '.axxium-production-pending'), ('colab-mcp', '.colab-production-pending')]:
            for dangling in (False, True):
                with self.subTest(service=service, dangling=dangling), tempfile.TemporaryDirectory() as temporary:
                    def prepare(root):
                        directory = root / service
                        directory.mkdir()
                        if dangling: (directory / marker).symlink_to(directory / 'absent-target')
                        else: (directory / marker).write_text('synthetic-held-transaction')
                    result = self.guard(temporary, prepare)
                    self.assertEqual(result.returncode, 74)
                    self.assertIn(b'preserve the operation journal', result.stderr)

    def test_missing_axxium_directory_does_not_hide_existing_colab_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            def prepare(root):
                (root / 'colab-mcp').mkdir()
                (root / 'colab-mcp/.colab-production-pending').write_text('hold')
            self.assertEqual(self.guard(temporary, prepare).returncode, 74)

    def test_unheld_services_pass_and_a_service_directory_alias_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.guard(temporary, lambda root: (root / 'colab-mcp').mkdir()).returncode, 0)
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.guard(temporary, lambda root: (root / 'colab-mcp').symlink_to(root / 'absent')).returncode, 74)


if __name__ == '__main__': unittest.main()
