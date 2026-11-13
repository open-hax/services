import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_ingress_transaction as ingress


OPERATION = "a" * 32
PLAN = "b" * 64


def observe(state, *, state_identity=None, evidence="first"):
    return ingress.Observation(state, hashlib.sha256((state_identity or state).encode()).hexdigest(),
                               hashlib.sha256(evidence.encode()).hexdigest())


class IngressJournalTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        runtime, state = base / "runtime", base / "state"
        runtime.mkdir()
        state.mkdir()
        self.lock = runtime / ".axxium-production.lock"
        self.hold = runtime / ".axxium-production-pending"
        self.operation = state / "ingress-operations" / OPERATION
        fixture = patch.multiple(ingress.production, LOCK=self.lock, PENDING=self.hold, STATE=state)
        fixture.start()
        self.addCleanup(fixture.stop)

    def open(self, initial=None, plan=PLAN):
        return ingress.open_journal(OPERATION, plan, observe_initial=(lambda: initial) if initial else None)

    def step(self, journal, phase, target):
        journal.begin(phase, journal.current)
        self.assertEqual("verified", journal.reconcile(observe(target)))

    def test_restart_recovers_verified_target_without_replaying_effect(self):
        with self.open(observe("shared_live")) as journal:
            self.assertEqual("live", journal.exposure)
            journal.begin("fence_shared", observe("shared_live", evidence="fresh"))
            self.assertEqual("unknown", journal.exposure)
            self.assertTrue(self.hold.is_file())
            self.assertEqual("intent", json.loads((self.operation / "phase-001.json").read_bytes())["action"])
        with self.open() as journal:
            self.assertEqual("fence_shared", journal.pending)
            self.assertEqual("verified", journal.reconcile(observe("shared_fenced")))
            self.assertEqual("fenced", journal.exposure)
            self.step(journal, "select_dedicated", "dedicated_fenced")
            self.step(journal, "publish_dedicated", "dedicated_live")
        with self.open() as journal:
            journal.finish(observe("dedicated_live", evidence="fresh terminal check"))
        self.assertFalse(self.hold.exists())
        self.assertTrue((self.operation / "phase-007.json").exists())

    def test_source_can_only_be_recorded_as_not_applied_when_fingerprint_agrees(self):
        with self.open(observe("shared_live")) as journal:
            journal.begin("fence_shared", observe("shared_live"))
        with self.open() as journal:
            for uncertain in (observe("unknown"), observe("shared_live", state_identity="proxy changed")):
                with self.assertRaisesRegex(ingress.Held, "remains uncertain"):
                    journal.reconcile(uncertain)
                self.assertEqual("fence_shared", journal.pending)
            self.assertEqual("not_applied", journal.reconcile(observe("shared_live", evidence="fresh")))
            self.assertTrue(self.hold.is_file())
            self.step(journal, "fence_shared", "shared_fenced")

    def test_pending_mixed_state_and_early_finish_leave_hold(self):
        with self.open(observe("shared_live")) as journal:
            self.step(journal, "fence_shared", "shared_fenced")
            journal.begin("select_dedicated", journal.current)
        with self.open() as journal:
            with self.assertRaisesRegex(ingress.Held, "remains uncertain"):
                journal.reconcile(observe("dedicated_live"))
            with self.assertRaisesRegex(ingress.Held, "transition refused"):
                journal.finish(observe("dedicated_live"))
        self.assertTrue(self.hold.is_file())

    def test_rollback_fences_published_candidate_and_allows_new_shared_peer(self):
        with self.open(observe("shared_live", state_identity="old peer")) as journal:
            self.step(journal, "fence_shared", "shared_fenced")
            self.step(journal, "select_dedicated", "dedicated_fenced")
            self.step(journal, "publish_dedicated", "dedicated_live")
            self.step(journal, "fence_dedicated", "dedicated_fenced")
            journal.begin("restore_shared", journal.current)
            journal.reconcile(observe("restored_fenced", state_identity="new peer"))
            journal.begin("publish_restored", journal.current)
        with self.open() as journal:
            journal.reconcile(observe("restored_live", state_identity="new peer"))
            journal.finish(observe("restored_live", state_identity="new peer", evidence="fresh"))
        self.assertFalse(self.hold.exists())

    def test_lock_and_foreign_or_mismatched_hold_refuse(self):
        with self.open(observe("shared_live")):
            with self.assertRaisesRegex(ingress.production.Refused, "already in progress"), self.open():
                self.fail("concurrent owner entered the transaction")
        with self.assertRaisesRegex(ingress.Held, "not this exact"), self.open(plan="c" * 64):
            self.fail("wrong plan opened the held operation")
        self.hold.write_text('{"schema":"axxium-production-pending/v1"}')
        with self.assertRaisesRegex(ingress.Held, "not this exact"), self.open():
            self.fail("legacy hold opened the migration journal")

    def test_open_failure_before_hold_publication_allows_no_effect_or_id_reuse(self):
        real = ingress.publish_private

        def stop_before_hold(path, data, **options):
            if path == self.hold:
                raise OSError("simulated hold publication failure")
            return real(path, data, **options)

        with patch.object(ingress, "publish_private", side_effect=stop_before_hold):
            with self.assertRaisesRegex(OSError, "hold publication failure"), self.open(observe("shared_live")):
                self.fail("caller obtained the journal before its hold was published")
        self.assertFalse(self.hold.exists())
        self.assertTrue((self.operation / "phase-000.json").exists())
        with self.assertRaises(FileExistsError), self.open(observe("shared_live")):
            self.fail("caller reused the orphaned operation ID")

    def test_torn_anchor_recovers_and_missing_last_event_refuses(self):
        with self.open(observe("shared_live")) as journal:
            real = ingress.publish_private

            def stop_before_anchor(path, data, **options):
                if path.name == "head.json":
                    raise OSError("simulated interruption")
                return real(path, data, **options)

            with patch.object(ingress, "publish_private", side_effect=stop_before_anchor):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    journal.begin("fence_shared", journal.current)
        with self.open() as journal:
            self.assertEqual("fence_shared", journal.pending)
            self.assertEqual("not_applied", journal.reconcile(observe("shared_live")))
        (self.operation / "phase-002.json").unlink()
        with self.assertRaisesRegex(ingress.Held, "head differs"), self.open():
            self.fail("truncated journal opened")
        self.assertTrue(self.hold.is_file())

    def test_terminal_record_survives_hold_release_interruption(self):
        with self.open(observe("shared_live")) as journal:
            self.step(journal, "fence_shared", "shared_fenced")
            self.step(journal, "select_dedicated", "dedicated_fenced")
            self.step(journal, "publish_dedicated", "dedicated_live")
            sync = ingress.production.NativeHost.sync_directory

            def fail_release_once(path):
                if path == self.hold.parent and not self.hold.exists():
                    raise OSError("simulated interruption")
                return sync(path)

            with patch.object(ingress.production.NativeHost, "sync_directory", side_effect=fail_release_once):
                with self.assertRaisesRegex(ingress.Held, "release is uncertain"):
                    journal.finish(observe("dedicated_live"))
        self.assertTrue(self.hold.is_file())
        with self.open() as journal:
            self.assertTrue(journal.terminal)
            with self.assertRaisesRegex(ingress.Held, "observation changed"):
                journal.finish(observe("dedicated_live", state_identity="unverified proxy"))
            journal.finish(observe("dedicated_live", evidence="new health check"))
        self.assertFalse(self.hold.exists())
