"""Closed unit observations for the native Events profile adapter, not host proof."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_active_mailbox_release as active
import axxium_events_profile as profile
import axxium_events_release as events
import axxium_minimal_release as native


IMAGE = 'sha256:' + 'a' * 64
OTHER_IMAGE = 'sha256:' + 'b' * 64
INCUMBENT = '1' * 64
CANDIDATE = '2' * 64
FOREIGN = '3' * 64
RECIPIENT = 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa'
SOURCE = '4' * 40
CLEAR = {name: 0 for name in profile.CALLBACK_COUNTS}


class EventsHostTests(unittest.TestCase):
    def setUp(self):
        self.plan = deepcopy(active.PLAN)
        self.plan.update(source_sha=SOURCE, image_id=IMAGE, recipient_did=RECIPIENT)
        self.plan['expected'].update(app_container_id=INCUMBENT, app_image_id=IMAGE)
        self.plan['incumbent'].update(source_sha=SOURCE, image_id=IMAGE,
                                      app_container_id=INCUMBENT)
        self.plan_hash = sha256((json.dumps(self.plan, sort_keys=True, separators=(',', ':'))
                                + '\n').encode()).hexdigest()
        self.before = {'name': 'axxium', 'services': {'axxium': {
            'image': IMAGE,
            'environment': {profile.STREAM: 'true', profile.SUBSCRIPTIONS: 'false',
                            profile.RECIPIENT: RECIPIENT,
                            profile.SEALING_FILE: '/run/axxium/oidc/mailbox-webhook.key',
                            'AXXIUM_IDENTITY_ROOT_DID': 'did:web:fixture.example:actors:owner',
                            'UNSET': None},
            'volumes': [
                {'type': 'bind', 'source': '/fixture/mailbox.edn',
                 'target': '/var/lib/axxium/mailbox/ledger.edn', 'read_only': False},
                {'type': 'bind', 'source': '/fixture/authority.json',
                 'target': '/run/axxium/oidc/authority.json', 'read_only': True}],
        }}, 'networks': {'database': {'internal': True}}}
        self.after = deepcopy(self.before)
        self.after['services']['axxium']['environment'].update({
            profile.SUBSCRIPTIONS: 'true', profile.COEXISTENCE: 'true'})

    def host(self):
        host = events.EventsHost(self.plan, self.plan_hash, mock.Mock(return_value=dict(CLEAR)))
        # These unit observations are closed: an unexpected command is a test
        # failure, never an opportunity to contact Docker, a ledger or a host.
        host.command = mock.Mock(side_effect=AssertionError('unexpected native command'))
        host.projections = mock.Mock(return_value=(deepcopy(self.before), deepcopy(self.after)))
        return host

    def selected(self, *, candidate=False, running=False, identity=None):
        service = (self.after if candidate else self.before)['services']['axxium']
        environment = {'PATH': '/image/default', 'UNSET': 'from-image'}
        for key, value in service['environment'].items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        return {'Id': identity or (CANDIDATE if candidate else INCUMBENT), 'Image': IMAGE,
                'State': {'Running': running, 'Health': {'Status': 'healthy'}},
                'Config': {'Env': [key + '=' + value for key, value in environment.items()]},
                'Mounts': [{'Type': row['type'], 'Source': row['source'],
                            'Destination': row['target'], 'RW': not row.get('read_only', False)}
                           for row in service['volumes']]}

    def startup(self, *, initial=None, selected=None, known_candidate=None):
        host = self.host()
        host.operation = Path('/fixture/task-owned-operation')
        host.candidate_id = known_candidate
        host.unchanged_dependencies = mock.Mock()
        host.original_layer = mock.Mock()
        host.verify_held_census = mock.Mock()
        host.private_write = mock.Mock()
        host.compose = mock.Mock()
        host.verify_active_runtime = mock.Mock()
        host.inspect = mock.Mock(side_effect=[initial or self.selected(),
                                              selected or self.selected(candidate=True, running=True)])
        return host

    def test_profile_operation_is_distinct_from_the_same_source_image_upgrade(self):
        host = self.host()
        self.assertEqual(host.operation_name(self.plan), SOURCE + '-mailbox-events')
        self.assertNotEqual(host.operation_name(self.plan), SOURCE + '-active-mailbox')
        host.command.assert_not_called()

    def test_first_start_checks_stopped_incumbent_and_census_before_any_compose_effect(self):
        host = self.startup()
        observed = []
        host.verify_held_census.side_effect = lambda *_: observed.append('census')
        host.compose.side_effect = lambda *_args, **_kwargs: observed.append('compose')
        host.start_candidate(self.plan)
        self.assertEqual(observed, ['census', 'compose'])
        self.assertEqual(host.candidate_id, CANDIDATE)
        host.unchanged_dependencies.assert_called()
        host.original_layer.assert_called_once_with(self.plan)
        host.verify_held_census.assert_called_once_with(self.plan)
        host.verify_active_runtime.assert_called_once_with(
            self.selected(candidate=True, running=True), candidate=True)
        self.assertEqual(host.compose.call_count, 1)
        self.assertEqual(host.compose.call_args.args[:6],
                         ('up', '-d', '--no-deps', '--pull', 'never', 'axxium'))
        host.command.assert_not_called()

    def test_first_start_refuses_a_running_incumbent_before_census_or_compose(self):
        host = self.startup(initial=self.selected(running=True))
        with self.assertRaises((native.Refused, ValueError)):
            host.start_candidate(self.plan)
        host.verify_held_census.assert_not_called()
        host.compose.assert_not_called()
        host.private_write.assert_not_called()

    def test_first_start_refuses_foreign_stopped_container_or_wrong_image(self):
        for changed in ({'Id': FOREIGN}, {'Image': OTHER_IMAGE}):
            initial = self.selected()
            initial.update(changed)
            host = self.startup(initial=initial)
            with self.subTest(changed=changed), self.assertRaises((native.Refused, ValueError)):
                host.start_candidate(self.plan)
            host.compose.assert_not_called()
            host.private_write.assert_not_called()

    def test_later_start_requires_known_stopped_candidate_not_original_or_foreign(self):
        for identity in (INCUMBENT, FOREIGN):
            host = self.startup(initial=self.selected(candidate=True, identity=identity),
                                known_candidate=CANDIDATE)
            with self.subTest(identity=identity), self.assertRaises((native.Refused, ValueError)):
                host.start_candidate(self.plan)
            host.compose.assert_not_called()
        host = self.startup(initial=self.selected(candidate=True), known_candidate=CANDIDATE)
        host.start_candidate(self.plan)
        host.original_layer.assert_not_called()
        host.verify_held_census.assert_called_once_with(self.plan)
        host.compose.assert_called_once()

    def test_census_or_dependency_refusal_prevents_compose(self):
        for seam in ('unchanged_dependencies', 'verify_held_census'):
            host = self.startup()
            getattr(host, seam).side_effect = native.Refused('closed fixture refusal')
            with self.subTest(seam=seam), self.assertRaises(native.Refused):
                host.start_candidate(self.plan)
            host.compose.assert_not_called()

    def test_first_start_requires_actual_recreation_and_expected_running_image(self):
        cases = [self.selected(candidate=True, running=True, identity=INCUMBENT),
                 {**self.selected(candidate=True, running=True), 'Image': OTHER_IMAGE},
                 self.selected(candidate=True, running=False)]
        for selected in cases:
            host = self.startup(selected=selected)
            with self.subTest(selected=selected), self.assertRaises((native.Refused, ValueError)):
                host.start_candidate(self.plan)

    def test_runtime_verification_selects_profiles_even_when_image_ids_are_equal(self):
        host = self.host()
        host.inspect = mock.Mock(return_value={'Config': {'Env': [
            'PATH=/image/default', 'UNSET=from-image']}})
        for candidate in (False, True):
            selected = self.selected(candidate=candidate)
            host.verify_active_runtime(selected, candidate=candidate)
            with self.subTest(wrong_profile=candidate), self.assertRaises((native.Refused, ValueError)):
                host.verify_active_runtime(selected, candidate=not candidate)
        host.command.assert_not_called()

    def test_held_census_checks_current_bytes_recipient_and_all_snapshot_prefixes(self):
        host = self.host()
        host.operation = Path('/fixture/task-owned-operation')
        host.pending_bytes = b'closed fixture hold'
        host.recheck_lock = mock.Mock()
        base = {'mailbox': b'accepted\n', 'identity': b'identity\n'}
        after = {'mailbox': b'accepted\nacknowledged\n', 'identity': b'identity\nbinding\n'}
        host.ledger_snapshots = {'before': base, 'after': after}
        with mock.patch.object(active, 'ledger_bytes', side_effect=[after, after]) as read:
            host.verify_held_census(self.plan)
        self.assertEqual(read.call_count, 2)
        host.census.assert_called_once_with(after['mailbox'], RECIPIENT)
        self.assertEqual(host.recheck_lock.call_count, 2)
        host.command.assert_not_called()

    def test_held_census_requires_a_hold_operation_and_before_backup_before_read(self):
        for field in ('pending_bytes', 'operation', 'ledger_snapshots'):
            host = self.host()
            host.operation = Path('/fixture/task-owned-operation')
            host.pending_bytes = b'closed fixture hold'
            host.recheck_lock = mock.Mock()
            host.ledger_snapshots = {'before': {'mailbox': b'accepted\n', 'identity': b'identity\n'}}
            setattr(host, field, {} if field == 'ledger_snapshots' else None)
            with mock.patch.object(active, 'ledger_bytes') as read:
                with self.subTest(field=field), self.assertRaises((native.Refused, ValueError)):
                    host.verify_held_census(self.plan)
            read.assert_not_called()
            host.census.assert_not_called()

    def test_held_census_refuses_rewritten_prefix_before_callback_and_changed_readback_after_it(self):
        base = {'mailbox': b'accepted\n', 'identity': b'identity\n'}
        rewritten = {'mailbox': b'rewritten\n', 'identity': b'identity\n'}
        grown = {'mailbox': b'accepted\nunexpected-write\n', 'identity': b'identity\n'}
        for reads, expected_calls in (([rewritten], 0), ([base, grown], 1)):
            host = self.host()
            host.operation = Path('/fixture/task-owned-operation')
            host.pending_bytes = b'closed fixture hold'
            host.recheck_lock = mock.Mock()
            host.ledger_snapshots = {'before': base}
            with mock.patch.object(active, 'ledger_bytes', side_effect=reads):
                with self.subTest(reads=reads), self.assertRaises((native.Refused, ValueError)):
                    host.verify_held_census(self.plan)
            self.assertEqual(host.census.call_count, expected_calls)

    def test_held_census_nonzero_missing_and_boolean_counts_refuse(self):
        base = {'mailbox': b'accepted\n', 'identity': b'identity\n'}
        variants = [{**CLEAR, 'due-callbacks': 1}, {**CLEAR, 'due-callbacks': False},
                    {name: value for name, value in CLEAR.items() if name != 'due-callbacks'}]
        for counts in variants:
            host = self.host()
            host.operation = Path('/fixture/task-owned-operation')
            host.pending_bytes = b'closed fixture hold'
            host.recheck_lock = mock.Mock()
            host.ledger_snapshots = {'before': base}
            host.census.return_value = counts
            with mock.patch.object(active, 'ledger_bytes', return_value=base):
                with self.subTest(counts=counts), self.assertRaises((native.Refused, ValueError)):
                    host.verify_held_census(self.plan)

    def test_changed_existing_candidate_overlay_refuses_without_census_or_compose(self):
        host = self.startup()
        with mock.patch.object(Path, 'exists', return_value=True), \
                mock.patch.object(native, 'private_read', return_value=b'changed-overlay'):
            with self.assertRaises((native.Refused, ValueError)):
                host.start_candidate(self.plan)
        host.verify_held_census.assert_not_called()
        host.compose.assert_not_called()
        host.private_write.assert_not_called()

    def test_execute_requires_exact_admitted_host_plan_before_transaction(self):
        host = self.host()
        changed = deepcopy(self.plan)
        changed['recipient_did'] = 'did:plc:bbbbbbbbbbbbbbbbbbbbbbbb'
        with mock.patch.object(native, 'execute_admitted') as execute:
            with self.assertRaises(ValueError):
                events.execute_admitted(changed, host)
            execute.assert_not_called()
            events.execute_admitted(self.plan, host)
            execute.assert_called_once_with(self.plan, host)

    def test_runtime_verification_refuses_image_environment_mount_and_permissions_drift(self):
        host = self.host()
        host.inspect = mock.Mock(return_value={'Config': {'Env': [
            'PATH=/image/default', 'UNSET=from-image']}})
        variants = []
        selected = self.selected(candidate=True)
        selected['Image'] = OTHER_IMAGE
        variants.append(selected)
        selected = self.selected(candidate=True)
        selected['Config']['Env'].append('EXTRA_CONFIG=unexpected')
        variants.append(selected)
        selected = self.selected(candidate=True)
        selected['Mounts'][0]['Source'] = '/fixture/foreign.edn'
        variants.append(selected)
        selected = self.selected(candidate=True)
        selected['Mounts'][1]['RW'] = True
        variants.append(selected)
        for selected in variants:
            with self.subTest(selected=selected), self.assertRaises((native.Refused, ValueError)):
                host.verify_active_runtime(selected, candidate=True)
        host.command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
