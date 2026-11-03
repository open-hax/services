"""Native edge regression tests; no inherited duplication of the OS matrix."""
import time
import unittest
from unittest import mock

from scripts.tests import test_local_generation_fs as fixtures
from scripts import local_generation_wire as wire


class EngineEdgeTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LocalGenerationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_clock_rollback_after_durable_intent_holds_before_generation_write(self):
        f = self.fixture
        request = f.request(f.plan('clock'))
        real = time.time
        changed = []
        def hook(event):
            if event == 'durable-intent':
                changed.append(True)
        def clock():
            return (request['plan']['created_at'] - 1000) / 1000 if changed else real()
        f.engine._test_hook = hook
        with mock.patch('time.time', side_effect=clock):
            result = f.engine.execute(request)
        self.assertTrue(changed)
        self.assertEqual(result['status'], 'held', result)
        self.assertEqual(result['first_failure']['code'], 'expiry', result)
        self.assertFalse((f.root / 'current').exists())
        self.assertEqual(list((f.root / 'generations').iterdir()), [])

    def test_replay_denial_reports_actual_observed_selection_not_absence(self):
        f = self.fixture
        request = f.request(f.plan('replay'))
        self.assertEqual(f.engine.execute(request)['status'], 'applied')
        selected = f.selection()
        before = fixtures.fixture_snapshot(f.root)
        result = f.engine.execute(request)
        self.assertEqual(result['status'], 'denied', result)
        self.assertEqual(result['first_failure']['code'], 'replay', result)
        self.assertEqual(result['before'], selected, result)
        self.assertEqual(result['after'], selected, result)
        self.assertEqual(result['target_writes'], 0)
        self.assertEqual(before, fixtures.fixture_snapshot(f.root))

    def test_wire_limit_error_is_distinct_from_shape(self):
        with self.assertRaises(ValueError) as caught:
            wire.encode('too-long', limit=1)
        self.assertEqual(getattr(caught.exception, 'code', None), 'limit')

    def test_compensation_failure_preserves_first_cause_and_durable_hold(self):
        f = self.fixture
        f.apply('a')
        old_tree = fixtures.fixture_snapshot(f.root / 'generations/a')
        request = f.request(f.plan('b'))
        fired = []
        def fail(event):
            if event == 'after:current-rename' and not fired:
                fired.append('selection')
                raise OSError(5, 'fixture selection failure')
            if event == 'before:restoration-file-open' and fired == ['selection']:
                fired.append('compensation')
                raise OSError(28, 'fixture compensation failure')
        f.engine._test_hook = fail
        result = f.engine.execute(request)
        self.assertEqual(fired, ['selection', 'compensation'])
        self.assertEqual(result['status'], 'held', result)
        self.assertIsNone(result['after'])
        self.assertIsNone(result['target_writes'])
        self.assertEqual(result['first_failure']['stage'], 'selecting', result)
        self.assertEqual(result['recovery_failures'][0]['stage'], 'restoring', result)
        self.assertEqual(old_tree, fixtures.fixture_snapshot(f.root / 'generations/a'))
        before = fixtures.fixture_snapshot(f.root)
        with fixtures.fs.Engine(f.policy, f.revision) as fresh:
            calls = []
            fresh._test_hook = calls.append
            held = fresh.recover()
        self.assertEqual(held['status'], 'held', held)
        self.assertEqual(calls, [])
        self.assertEqual(before, fixtures.fixture_snapshot(f.root))

    def test_historical_expiry_does_not_block_capsule_only_compensation(self):
        f = self.fixture
        f.apply('a')
        before = f.selection()
        f.kill_selection()
        future = time.time() + 3600
        with mock.patch('time.time', return_value=future):
            with fixtures.fs.Engine(f.policy, f.revision) as fresh:
                result = fresh.recover()
        self.assertEqual(result['status'], 'failed-restored', result)
        self.assertEqual(f.selection(), before)

    def test_full_sixteen_component_artifact_path_has_bounded_internal_paths(self):
        f = self.fixture
        seed = f.seed('deep')
        artifact = seed['manifest']['artifacts'][0]
        artifact['path'] = ['d%02d' % n for n in range(15)] + ['config.edn']
        preview = f.engine.preview(seed)
        self.assertEqual(preview['status'], 'previewed', preview)
        result = f.engine.execute(f.request(preview['plan']))
        self.assertEqual(result['status'], 'applied', result)
        path = f.root / 'generations/deep/payload'
        for part in artifact['path']:
            path = path / part
        self.assertEqual(path.read_bytes(), b'{:fixture/value "deep"}\n')

    def test_unqualified_bash_is_unavailable_not_a_fake_validator_verdict(self):
        f = self.fixture
        seed = f.seed('bash')
        seed['manifest']['artifacts'][0]['format'] = 'bash-env'
        before = fixtures.fixture_snapshot(f.root)
        with mock.patch.object(fixtures.fs.edn, 'validate', side_effect=AssertionError('no Bash validator')):
            result = f.engine.preview(seed)
        self.assertEqual(result['status'], 'unavailable', result)
        self.assertEqual(result['first_failure']['code'], 'unsupported', result)
        self.assertEqual(result['target_writes'], 0)
        self.assertEqual(before, fixtures.fixture_snapshot(f.root))

    def test_unknown_closed_journal_field_holds_before_new_admission(self):
        f = self.fixture
        f.apply('a')
        request = f.request(f.plan('b'))
        journal = next((f.root / 'transactions').iterdir()) / 'journal'
        value = fixtures.json.loads(journal.read_bytes())
        value['unknown'] = True
        journal.write_bytes(fixtures.json_bytes(value))
        before = fixtures.fixture_snapshot(f.root)
        preview = f.engine.preview(f.seed('b'))
        result = f.engine.execute(request)
        self.assertEqual(preview['status'], 'held', preview)
        self.assertEqual(result['status'], 'held', result)
        self.assertEqual(preview['recovery'], 'uncertain', preview)
        self.assertEqual(result['recovery'], 'uncertain', result)
        self.assertEqual(preview['target_writes'], 0)
        self.assertEqual(result['target_writes'], 0)
        self.assertEqual(before, fixtures.fixture_snapshot(f.root))

    def test_restored_apply_intent_does_not_authorize_foreign_retained_generation(self):
        f = self.fixture
        f.apply('a')
        request = f.request(f.plan('b'))
        fired = []
        def fail(event):
            if event == 'before:candidate-mkdir' and not fired:
                fired.append(event)
                raise OSError(5, 'fixture before candidate creation')
        f.engine._test_hook = fail
        self.assertEqual(f.engine.execute(request)['status'], 'failed-restored')
        f.engine._test_hook = None
        # A fully valid foreign tree still is not a *retained committed* tree.
        # A failed transaction's old intent cannot supply ownership authority.
        root = f.root / 'generations/b'
        root.mkdir(mode=0o700)
        (root / 'payload').mkdir(mode=0o700)
        manifest = request['plan']['manifest']
        (root / 'manifest').write_bytes(fixtures.json_bytes(manifest))
        (root / 'manifest').chmod(0o600)
        payload = root / 'payload/config.edn'
        payload.write_bytes(fixtures.base64.b64decode(manifest['artifacts'][0]['bytes_b64']))
        payload.chmod(0o600)
        before = fixtures.fixture_snapshot(f.root)
        result = f.engine.preview(f.seed('b', 'rollback'))
        self.assertIn(result['status'], ('denied', 'held'), result)
        self.assertEqual(result['target_writes'], 0)
        self.assertEqual(before, fixtures.fixture_snapshot(f.root))

    def test_distinct_full_length_nonces_do_not_alias_transaction_names(self):
        f = self.fixture
        for generation, nonce in (('a', 'n' + 'a' * 63), ('b', 'n' + 'a' * 62 + 'b')):
            seed = f.seed(generation)
            seed['nonce'] = nonce
            preview = f.engine.preview(seed)
            self.assertEqual(preview['status'], 'previewed', preview)
            result = f.engine.execute(f.request(preview['plan']))
            self.assertEqual(result['status'], 'applied', result)
        self.assertEqual(f.selection()['generation'], 'b')
        self.assertEqual(len(list((f.root / 'transactions').iterdir())), 2)

    def test_post_rename_identity_is_derived_from_owned_source_not_adopted(self):
        for target in ('current', 'journal'):
            f = fixtures.LocalGenerationTest()
            f.setUp()
            try:
                f.apply('a')
                old = fixtures.fixture_snapshot(f.root / 'generations/a')
                request = f.request(f.plan('b'))
                original = fixtures.os.rename
                replaced = []
                def rename(src, dst, **kwargs):
                    result = original(src, dst, **kwargs)
                    if dst == target and not replaced:
                        path = (f.root / 'current' if target == 'current' else
                                f.root / 'transactions' / f.engine.tx / 'journal')
                        temporary = path.parent / 'fixture-replacement'
                        temporary.write_bytes(path.read_bytes())
                        temporary.chmod(0o600)
                        fixtures.os.replace(temporary, path)
                        replaced.append((path, fixtures.native_identity(path)))
                    return result
                with mock.patch.object(fixtures.os, 'rename', side_effect=rename):
                    result = f.engine.execute(request)
                self.assertTrue(replaced)
                self.assertEqual(result['status'], 'held', (target, result))
                self.assertIsNone(result['after'])
                self.assertEqual(fixtures.native_identity(replaced[0][0]), replaced[0][1], target)
                self.assertEqual(old, fixtures.fixture_snapshot(f.root / 'generations/a'), target)
            finally:
                f.doCleanups()
