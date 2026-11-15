import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_ingress_host as host
import axxium_ingress_native as native
import axxium_ingress_transaction as journal


def observation(state, runtime):
    return journal.Observation(state, hashlib.sha256(state.encode()).hexdigest(), "e" * 64, runtime)


class IngressHostTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.paths = journal.JournalPaths(root / "runtime", root / "state")
        self.paths.runtime.mkdir(mode=0o700)
        self.paths.state.mkdir(mode=0o700)
        self.state = "shared_live"
        self.runtime = "d" * 64
        self.adapter = Mock()
        self.adapter.observe.side_effect = lambda _host, _states: observation(self.state, self.runtime)
        self.operation = "a" * 32
        self.plan = "b" * 64

    def run_action(self, action, phase=None):
        return host.execute(self.adapter, self.operation, self.plan, action, phase, paths=self.paths)

    def test_pending_phase_is_settled_by_readback_after_interruption(self):
        self.run_action("start")
        marker = self.paths.pending.stat().st_ino, self.paths.pending.read_bytes()

        def interrupt(_host, _phase):
            self.state = "shared_fenced"
            raise InterruptedError("simulated process loss")

        self.adapter.effect.side_effect = interrupt
        with self.assertRaises(InterruptedError):
            self.run_action("step", "fence_shared")
        with self.assertRaisesRegex(journal.Held, "Reconcile the pending phase"):
            self.run_action("step", "fence_shared")
        self.state = "shared_live"
        with self.assertRaisesRegex(journal.Held, "independently settled"):
            self.run_action("reconcile")
        self.state = "unknown"
        with self.assertRaisesRegex(journal.Held, "did not establish an admitted state"):
            self.run_action("reconcile")
        self.state = "shared_fenced"
        self.runtime = "f" * 64
        with self.assertRaisesRegex(journal.Held, "Runtime changed"):
            self.run_action("reconcile")
        self.runtime = "d" * 64
        self.assertEqual("verified", self.run_action("reconcile")["status"])
        self.assertEqual(1, self.adapter.effect.call_count)
        self.assertEqual(marker, (self.paths.pending.stat().st_ino, self.paths.pending.read_bytes()))

    def test_production_entry_rejects_an_adapter_bound_to_another_plan(self):
        self.adapter.operation_id = self.operation
        self.adapter.plan_sha256.return_value = "c" * 64
        with patch.object(native.os, "getuid", return_value=1000), patch.object(native.controller, "execute") as run:
            with self.assertRaisesRegex(journal.Held, "inputs differ"):
                native.execute(self.adapter, self.operation, self.plan, "start")
            run.assert_not_called()

    def test_production_route_plan_preserves_other_routes_and_the_rollback_fence(self):
        base = b"import /etc/caddy/managed/*.caddy\n"
        inline = native.SITE + native.UPSTREAM + b"}\n"
        shared = base + inline
        fenced = shared.replace(native.UPSTREAM, native.MAINTENANCE, 1)
        dedicated = inline.replace(native.UPSTREAM, native.DEDICATED_UPSTREAM, 1)
        digest = "c" * 64
        routes = {
            "shared_live": native.RouteSelection(shared, None, digest),
            "shared_fenced": native.RouteSelection(fenced, None, digest),
            "dedicated_live": native.RouteSelection(base, dedicated, digest),
            "dedicated_fenced": native.RouteSelection(
                base, dedicated.replace(native.DEDICATED_UPSTREAM, native.MAINTENANCE, 1), digest
            ),
            "restored_fenced": native.RouteSelection(fenced, None, digest),
            "restored_live": native.RouteSelection(shared, None, digest),
        }
        native._validate_routes(routes)
        routes["restored_fenced"] = routes["restored_live"]
        with self.assertRaisesRegex(journal.Held, "outside the admitted Axxium"):
            native._validate_routes(routes)
