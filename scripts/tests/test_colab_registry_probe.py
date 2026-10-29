"""The package read probe never builds, publishes, connects to SSH or logs a token."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('colab_read', Path(__file__).parents[1] / 'probe_colab_registry.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
SHA = 'a' * 40
PACKAGE = {'name': 'cloud-cuckoo', 'package_type': 'container', 'owner': {'login': 'open-hax'}, 'visibility': 'private'}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'open-hax/services', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_SHA': SHA, 'GITHUB_RUN_ID': '12345',
            'GITHUB_ACTOR': 'fixture-reader', 'GH_TOKEN': 'fixture-secret', 'GITHUB_TOKEN': 'fixture-secret'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.calls = []
        self.fail_pull = False
        self.wrong_id = False

    def command(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] == 'git':
            return SHA.encode()
        if args[1] == 'pull' and self.fail_pull:
            raise probe.Refusal('fixture-secret-error')
        if args[1:3] == ['image', 'inspect']:
            return json.dumps([{'Id': 'wrong' if self.wrong_id else probe.IMAGE_ID,
                'Config': {'User': '1000:1000', 'Labels': {'org.opencontainers.image.source':
                    'https://github.com/open-hax/cloud-cuckoo', 'org.opencontainers.image.revision': probe.SOURCE}}}]).encode()
        return b''

    def observe(self, read=lambda token: (200, PACKAGE)):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'receipt.json'
            with patch('builtins.print') as printed:
                code = probe.observe(output, run=self.command, read=read)
            text = output.read_text()
            self.assertNotIn('fixture-secret', text)
            self.assertNotIn('fixture-secret', str(printed.call_args_list))
            return code, json.loads(text)

    def test_private_exact_image_uses_only_read_commands_and_ephemeral_auth(self):
        code, receipt = self.observe()
        self.assertEqual(code, 0)
        self.assertTrue(receipt['image_contract_verified'])
        self.assertEqual(receipt['stage'], 'verified')
        self.assertIn(['docker', 'pull', probe.IMAGE], [a for a, _ in self.calls])
        for args, kwargs in self.calls:
            self.assertNotIn('fixture-secret', ' '.join(args))
            if args[0] == 'docker':
                self.assertIn(args[1], {'login', 'pull', 'image', 'logout'})
                self.assertNotIn('GH_TOKEN', kwargs['env'])
                self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
                self.assertFalse(Path(kwargs['env']['DOCKER_CONFIG']).exists())

    def test_inaccessible_metadata_remains_unknown_and_never_attempts_pull(self):
        code, receipt = self.observe(lambda token: (404, None))
        self.assertEqual(code, 2)
        self.assertIsNone(receipt['package_visibility'])
        self.assertFalse(receipt['registry_pull_succeeded'])
        self.assertEqual([a[0] for a, _ in self.calls], ['git'])

    def test_public_and_wrong_package_identity_refuse_before_login(self):
        for change in [{'visibility': 'public'}, {'owner': {'login': 'other'}}, {'name': 'other'}]:
            with self.subTest(change=change):
                self.calls.clear()
                code, receipt = self.observe(lambda token: (200, {**PACKAGE, **change}))
                self.assertEqual(code, 2)
                self.assertEqual(receipt['stage'], 'private_metadata')
                self.assertFalse(any(a[0] == 'docker' for a, _ in self.calls))

    def test_pull_denial_is_recorded_without_claiming_access_and_logs_out(self):
        self.fail_pull = True
        code, receipt = self.observe()
        self.assertEqual(code, 2)
        self.assertFalse(receipt['registry_pull_succeeded'])
        self.assertEqual(receipt['stage'], 'immutable_pull')
        self.assertTrue(any(a[1] == 'logout' for a, _ in self.calls if a[0] == 'docker'))

    def test_wrong_pulled_image_refuses_origin_claim(self):
        self.wrong_id = True
        code, receipt = self.observe()
        self.assertEqual(code, 2)
        self.assertTrue(receipt['registry_pull_succeeded'])
        self.assertFalse(receipt['image_contract_verified'])

    def test_missing_token_never_reaches_metadata_or_registry(self):
        os.environ.pop('GH_TOKEN')
        code, receipt = self.observe(lambda token: self.fail('Metadata request with missing token'))
        self.assertEqual(code, 2)
        self.assertEqual(receipt['stage'], 'native_context')

    def test_topic_branch_refuses_before_any_command(self):
        os.environ['GITHUB_REF'] = 'refs/heads/topic'
        with self.assertRaises(probe.Refusal):
            self.observe()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
