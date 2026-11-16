import fcntl
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import provision_axxium_mailbox_identity as subject


SOURCE = '1' * 40
ACTOR = 'actor_00000000-0000-0000-0000-000000000001'
MAILBOX = 'did:plc:' + 'a' * 24
SEED = b'{:root {:id "err"} :nodes [{:id "err:work"}]}'


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        root = Path(temporary)
        self.state = root / 'state' / 'axxium'
        self.operation = self.state / 'operations' / SOURCE
        self.operation.mkdir(parents=True, mode=0o700)
        oidc = self.state / 'oidc'
        oidc.mkdir(mode=0o700)
        self.authority = oidc / 'authority.json'
        self.binding = oidc / 'production-binding.json'
        self.lock = root / 'lock'
        self.pending = root / 'pending.json'
        self.seed = root / 'seed.edn'
        self.plan = root / 'plan.json'
        self.request = root / 'request.json'
        self.identity = self.operation / 'mailbox-identity'
        self.mailbox = self.state / 'mailbox/ledger.edn'
        self.actor_ledger = self.state / 'identity/ledger.edn'
        self.original = {'issuer': subject.ISSUER, 'cookie-keys': ['x' * 48], 'jwks': {'keys': [{'kid': 'original'}]},
                         'clients': [{'id': subject.incumbent.CLIENT_ID, 'secret': 'y' * 48,
                                      'redirect-uris': [subject.incumbent.CALLBACK], 'refresh-enabled?': True,
                                      'device-enabled?': False}]}
        self.request_value = {'schema': subject.REQUEST_SCHEMA, 'work_mailbox_did': MAILBOX,
                              'work_client_secret': 'z' * 48}
        self.write(self.authority, subject.json_bytes(self.original))
        self.write(self.binding, subject.json_bytes({'schema': 'axxium-colab-production-binding/v1',
                   'issuer': subject.ISSUER, 'actor_id': ACTOR, 'actor_kind': 'human', 'actor_status': 'active',
                   'evidence_sha256': '0' * 64, 'observed_at': '2025-01-01T00:00:00Z'}))
        self.write(self.lock, b'')
        self.write(self.seed, SEED)
        self.write(self.request, subject.json_bytes(self.request_value))
        self.plan_value = {'source_sha': SOURCE, 'identity': {
            'incumbent_authority_sha256': subject.digest(self.authority.read_bytes()),
            'seed_sha256': subject.digest(SEED), 'work_client_id': subject.WORK_CLIENT}}
        self.write(self.plan, subject.json_bytes(self.plan_value))
        self.marker = {'schema': 'axxium-production-pending/v1', 'operation': str(self.operation),
                       'source_sha': SOURCE, 'plan_sha256': subject.digest(self.plan.read_bytes())}
        self.write(self.pending, subject.json_bytes(self.marker))
        self.enterContext(patch.object(subject, 'STATE', self.state))
        self.enterContext(patch.multiple(subject.incumbent, STATE_ROOT=self.state.parent, OWNER_UID=os.getuid(),
                          AUTHORITY=self.authority, BINDING=self.binding, APPROVED_ACTOR=ACTOR,
                          LOCK=self.lock, PENDING=self.pending))
        self.owner = self.enterContext(patch.object(subject, 'existing_google_owner', return_value=True))
        self.work_account = self.enterContext(patch.object(subject, 'existing_atproto_work', return_value=True))

    @staticmethod
    def write(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def invoke(self, **kwargs):
        return subject.provision(self.plan, self.request, self.seed, self.operation, **kwargs)

    def with_lock(self, function):
        with self.lock.open('rb') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            return function(handle.fileno())

    def test_preflight_is_read_only_and_binds_existing_google_owner(self):
        self.assertEqual(self.invoke(), {'verified': True, 'prepared': False})
        self.assertFalse(self.identity.exists())
        self.assertFalse(self.mailbox.exists())
        self.owner.return_value = False
        with self.assertRaisesRegex(subject.Refusal, 'Google owner'):
            self.invoke()

    def test_unbound_work_did_refuses_before_writes(self):
        self.work_account.return_value = False
        with self.assertRaisesRegex(subject.Refusal, 'distinct production Work'):
            self.invoke()
        with self.assertRaisesRegex(subject.Refusal, 'distinct production Work'):
            self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        self.assertFalse(self.identity.exists())
        self.assertFalse(self.mailbox.exists())

    def test_interrupted_key_durable_link_recovers_without_rekey(self):
        self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        key = self.identity / 'owner/identity.key'
        data = key.read_bytes()
        stage = subject.incumbent.stage_path(key)
        os.link(key, stage)
        self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        self.assertEqual(key.read_bytes(), data)
        self.assertFalse(stage.exists())

    def test_prepare_preserves_original_authority_and_retains_keys_and_ledger_bytes(self):
        original = self.authority.read_bytes()
        self.assertEqual(self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd)),
                         {'verified': True, 'prepared': True})
        app_authority = json.loads((self.identity / 'oidc/authority.json').read_bytes())
        self.assertEqual(self.authority.read_bytes(), original)
        self.assertEqual(app_authority['clients'][0], self.original['clients'][0])
        self.assertEqual(app_authority['cookie-keys'], self.original['cookie-keys'])
        self.assertEqual(app_authority['jwks'], self.original['jwks'])
        self.assertEqual(app_authority['clients'][1]['redirect-uris'], [subject.WORK_CALLBACK])
        self.assertEqual(app_authority['mailbox-client-id'], subject.WORK_CLIENT)
        self.assertEqual(app_authority['mailbox-resource'], subject.RESOURCE)
        self.assertEqual((self.identity / 'owner/foresight-tree.edn').read_bytes(), SEED)
        dotenv = (self.identity / 'mailbox.env').read_text()
        self.assertIn(f"AXXIUM_MAILBOX_ACTOR_BINDINGS='[[\"{MAILBOX}\",\"{subject.WORK_ACTOR}\"]]'", dotenv)
        self.assertIn(f"AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS='{subject.CODEX_ACTOR}'", dotenv)
        keys = {path: path.read_bytes() for path in (self.identity / 'actor-signing.pem',
                self.identity / 'owner/identity.key', self.identity / 'oidc/mailbox-webhook.key')}
        self.assertEqual(len(set(keys.values())), 3)
        self.mailbox.write_bytes(b'accepted mailbox bytes\n')
        self.actor_ledger.write_bytes(b'accepted actor bytes\n')
        ledger_id = self.mailbox.stat().st_ino
        self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        self.assertEqual(self.mailbox.stat().st_ino, ledger_id)
        self.assertEqual(self.mailbox.read_bytes(), b'accepted mailbox bytes\n')
        self.assertEqual(self.actor_ledger.read_bytes(), b'accepted actor bytes\n')
        for path, key in keys.items():
            self.assertEqual(path.read_bytes(), key)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_prepare_refuses_without_held_matching_lock_and_marker(self):
        with self.assertRaisesRegex(subject.Refusal, 'inherited production lock'):
            self.invoke(prepare=True)
        with self.lock.open('rb') as handle:
            with self.assertRaisesRegex(subject.Refusal, 'not held'):
                self.invoke(prepare=True, lock_fd=handle.fileno())
        self.write(self.pending, subject.json_bytes({**self.marker, 'plan_sha256': '0' * 64}))
        with self.assertRaisesRegex(subject.Refusal, 'hold differs'):
            self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        self.assertFalse(self.identity.exists())

    def test_preflight_rejects_unclaimed_ledger_or_modified_seed(self):
        self.mailbox.parent.mkdir(mode=0o700)
        self.write(self.mailbox, b'older state')
        with self.assertRaisesRegex(subject.Refusal, 'ledger lacks'):
            self.invoke()
        self.mailbox.unlink()
        self.write(self.seed, b'changed seed')
        with self.assertRaisesRegex(subject.Refusal, 'does not match the plan'):
            self.invoke()

    def test_work_registration_drift_refuses_rekey(self):
        self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        path = self.identity / 'oidc/authority.json'
        previous = path.read_bytes()
        self.write(self.request, subject.json_bytes({**self.request_value, 'work_client_secret': 'q' * 48}))
        with self.assertRaisesRegex(subject.Refusal, 'claim differs'):
            self.with_lock(lambda fd: self.invoke(prepare=True, lock_fd=fd))
        self.assertEqual(path.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
