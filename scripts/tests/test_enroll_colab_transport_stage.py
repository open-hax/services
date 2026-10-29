"""Stage enrollment preserves private authority and refuses partial recovery."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enroll_colab_transport_stage as m
import colab_transport_stage as stage


def authority():
    return {'issuer': m.ISSUER, 'allow-loopback?': True, 'device-flow-enabled?': True,
            'clients': [{'id': 'knoxx-colab-staging', 'secret': 'existing-private-secret-' * 2,
                         'redirect-uris': ['http://127.0.0.1:18761/auth/callback']}],
            'cookie-keys': ['existing-private-cookie-' * 2],
            'jwks': {'keys': [{'kty': 'RSA', 'alg': 'RS256', 'd': 'existing-private-key'}]},
            'node-grants': []}


class EnrollmentLaws(unittest.TestCase):
    def test_production_enrollment_owner_is_closed_at_uid1000(self):
        self.assertEqual(m.OWNER_UID, 1000)
        self.assertEqual(stage.OWNER_UID, 1000)

    def test_only_one_new_client_and_no_existing_semantics_change(self):
        old = authority()
        snapshot = copy.deepcopy(old)
        new, gateway, added = m.plan_pair(old, None, 'distinct-private-client-' * 2)
        self.assertTrue(added)
        self.assertEqual(old, snapshot)
        self.assertEqual(new['clients'][:-1], snapshot['clients'])
        self.assertEqual({k: v for k, v in new.items() if k != 'clients'},
                         {k: v for k, v in snapshot.items() if k != 'clients'})
        self.assertEqual(gateway['allowed_subjects'], [m.ACTOR])
        self.assertEqual(gateway['allowed_client_redirect_uris'], [m.REDIRECT])
        self.assertEqual(new['clients'][-1]['redirect-uris'], [m.CALLBACK])
        self.assertEqual(m.plan_pair(new, gateway), (new, gateway, False))

    def test_partial_or_mismatched_pair_refuses_without_repair(self):
        new, gateway, _ = m.plan_pair(authority(), None, 'distinct-private-client-' * 2)
        for a, g in [(new, None), (authority(), gateway),
                     (new, {**gateway, 'client_secret': 'different-private-secret-' * 2}),
                     (new, {**gateway, 'allowed_subjects': ['actor-unowned']})]:
            with self.subTest(pair=(bool(a), bool(g))), self.assertRaises(Exception):
                m.plan_pair(a, g)

    def test_existing_issuer_loopback_and_unique_clients_are_required(self):
        for mutate in [lambda a: a.update(issuer='https://axxium.promethean.rest/oidc'),
                       lambda a: a.update({'allow-loopback?': False}),
                       lambda a: a['clients'].append(copy.deepcopy(a['clients'][0])),
                       lambda a: a.update(unknown=True),
                       lambda a: a['clients'][0].update({'allow-loopback?': True})]:
            with self.subTest(mutate=mutate):
                a = authority()
                mutate(a)
                with self.assertRaises(Exception):
                    m.plan_pair(a, None, 'distinct-private-client-' * 2)

    def test_secret_is_never_reused_from_incumbent_clients_or_cookies(self):
        a = authority()
        for secret in [a['clients'][0]['secret'], a['cookie-keys'][0], 'short']:
            with self.assertRaises(Exception):
                m.plan_pair(a, None, secret)

    def test_duplicate_json_fields_refuse(self):
        with self.assertRaises(Exception):
            m.decode(b'{"issuer":"one","issuer":"two"}')


class EnrollmentTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.slot = self.root / 'environments/staging/axxium'
        self.directory = self.slot / 'oidc'
        self.directory.mkdir(parents=True)
        self.directory.chmod(0o700)
        self.state = self.root / 'state/colab-transport-stage'
        self.state.parent.mkdir()
        self.paths = {'ENVIRONMENTS': self.root / 'environments', 'SLOT': self.slot,
                      'AUTHORITY': self.directory / 'authority.json',
                      'BACKUP': self.directory / 'authority.before.json',
                      'MARKER': self.directory / 'enrollment.json', 'STATE': self.state,
                      'GATEWAY': self.state / 'axxium-client.json'}
        self.raw = (json.dumps(authority(), indent=3) + '\n').encode()
        self.paths['AUTHORITY'].write_bytes(self.raw)
        self.paths['AUTHORITY'].chmod(0o600)
        self.before = {'container_id': 'a' * 64, 'image_id': 'sha256:' + 'b' * 64, 'source_sha': 'c' * 40}
        self.calls = []
        self.patchers = [patch.object(m, key, value) for key, value in self.paths.items()]
        # Sandbox files belong to the ordinary runner UID. Root-only chowns
        # are observed without executing them; actual mode/regular/symlink,
        # byte backup, exclusive creation and refusal checks remain active.
        self.patchers += [patch.object(m, 'OWNER_UID', os.getuid()),
                          patch.object(stage, 'OWNER_UID', os.getuid()),
                          patch.object(m.os, 'chown'), patch.object(m.os, 'fchown')]
        self.patchers += [patch.object(m, 'require_identity'), patch.object(m, 'actor_verified'),
                          patch.object(m, 'installed_stage', return_value=self.before),
                          patch.object(m, 'issuer_ready'), patch.object(m, 'run', side_effect=self.command),
                          patch.object(m, 'receiver_lock', self.lock)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.temporary.cleanup()

    @contextmanager
    def lock(self):
        self.calls.append('lock')
        yield
        self.calls.append('unlock')

    def command(self, args, **kwargs):
        self.calls.append(args)
        return b''

    def test_backup_exact_pair_permissions_and_only_frozen_stage_restart(self):
        result = m.enroll()
        self.assertTrue(result['verified'])
        self.assertTrue(result['added'])
        self.assertEqual(self.paths['BACKUP'].read_bytes(), self.raw)
        self.assertEqual(self.calls, ['lock', ['docker', 'restart', '--time', '610', 'a' * 64], 'unlock'])
        self.assertEqual(self.paths['MARKER'].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.paths['GATEWAY'].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('sha256', json.dumps(result))

    def test_complete_readonly_check_and_idempotent_enrollment_do_not_restart(self):
        m.enroll()
        original = {p: p.read_bytes() for key, p in self.paths.items()
                    if key in {'AUTHORITY', 'BACKUP', 'MARKER', 'GATEWAY'}}
        self.calls.clear()
        self.assertFalse(m.enroll(check=True)['added'])
        self.assertFalse(m.enroll()['stage_restarted'])
        self.assertEqual(self.calls, ['lock', 'unlock', 'lock', 'unlock'])
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_check_cannot_install_an_absent_pair(self):
        with self.assertRaises(Exception):
            m.enroll(check=True)
        self.assertEqual(self.paths['AUTHORITY'].read_bytes(), self.raw)
        self.assertFalse(self.paths['BACKUP'].exists())
        self.assertFalse(self.state.exists())

    def test_dangling_gateway_symlink_refuses_before_any_backup_or_restart(self):
        self.state.mkdir(mode=0o700)
        self.paths['GATEWAY'].symlink_to('missing-private-file')
        with self.assertRaises(Exception):
            m.enroll()
        self.assertEqual(self.calls, ['lock'])
        self.assertFalse(self.paths['BACKUP'].exists())

    def test_uncertain_restart_retains_pending_and_cannot_replay(self):
        with patch.object(m, 'run', side_effect=RuntimeError('uncertain')):
            with self.assertRaises(RuntimeError):
                m.enroll()
        self.assertEqual(json.loads(self.paths['MARKER'].read_text())['status'], 'pending')
        before = self.paths['AUTHORITY'].read_bytes()
        with self.assertRaises(Exception):
            m.enroll()
        self.assertEqual(self.paths['AUTHORITY'].read_bytes(), before)
        self.assertEqual(self.paths['BACKUP'].read_bytes(), self.raw)

    def test_receiver_selection_drift_refuses_before_authority_replacement(self):
        with patch.object(m, 'installed_stage', side_effect=[self.before, {**self.before, 'container_id': 'd' * 64}]):
            with self.assertRaises(Exception):
                m.enroll()
        self.assertEqual(self.paths['AUTHORITY'].read_bytes(), self.raw)
        self.assertEqual(json.loads(self.paths['MARKER'].read_text())['status'], 'pending')

    def test_changed_completed_pair_refuses_without_overwriting(self):
        m.enroll()
        gateway = self.paths['GATEWAY'].read_bytes()
        self.paths['AUTHORITY'].write_bytes(self.raw)
        with self.assertRaises(Exception):
            m.enroll(check=True)
        self.assertEqual(self.paths['GATEWAY'].read_bytes(), gateway)

    def test_unknown_backup_without_marker_is_a_partial_refusal(self):
        self.paths['BACKUP'].write_bytes(b'prior-recovery-evidence')
        self.paths['BACKUP'].chmod(0o600)
        with self.assertRaises(Exception):
            m.enroll()
        self.assertEqual(self.paths['AUTHORITY'].read_bytes(), self.raw)
        self.assertEqual(self.paths['BACKUP'].read_bytes(), b'prior-recovery-evidence')


if __name__ == '__main__':
    unittest.main()
