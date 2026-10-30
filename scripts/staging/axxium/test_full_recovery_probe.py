"""Synthetic-only diagnostics must preserve refusals and redact unknown text."""
from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('full_recovery_probe_test', HERE / 'full_recovery_probe.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


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
        output = self.exercise('Production hold differs')
        self.assertIn('Production hold differs', output)

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
