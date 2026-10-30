"""Contract checks for the synthetic recovery incident."""
from contextlib import redirect_stderr
import io
import types
import unittest

import full_recovery_probe as fixture


class FullRecoveryProbeTests(unittest.TestCase):
    def test_pending_marker_matches_native_exact_bytes(self):
        marker = {'schema': 'axxium-production-pending/v1', 'operation': '/fixture/operation',
                  'source_sha': 'abc', 'plan_sha256': 'def'}
        self.assertEqual(fixture.encode_hold(marker),
            b'{"operation": "/fixture/operation", "plan_sha256": "def", '
            b'"schema": "axxium-production-pending/v1", "source_sha": "abc"}')


class Blocked(RuntimeError):
    pass


class GuardDiagnosticsTests(unittest.TestCase):
    def exercise(self, message):
        def verify(*args, **kwargs):
            raise Blocked(message)
        incident = types.SimpleNamespace(verify=verify, Blocked=Blocked)
        fixture.enable_fixture_guard_diagnostics(incident)
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(Blocked):
            incident.verify('unused')
        return output.getvalue()

    def test_known_static_reason_is_reported(self):
        self.assertIn('Production hold differs', self.exercise('Production hold differs'))

    def test_unknown_reason_is_redacted(self):
        output = self.exercise('fixture-secret-do-not-print')
        self.assertIn('unclassified guard refusal', output)
        self.assertNotIn('fixture-secret-do-not-print', output)

    def test_success_returns_without_output(self):
        incident = types.SimpleNamespace(verify=lambda value: value, Blocked=Blocked)
        fixture.enable_fixture_guard_diagnostics(incident)
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(incident.verify('ok'), 'ok')
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
