"""Synthetic regressions for the candidate identity transaction.

These tests use only in-memory host doubles and temporary fixture state. They
are not production backup, identity, or deployment evidence.
"""
from contextlib import contextmanager
import contextlib
import fcntl
import hashlib
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
from test_axxium_minimal_release import FakeHost, plan, release
from test_provision_axxium_colab_identity import identity


class TracedHost(FakeHost):
    def __init__(self, fail=None, uncertain=False):
        super().__init__(fail, uncertain)
        self.locked = False

    @contextmanager
    def lock(self):
        if self.locked:
            raise AssertionError('unexpected nested test lock')
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def provision_identity(self, p):
        if not self.locked:
            raise AssertionError('identity provisioning escaped native lock')
        self.call('provision_identity')


class IdentityTransitionRegressions(unittest.TestCase):
    def test_provision_occurs_inside_lock_after_fence_stop_and_verified_backup(self):
        host = TracedHost()
        self.assertEqual(release.execute(plan(), host), {'status': 'healthy'})
        for earlier in ('preflight', 'claim', 'fence', 'stop', 'backup_before'):
            self.assertLess(host.calls.index(earlier), host.calls.index('provision_identity'), earlier)
        self.assertLess(host.calls.index('provision_identity'), host.calls.index('start'))
        self.assertFalse(host.locked)

    def test_failed_preflight_performs_no_provisioning_or_claim(self):
        host = TracedHost('preflight')
        with self.assertRaises(release.KnownFailure):
            release.execute(plan(), host)
        self.assertEqual(host.calls, ['preflight'])
        self.assertNotIn('provision_identity', host.calls)
        self.assertFalse(host.locked)

    def test_failed_verified_backup_never_provisions_identity(self):
        host = TracedHost('backup_before')
        release.execute(plan(), host)
        self.assertNotIn('provision_identity', host.calls)
        self.assertNotIn('start', host.calls)
        self.assertFalse(host.locked)

    def test_uncertain_provision_retains_hold_and_never_rolls_back(self):
        host = TracedHost('provision_identity', uncertain=True)
        with self.assertRaises(release.Uncertain):
            release.execute(plan(), host)
        self.assertNotIn('rollback', host.calls)
        self.assertNotIn('commit', host.calls)
        self.assertNotIn('finish_healthy', host.calls)
        self.assertNotIn('start', host.calls)
        self.assertIn(('provision_identity', 'pending'), host.events)
        self.assertFalse(host.locked)


class DirectProvisionGuardRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.operation = self.root / 'axxium' / 'operations' / identity.SOURCE
        self.operation.mkdir(parents=True, mode=0o700)
        self.lock = self.root / 'lock'
        self.pending = self.root / 'pending'
        self.plan = self.root / 'plan.json'
        self.plan.write_bytes(b'{"synthetic":"fixed-plan"}\n')
        self.plan.chmod(0o600)
        self.lock.write_bytes(b'')
        self.lock.chmod(0o600)
        self.fd = os.open(self.lock, os.O_RDWR)
        self.addCleanup(os.close, self.fd)
        for key, value in {'OWNER_UID': os.getuid(), 'STATE_ROOT': self.root,
                           'LOCK': self.lock, 'PENDING': self.pending, 'PLAN': self.plan}.items():
            item = patch.object(identity, key, value)
            item.start()
            self.addCleanup(item.stop)
        self.write_json(self.pending, {'schema': 'axxium-production-pending/v1',
            'operation': str(self.operation), 'source_sha': identity.SOURCE,
            'plan_sha256': hashlib.sha256(self.plan.read_bytes()).hexdigest()})
        self.write_json(self.operation / '000-verified_before_backup-succeeded.json',
                        {'sequence': 0, 'phase': 'verified_before_backup', 'status': 'succeeded'})
        self.write_json(self.operation / '001-provision_identity-pending.json',
                        {'sequence': 1, 'phase': 'provision_identity', 'status': 'pending'})
        dump = self.operation / 'before.pgdump'
        dump.write_bytes(b'synthetic-backup')
        dump.chmod(0o600)
        self.write_json(self.operation / 'before-backup.json',
            {'path': str(dump), 'sha256': hashlib.sha256(dump.read_bytes()).hexdigest(),
             'database': 'axxium_verify_' + 'a' * 24})

    @staticmethod
    def write_json(path, payload):
        path.write_text(json.dumps(payload))
        path.chmod(0o600)

    def test_direct_provision_without_controller_lock_is_refused(self):
        with self.assertRaises(identity.Refusal):
            identity.require_transaction_context(-1, self.operation)
        with self.assertRaises(identity.Refusal):
            identity.require_transaction_context(self.fd, self.operation)

    def test_matching_live_lock_and_verified_backup_are_required(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity.require_transaction_context(self.fd, self.operation)
        (self.operation / '000-verified_before_backup-succeeded.json').unlink()
        with self.assertRaises(identity.Refusal):
            identity.require_transaction_context(self.fd, self.operation)

    def test_tampered_backup_cannot_authorize_provision(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (self.operation / 'before.pgdump').write_bytes(b'different-synthetic-bytes')
        with self.assertRaises(identity.Refusal):
            identity.require_transaction_context(self.fd, self.operation)

    def test_other_process_lock_does_not_authorize_unlocked_descriptor(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        decoy = os.open(self.lock, os.O_RDWR)
        try:
            with self.assertRaises(identity.Refusal):
                identity.require_transaction_context(decoy, self.operation)
        finally:
            os.close(decoy)

    def test_unordered_or_stale_pending_phase_cannot_authorize_provision(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.write_json(self.operation / '002-fence-succeeded.json',
                        {'sequence': 2, 'phase': 'fence', 'status': 'succeeded'})
        with self.assertRaises(identity.Refusal):
            identity.require_transaction_context(self.fd, self.operation)

    def test_direct_cli_without_lock_fails_without_writes(self):
        capture = io.StringIO()
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        with patch.object(sys, 'argv', ['provision_axxium_colab_identity.py', '--provision']), \
                contextlib.redirect_stdout(capture):
            self.assertEqual(identity.main(), 1)
        self.assertEqual(json.loads(capture.getvalue()), {'verified': False, 'status': 'refused'})
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')))


if __name__ == '__main__':
    unittest.main()
