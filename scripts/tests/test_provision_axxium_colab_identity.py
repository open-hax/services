"""Private provisioning decisions use fixtures; no service or identity is created."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'provision_axxium_colab_identity.py'
spec = importlib.util.spec_from_file_location('provision_identity', SCRIPT)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
NATIVE_CRYPTO = identity.native_crypto
ACTOR = 'actor_00000000-0000-4000-8000-000000000001'


class PrivateIdentityProvisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['node', '-e', identity.CRYPTO, 'generate'],
                                input=b'', capture_output=True, timeout=45, check=True)
        cls.key = json.loads(result.stdout)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.oidc = self.root / 'oidc'
        self.oidc.mkdir(mode=0o700)
        self.authority = self.oidc / 'authority.json'
        self.binding_path = self.oidc / 'production-binding.json'
        self.gateway = self.root / 'colab-mcp' / 'axxium-client.json'
        self.plan = self.root / 'plan.json'
        self.controller_binding = self.root / 'production-binding.json'
        self.binding = {'schema': 'axxium-colab-production-binding/v1', 'issuer': identity.ISSUER,
                        'actor_id': ACTOR, 'actor_kind': 'human', 'actor_status': 'active',
                        'evidence_sha256': 'e' * 64, 'observed_at': '2026-01-01T00:00:00Z'}
        self.write(self.binding_path, self.binding)
        self.write(self.plan, {'schema': 'axxium-minimal-production/v1', 'image_id': 'sha256:' + 'a' * 64})
        for name, value in [('APPROVED_ACTOR', ACTOR), ('OWNER_UID', os.getuid()), ('STATE_ROOT', self.root), ('AUTHORITY', self.authority),
                            ('BINDING', self.binding_path), ('GATEWAY_CLIENT', self.gateway), ('PLAN', self.plan),
                            ('CONTROLLER_BINDING_SOURCE', self.controller_binding)]:
            item = patch.object(identity, name, value)
            item.start()
            self.addCleanup(item.stop)
        self.human = patch.object(identity, 'existing_human_verified', return_value=True).start()
        self.addCleanup(patch.stopall)
        self.crypto = patch.object(identity, 'native_crypto', side_effect=self.local_crypto).start()
        self.context = patch.object(identity, 'require_transaction_context').start()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def local_crypto(self, mode, value=None):
        if mode == 'generate':
            return copy.deepcopy(self.key)
        result = subprocess.run(['node', '-e', identity.CRYPTO, 'check'], input=json.dumps(value).encode(),
                                capture_output=True, timeout=15)
        if result.returncode or result.stdout != b'true':
            raise identity.Refusal('Native private signing-key validation failed')

    def test_complete_new_pair_is_private_source_shaped_and_idempotent(self):
        self.assertEqual(identity.provision(), {'verified': True, 'status': 'created'})
        before = [path.read_bytes() for path in (self.authority, self.gateway)]
        authority = json.loads(before[0])
        gateway = json.loads(before[1])
        self.assertEqual(set(authority), {'issuer', 'clients', 'cookie-keys', 'jwks'})
        client = authority['clients'][0]
        self.assertEqual(client['id'], 'knoxx-colab')
        self.assertEqual(client['redirect-uris'], [identity.CALLBACK])
        self.assertEqual(gateway['allowed_subjects'], [ACTOR])
        self.assertEqual(gateway['client_secret'], client['secret'])
        for path in (self.authority, self.gateway):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
        self.assertEqual(identity.check(), {'verified': True, 'status': 'complete'})
        self.assertEqual(identity.provision(), {'verified': True, 'status': 'unchanged'})
        self.assertEqual(before, [path.read_bytes() for path in (self.authority, self.gateway)])
        self.assertEqual(sum(call.args[0] == 'generate' for call in self.crypto.call_args_list), 1)

    def test_require_existing_reuses_complete_identity_without_any_write(self):
        identity.provision()
        paths = (self.binding_path, self.authority, self.gateway)
        before = [(path.stat().st_ino, path.read_bytes()) for path in paths]
        self.crypto.reset_mock()
        with patch.object(identity, 'publish_identity_file', side_effect=AssertionError('unexpected write')):
            self.assertEqual(identity.provision(require_existing=True),
                             {'verified': True, 'status': 'unchanged'})
        self.assertEqual(before, [(path.stat().st_ino, path.read_bytes()) for path in paths])
        self.assertFalse(any(call.args[0] == 'generate' for call in self.crypto.call_args_list))

    def test_require_existing_refuses_ready_state_without_writes(self):
        with patch.object(identity, 'publish_identity_file', side_effect=AssertionError('unexpected write')):
            with self.assertRaisesRegex(identity.Refusal, 'complete existing identity'):
                identity.provision(require_existing=True)
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())
        self.crypto.assert_not_called()

    def test_require_existing_refuses_recoverable_transaction_without_repair(self):
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.gateway:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision()
        files = [p for p in self.root.rglob('*') if p.is_file()]
        before = {p: p.read_bytes() for p in files}
        self.crypto.reset_mock()
        with patch.object(identity, 'publish_identity_file', side_effect=AssertionError('unexpected repair')):
            with self.assertRaisesRegex(identity.Refusal, 'complete existing identity'):
                identity.provision(require_existing=True)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse(any(call.args[0] == 'generate' for call in self.crypto.call_args_list))

    def test_require_existing_requires_valid_transaction_context(self):
        with patch.object(identity, 'require_transaction_context', side_effect=identity.Refusal('no lock')):
            with self.assertRaises(identity.Refusal):
                identity.provision(require_existing=True)
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())

    def test_missing_or_unverified_human_binding_writes_nothing(self):
        self.human.return_value = False
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())
        self.crypto.assert_not_called()
        self.binding_path.unlink()
        with self.assertRaises(FileNotFoundError):
            identity.provision()

    def test_owner_attestation_requires_stable_human_and_evidence(self):
        for update in [{'actor_id': 'human@example.invalid'}, {'actor_kind': 'service'},
                       {'actor_status': 'disabled'}, {'evidence_sha256': ''},
                       {'issuer': 'https://other.invalid/oidc'}, {'observed_at': '2099-01-01T00:00:00Z'},
                       {'unexpected': 'value'}]:
            with self.subTest(update=update):
                self.write(self.binding_path, {**self.binding, **update})
                with self.assertRaises(identity.Refusal):
                    identity.provision()
                self.assertFalse(self.authority.exists())
        self.crypto.assert_not_called()

    def test_distinct_active_human_cannot_replace_approved_actor(self):
        other = 'actor_00000000-0000-4000-8000-000000000002'
        self.write(self.binding_path, {**self.binding, 'actor_id': other})
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())
        self.crypto.assert_not_called()

    def test_incoming_mismatched_actor_cannot_install_binding(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, {**self.binding,
            'actor_id': 'actor_00000000-0000-4000-8000-000000000002'})
        with self.assertRaises(identity.Refusal):
            identity.provision(self.controller_binding)
        self.assertFalse(self.binding_path.exists())
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())
        self.crypto.assert_not_called()

    def test_preflight_is_read_only_for_first_install(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, self.binding)
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(identity.preflight(self.controller_binding),
                         {'verified': True, 'status': 'ready'})
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(self.gateway.parent.exists())
        self.crypto.assert_not_called()

    def test_preflight_rejects_other_active_human_without_writes(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, {**self.binding,
            'actor_id': 'actor_00000000-0000-4000-8000-000000000002'})
        with self.assertRaises(identity.Refusal):
            identity.preflight(self.controller_binding)
        self.assertFalse(self.binding_path.exists())
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.parent.exists())
        self.crypto.assert_not_called()

    def test_partial_pair_is_preserved_and_never_repaired(self):
        self.write(self.authority, {'partial': 'preserve-exactly'})
        before = self.authority.read_bytes()
        with self.assertRaisesRegex(identity.Refusal, 'Partial'):
            identity.provision()
        self.assertEqual(self.authority.read_bytes(), before)
        self.assertFalse(self.gateway.exists())
        self.crypto.assert_not_called()

    def test_owned_interrupted_pair_resumes_identical_material_and_is_idempotent(self):
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.gateway:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision()
        authority_bytes = self.authority.read_bytes()
        self.assertTrue(identity.transaction_path().exists())
        self.assertFalse(self.gateway.exists())
        self.assertEqual(identity.provision()['status'], 'created')
        self.assertEqual(self.authority.read_bytes(), authority_bytes)
        gateway_bytes = self.gateway.read_bytes()
        self.assertFalse(identity.transaction_path().exists())
        self.assertEqual(identity.provision()['status'], 'unchanged')
        self.assertEqual(self.gateway.read_bytes(), gateway_bytes)
        self.assertEqual(sum(call.args[0] == 'generate' for call in self.crypto.call_args_list), 1)

    def test_owned_interrupted_binding_resumes_same_prepared_transaction(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, self.binding)
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.binding_path:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision(self.controller_binding)
        self.assertTrue(identity.transaction_path().exists())
        self.assertFalse(self.binding_path.exists())
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'created')
        self.assertEqual(self.binding_path.read_bytes(), self.controller_binding.read_bytes())
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'unchanged')
        self.assertEqual(sum(call.args[0] == 'generate' for call in self.crypto.call_args_list), 1)

    def test_owned_interrupted_authority_resumes_same_prepared_transaction(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, self.binding)
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.authority:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision(self.controller_binding)
        self.assertTrue(identity.transaction_path().exists())
        before = self.binding_path.read_bytes()
        self.assertFalse(self.authority.exists())
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'created')
        self.assertEqual(self.binding_path.read_bytes(), before)
        self.assertEqual(sum(call.args[0] == 'generate' for call in self.crypto.call_args_list), 1)

    def test_partial_stage_bytes_are_preserved_for_manual_recovery(self):
        writer = identity.write_new_bytes
        interrupted_stage = identity.stage_path(self.gateway)
        def interrupted(path, value):
            if path == interrupted_stage:
                writer(path, b'partial-synthetic-data')
                raise OSError('synthetic partial stage write')
            writer(path, value)
        with patch.object(identity, 'write_new_bytes', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision()
        self.assertTrue(identity.transaction_path().exists())
        self.assertEqual(interrupted_stage.read_bytes(), b'partial-synthetic-data')
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.assertEqual(interrupted_stage.read_bytes(), b'partial-synthetic-data')
        self.assertTrue(identity.transaction_path().exists())
        self.assertEqual(sum(call.args[0] == 'generate' for call in self.crypto.call_args_list), 1)

    def exercise_interruption_after_link(self, target_name):
        self.binding_path.unlink()
        self.write(self.controller_binding, self.binding)
        targets = {'journal': identity.transaction_path(), 'binding': self.binding_path,
                   'authority': self.authority, 'gateway': self.gateway}
        target = targets[target_name]
        original_link = os.link
        def interrupted(source, dest, **kwargs):
            original_link(source, dest, **kwargs)
            if Path(dest) == target:
                raise OSError('synthetic interruption after link')
        with patch.object(identity.os, 'link', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision(self.controller_binding)
        self.assertTrue(target.exists())
        self.assertTrue(identity.stage_path(target).exists())
        published = target.read_bytes()
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'created')
        if target != identity.transaction_path():
            self.assertEqual(target.read_bytes(), published)
        self.assertFalse(identity.stage_path(target).exists())
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'unchanged')
        self.assertEqual(sum(call.args[0] == 'generate' for call in self.crypto.call_args_list), 1)

    def test_interrupted_journal_link_recovers_idempotently(self):
        self.exercise_interruption_after_link('journal')

    def test_interrupted_binding_link_recovers_idempotently(self):
        self.exercise_interruption_after_link('binding')

    def test_interrupted_authority_link_recovers_idempotently(self):
        self.exercise_interruption_after_link('authority')

    def test_interrupted_gateway_link_recovers_idempotently(self):
        self.exercise_interruption_after_link('gateway')

    def test_changed_partial_material_refuses_owned_retry_without_overwrite(self):
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.gateway:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision()
        self.write(self.authority, {'foreign': 'preserve'})
        before = self.authority.read_bytes()
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.assertEqual(self.authority.read_bytes(), before)
        self.assertFalse(self.gateway.exists())

    def test_unowned_stage_without_journal_is_refused_and_preserved(self):
        stage = identity.stage_path(self.authority)
        stage.write_bytes(b'unknown-existing-private-material')
        stage.chmod(0o600)
        before = stage.read_bytes()
        self.assertFalse(identity.transaction_path().exists())
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.assertEqual(stage.read_bytes(), before)
        self.assertFalse(identity.transaction_path().exists())
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())
        self.crypto.assert_not_called()

    def test_preflight_refuses_conflicting_target_stage_before_fence(self):
        self.write(self.controller_binding, self.binding)
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.gateway:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision(self.controller_binding)
        stage = identity.stage_path(self.gateway)
        stage.write_bytes(b'foreign-private-fixture')
        stage.chmod(0o600)
        before = stage.read_bytes()
        with self.assertRaises(identity.Refusal):
            identity.preflight(self.controller_binding)
        self.assertEqual(stage.read_bytes(), before)
        self.assertFalse(self.gateway.exists())

    def test_preflight_refuses_conflicting_journal_stage_before_fence(self):
        self.write(self.controller_binding, self.binding)
        publish = identity.publish_identity_file
        def interrupted(path, value):
            if path == self.gateway:
                raise OSError('synthetic interruption')
            publish(path, value)
        with patch.object(identity, 'publish_identity_file', side_effect=interrupted):
            with self.assertRaises(OSError):
                identity.provision(self.controller_binding)
        stage = identity.stage_path(identity.transaction_path())
        stage.write_bytes(b'foreign-private-fixture')
        stage.chmod(0o600)
        before = stage.read_bytes()
        with self.assertRaises(identity.Refusal):
            identity.preflight(self.controller_binding)
        self.assertEqual(stage.read_bytes(), before)
        self.assertTrue(identity.transaction_path().exists())

    def test_existing_material_never_overwrites_drift(self):
        identity.provision()
        data = json.loads(self.gateway.read_text())
        data['client_secret'] = 'changed-private-fixture-' + 'x' * 40
        self.write(self.gateway, data)
        before = [path.read_bytes() for path in (self.authority, self.gateway)]
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.assertEqual(before, [path.read_bytes() for path in (self.authority, self.gateway)])

    def test_wrong_source_schema_and_bad_private_key_fail(self):
        identity.provision()
        authority = json.loads(self.authority.read_text())
        gateway = json.loads(self.gateway.read_text())
        for field in ['clients', 'cookie-keys', 'jwks']:
            changed = copy.deepcopy(authority)
            changed[field.replace('-', '_') + '_wrong'] = changed.pop(field)
            with self.subTest(field=field), self.assertRaises(identity.Refusal):
                identity.validate_pair(changed, gateway, self.binding)
        changed = copy.deepcopy(authority)
        changed['jwks']['keys'][0]['d'] = 'invalid'
        with self.assertRaises(identity.Refusal):
            identity.validate_pair(changed, gateway, self.binding)

    def test_private_modes_symlinks_and_duplicate_fields_are_refused(self):
        self.binding_path.chmod(0o644)
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.binding_path.chmod(0o600)
        target = self.oidc / 'binding-backup'
        self.binding_path.rename(target)
        self.binding_path.symlink_to(target)
        with self.assertRaises(identity.Refusal):
            identity.provision()
        self.binding_path.unlink()
        self.binding_path.write_text('{"issuer":"one","issuer":"two"}')
        self.binding_path.chmod(0o600)
        with self.assertRaises(identity.Refusal):
            identity.provision()

    def test_exclusive_write_never_changes_an_existing_file(self):
        self.write(self.authority, {'old': 'private-fixture'})
        before = self.authority.read_bytes()
        with self.assertRaises(FileExistsError):
            identity.write_new(self.authority, {'new': 'different'})
        self.assertEqual(self.authority.read_bytes(), before)

    def test_special_configuration_file_is_refused_without_blocking(self):
        self.binding_path.unlink()
        os.mkfifo(self.binding_path, mode=0o600)
        with self.assertRaises(identity.Refusal):
            identity.provision()

    def test_native_crypto_command_is_isolated_and_keeps_key_off_arguments(self):
        seen = []
        def runner(command, **kwargs):
            seen.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=b'true', stderr=b'')
        NATIVE_CRYPTO('check', self.key, runner=runner)
        command, options = seen[0]
        self.assertEqual(command[:4], ['docker', 'run', '--rm', '-i'])
        self.assertEqual(command[command.index('--network') + 1], 'none')
        self.assertIn('--read-only', command)
        self.assertEqual(command[command.index('--pull') + 1], 'never')
        self.assertIn('--cap-drop', command)
        self.assertNotIn('--volume', command)
        self.assertEqual(command[command.index('--entrypoint') + 1], 'node')
        self.assertIn('sha256:' + 'a' * 64, command)
        self.assertNotIn(self.key['d'], ' '.join(command))
        self.assertEqual(json.loads(options['input']), self.key)
        self.assertEqual(options['timeout'], 45)

    def test_missing_admitted_image_refuses_without_pull_or_fallback(self):
        calls = []
        def unavailable(command, **options):
            calls.append(command)
            return SimpleNamespace(returncode=125, stdout=b'', stderr=b'private-fixture-detail')
        with self.assertRaises(identity.Refusal) as raised:
            NATIVE_CRYPTO('check', self.key, runner=unavailable)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index('--pull') + 1], 'never')
        self.assertNotIn('private-fixture-detail', str(raised.exception))

    def test_cli_refuses_with_safe_json_and_no_secret_values(self):
        identity.provision()
        secret = json.loads(self.gateway.read_text())['client_secret']
        self.human.return_value = False
        output = io.StringIO()
        with patch('sys.argv', [str(SCRIPT), '--check']), contextlib.redirect_stdout(output):
            self.assertEqual(identity.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {'verified': False, 'status': 'refused'})
        self.assertNotIn(secret, output.getvalue())

    def test_binding_delivery_installs_only_fixed_path_and_retries_identically(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, self.binding)
        source_bytes = self.controller_binding.read_bytes()
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'created')
        self.assertEqual(self.binding_path.read_bytes(), source_bytes)
        self.assertEqual(identity.provision(self.controller_binding)['status'], 'unchanged')
        self.assertEqual(self.binding_path.read_bytes(), source_bytes)

    def test_binding_delivery_refuses_reformatted_or_different_existing_binding(self):
        self.write(self.controller_binding, self.binding)
        before = self.binding_path.read_bytes()
        self.controller_binding.write_text(json.dumps(self.binding, indent=2))
        with self.assertRaises(identity.Refusal):
            identity.provision(self.controller_binding)
        self.assertEqual(self.binding_path.read_bytes(), before)
        self.assertFalse(self.authority.exists())

    def test_binding_input_has_no_arbitrary_path_or_actor_override(self):
        other = self.root / 'unadmitted.json'
        self.write(other, self.binding)
        with self.assertRaises(identity.Refusal):
            identity.provision(other)
        self.assertFalse(self.authority.exists())
        with patch.object(identity.os, 'geteuid', return_value=os.getuid() + 1):
            with self.assertRaises(identity.Refusal):
                identity.provision()

    def test_read_only_check_never_installs_binding_input(self):
        self.binding_path.unlink()
        self.write(self.controller_binding, self.binding)
        output = io.StringIO()
        with patch('sys.argv', [str(SCRIPT), '--check', '--binding-input', str(self.controller_binding)]), contextlib.redirect_stdout(output):
            self.assertEqual(identity.main(), 1)
        self.assertFalse(self.binding_path.exists())
        self.assertFalse(self.authority.exists())

    def test_require_existing_option_is_only_for_provision(self):
        for mode in ('--check', '--preflight'):
            with self.subTest(mode=mode):
                args = [str(SCRIPT), mode, '--require-existing']
                if mode == '--preflight':
                    self.write(self.controller_binding, self.binding)
                    args += ['--binding-input', str(self.controller_binding)]
                output = io.StringIO()
                with patch('sys.argv', args), contextlib.redirect_stdout(output):
                    self.assertEqual(identity.main(), 1)
                self.assertEqual(json.loads(output.getvalue()), {'verified': False, 'status': 'refused'})
                self.assertFalse(self.authority.exists())

    def test_provision_cli_passes_require_existing_flag(self):
        output = io.StringIO()
        args = [str(SCRIPT), '--provision', '--require-existing']
        with patch('sys.argv', args), contextlib.redirect_stdout(output):
            self.assertEqual(identity.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {'verified': False, 'status': 'refused'})
        self.assertFalse(self.authority.exists())
        self.assertFalse(self.gateway.exists())


class ExistingHumanProbeTests(unittest.TestCase):
    def test_fixed_read_only_probe_returns_boolean(self):
        seen = []
        def runner(command, **kwargs):
            seen.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=b't\n', stderr=b'')
        self.assertTrue(identity.existing_human_verified({'actor_id': ACTOR}, runner))
        command, kwargs = seen[0]
        self.assertEqual(command[:4], ['docker', 'exec', '-i', 'axxium-axxium-db-1'])
        query = kwargs['input'].decode()
        self.assertTrue(query.startswith('SELECT EXISTS('))
        self.assertIn('JOIN entities e ON e.id=a.entity_id', query)
        self.assertIn("e.kind='human'", query)
        self.assertIn("a.status='active'", query)
        self.assertNotIn('INSERT', query)
        self.assertFalse(identity.existing_human_verified({'actor_id': "x' OR TRUE--"}, runner))
        self.assertEqual(len(seen), 1)
        for result in [SimpleNamespace(returncode=1, stdout=b't', stderr=b'private-error'),
                       SimpleNamespace(returncode=0, stdout=b'f', stderr=b''),
                       SimpleNamespace(returncode=0, stdout=b't\nt', stderr=b'')]:
            self.assertFalse(identity.existing_human_verified({'actor_id': ACTOR}, lambda *a, **kw: result))


if __name__ == '__main__':
    unittest.main()
