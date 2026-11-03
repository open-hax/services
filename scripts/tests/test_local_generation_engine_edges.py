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
