"""Unit checks for the disposable native Events fixture, without Docker."""
from contextlib import redirect_stderr
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import events_restart_fixture as runner


IMAGE = 'sha256:' + 'a' * 64
INCUMBENT_ID = 'b' * 64
CANDIDATE_ID = 'c' * 64
APP = runner.native.APP
PROBE = 'axxium-verify-' + 'd' * 24


def runtime(container_id, *, events=False):
    profile = runner.profile
    environment = [f'{profile.STREAM}=true', f'{profile.RECIPIENT}={runner.base.DID}',
                   f'{profile.SUBSCRIPTIONS}={"true" if events else "false"}']
    if events:
        environment.append(f'{profile.COEXISTENCE}=true')
    return {'Id': container_id, 'Image': IMAGE, 'Config': {'Env': environment}, 'State': {'Running': True}}


class RuntimeRoleTests(TestCase):
    def setUp(self):
        self.fixture = runner.Fixture(IMAGE)

    def test_live_roles_have_the_same_image_but_require_the_expected_id_and_flags(self):
        self.fixture.roles[APP] = ('incumbent', INCUMBENT_ID)
        self.assertEqual(self.fixture.runtime_role(APP, runtime(INCUMBENT_ID)), 'incumbent')
        with self.assertRaisesRegex(RuntimeError, 'wrong Events profile'):
            self.fixture.runtime_role(APP, runtime(INCUMBENT_ID, events=True))

        self.fixture.roles[APP] = ('candidate', CANDIDATE_ID)
        self.assertEqual(self.fixture.runtime_role(APP, runtime(CANDIDATE_ID, events=True)), 'candidate')
        with self.assertRaisesRegex(RuntimeError, 'runtime identity differs'):
            self.fixture.runtime_role(APP, runtime(INCUMBENT_ID, events=True))
        with self.assertRaisesRegex(RuntimeError, 'wrong Events profile'):
            self.fixture.runtime_role(APP, runtime(CANDIDATE_ID))

    def test_candidate_probe_role_does_not_enable_callback_dispatch(self):
        self.fixture.roles[PROBE] = ('candidate', None)
        self.assertEqual(self.fixture.runtime_role(PROBE, runtime(CANDIDATE_ID)), 'candidate')
        with self.assertRaisesRegex(RuntimeError, 'wrong Events profile'):
            self.fixture.runtime_role(PROBE, runtime(CANDIDATE_ID, events=True))
        with self.assertRaisesRegex(RuntimeError, 'no expected role'):
            self.fixture.runtime_role(PROBE + 'other', runtime(CANDIDATE_ID))

    def test_stream_recipient_coexistence_and_nonrunning_drift_refuse_wire_reads(self):
        self.fixture.roles[APP] = ('incumbent', INCUMBENT_ID)
        current = runtime(INCUMBENT_ID)
        for name, expected in ((runner.profile.STREAM, 'false'), (runner.profile.RECIPIENT, 'did:plc:other')):
            modified = deepcopy(current)
            modified['Config']['Env'] = [row for row in modified['Config']['Env'] if not row.startswith(name + '=')]
            modified['Config']['Env'].append(f'{name}={expected}')
            with self.subTest(name=name), mock.patch.object(self.fixture, 'owned', return_value=modified), \
                    mock.patch.object(self.fixture, 'tool') as tool:
                with self.assertRaises(RuntimeError):
                    self.fixture.verify(APP)
                tool.assert_not_called()
        for changed in ({**current, 'State': {'Running': False}},
                        {**current, 'Config': {'Env': current['Config']['Env'] + [runner.profile.COEXISTENCE + '=false']}}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.fixture.runtime_role(APP, changed)


class FixtureGuardTests(TestCase):
    def test_pinned_census_uses_fixed_plan_hashes_and_fixed_artifact_names(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / runner.CENSUS_PLAN
            plan_path.parent.mkdir(parents=True)
            module_sha256, driver_sha256 = 'a' * 64, 'b' * 64
            plan_path.write_text(json.dumps({'census': {
                'module_sha256': module_sha256, 'driver_sha256': driver_sha256,
                'unrelated': 'must never be forwarded',
            }}))
            artifact_dir = root / 'artifact'
            with mock.patch.object(runner.active, 'ROOT', root), mock.patch.object(runner, 'PinnedCensus') as pin:
                projector, observed_digest = runner.load_census(artifact_dir)
            pin.assert_called_once_with(artifact_dir / 'probe.js', artifact_dir / 'probe.mjs',
                                        module_sha256=module_sha256, driver_sha256=driver_sha256)
            self.assertIs(projector, pin.return_value)
            self.assertEqual(observed_digest, module_sha256)

    def test_census_projects_only_the_preserved_ledger_and_recipient(self):
        counts = {'retained-subscriptions': 1}
        projector = mock.Mock(return_value=counts)
        census = runner.FixtureCensus(projector, b'fixture\n')
        ledger_bytes = b'fixture\nsupplement\n'
        self.assertIs(census(ledger_bytes, runner.base.DID), counts)
        projector.assert_called_once_with(ledger_bytes, runner.base.DID)
        self.assertEqual(census.reads, 1)
        for ledger, did in ((b'', runner.base.DID), (b'other\n', runner.base.DID), (b'fixture\n', 'did:plc:other')):
            with self.subTest(ledger=ledger, did=did), self.assertRaises(RuntimeError):
                census(ledger, did)
        projector.assert_called_once()
        projector.side_effect = runner.native.Refused('parser refused')
        with self.assertRaisesRegex(runner.native.Refused, 'parser refused'):
            census(ledger_bytes, runner.base.DID)
        self.assertEqual(census.reads, 1)

    def test_main_reuses_hosted_guard_and_verify_archive_before_topology_effects(self):
        argv = ['events_restart_fixture.py', '--archive', '/fixture/candidate.zip', '--scenario', 'healthy',
                '--census-dir', '/fixture/census']
        with mock.patch('sys.argv', argv), \
                mock.patch.object(runner.base, 'require_hosted_fixture', side_effect=RuntimeError('guard')), \
                mock.patch.object(runner.base, 'verify_role') as verify, \
                mock.patch.object(runner.base, 'setup_fixture') as setup:
            with self.assertRaisesRegex(RuntimeError, 'guard'):
                runner.main()
            verify.assert_not_called()
            setup.assert_not_called()

        with mock.patch('sys.argv', argv), mock.patch.object(runner.base, 'require_hosted_fixture'), \
                mock.patch.object(runner.base, 'verify_role', side_effect=ValueError('archive')), \
                mock.patch.object(runner.base, 'setup_fixture') as setup:
            with self.assertRaisesRegex(ValueError, 'archive'):
                runner.main()
            setup.assert_not_called()

    def test_missing_census_refuses_before_runtime_observation_or_setup(self):
        argv = ['events_restart_fixture.py', '--archive', '/fixture/candidate.zip', '--scenario', 'healthy']
        with mock.patch('sys.argv', argv), redirect_stderr(StringIO()), \
                mock.patch.object(runner.base, 'require_hosted_fixture') as guard:
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            guard.assert_not_called()

        with TemporaryDirectory() as directory, mock.patch('sys.argv', argv + ['--census-dir', directory]), \
                mock.patch.object(runner.base, 'require_hosted_fixture'), \
                mock.patch.object(runner.base, 'verify_role'), \
                mock.patch.object(runner.base.wire, 'image_id') as image_id, \
                mock.patch.object(runner.base, 'setup_fixture') as setup:
            with self.assertRaisesRegex(runner.native.Refused, 'artifact custody'):
                runner.main()
            image_id.assert_not_called()
            setup.assert_not_called()

    def test_failure_is_injected_after_observed_candidate_and_restore_checks_the_old_profile(self):
        fixture = runner.Fixture(IMAGE)
        plan = {'image_id': IMAGE, 'source_sha': 'e' * 40}
        host = runner.Host(plan, fixture, 'uncertain', mock.Mock())
        host.candidate_id = CANDIDATE_ID
        with mock.patch.object(runner.events.EventsHost, 'start_candidate') as start, \
                mock.patch.object(fixture, 'owned', return_value=runtime(CANDIDATE_ID, events=True)):
            with self.assertRaises(runner.base.ExpectedUncertainty):
                host.start_candidate(plan)
        start.assert_called_once_with(plan)
        self.assertEqual(host.started, [CANDIDATE_ID])
        self.assertTrue(host.injected)

        with mock.patch.object(runner.events.EventsHost, 'verify_rollback') as verify:
            with self.assertRaisesRegex(RuntimeError, 'retained the Events container identity'):
                host.verify_rollback(plan, runtime(CANDIDATE_ID))
            with self.assertRaisesRegex(RuntimeError, 'wrong Events profile'):
                host.verify_rollback(plan, runtime(INCUMBENT_ID, events=True))
            verify.assert_not_called()
            restored = runtime(INCUMBENT_ID)
            host.verify_rollback(plan, restored)
            verify.assert_called_once_with(plan, restored)
