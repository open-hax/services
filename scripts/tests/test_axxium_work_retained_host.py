"""Exercise retained Work publication, rollback and close without host changes."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_activation_host as host


class RetainedHostTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(TemporaryDirectory())).resolve()
        self.live = self.root / "live"
        self.live.mkdir(mode=0o700)
        (self.live / "mailbox").mkdir(mode=0o700)
        self.ledger = self.live / "mailbox/ledger.edn"
        host.replace_file(self.ledger, b"retained message and unsubscribe\n", 0o600)
        self.route = self.root / "pilot.caddy"
        self.pending = self.root / ".pending"
        self.reopened = self.live / "reopen"
        self.fenced, self.active = b"fenced", b"active"
        self.receipt = {"pilot_containers": {"database": "same-db"}}
        self.containers = {"axxium": "new-app", "database": "same-db"}
        self.dark = {"axxium": "dark-app", "database": "same-db"}
        self.baseline = {"incumbent": "before"}
        self.view = ("caddy", b"root", {})
        self.plan_hash = "a" * 64
        self.controller = "b" * 40
        self.env = b"AXXIUM_PILOT_IMAGE=sha256:dark\n"
        self.roles = {"sender_did": "sender", "recipient_did": "recipient"}
        archive = b"exact staged transport"
        (self.root / "stage-images.tar").write_bytes(archive)
        self.plan = {"source_sha": "c" * 40, "image_id": "index", "image_config_digest": "config",
                     "image_manifest_digest": "manifest", "images_tar_sha256": host.digest(archive),
                     "retained_ledger_sha256": host.digest(self.ledger.read_bytes())}
        for name, value in {"ROOT": self.root, "LIVE": self.live, "REOPEN": self.reopened,
                            "ROUTE": self.route, "PENDING": self.pending, "SLOT": self.root}.items():
            stack.enter_context(patch.object(host, name, value))
        stack.enter_context(patch.object(host.dark, "SLOT", self.root))
        self.operations = []

        def publish(candidate, expected, view):
            self.assertTrue(self.pending.exists())
            self.assertEqual(view, self.view)
            self.assertEqual(self.route.read_bytes() if self.route.exists() else None, expected)
            self.operations.append(candidate)
            if candidate is None:
                self.route.unlink()
            else:
                self.route.write_bytes(candidate)

        def restore(receipt):
            self.assertEqual(receipt, self.receipt)
            self.assertEqual(self.route.read_bytes(), self.fenced)
            self.operations.append("restore")
            return self.dark

        for name, effect in (("publish_route", publish), ("restore_dark", restore)):
            stack.enter_context(patch.object(host, name, side_effect=effect))
        for name, value in (("retained_release", (self.plan, self.plan_hash, {})),
                            ("verify_retained_dark", self.dark), ("retained_private_state", (self.roles, ("key",))),
                            ("read_live_runtime", self.containers), ("caddy_view", self.view),
                            ("public_probe", "503"), ("verify_provider", None),
                            ("verify_retained_credentials", None), ("require_restart_policy", None),
                            ("verify_relay_boundary", None), ("probe_live", None),
                            ("installed_active_route", self.active), ("verify_teardown_fence", None),
                            ("live_compose", None), ("docker", None), ("verify_durable_relay", None),
                            ("set_database_restart_policy", None)):
            setattr(self, name, stack.enter_context(patch.object(host, name, return_value=value)))
        stack.enter_context(patch.object(host.dark, "verified_image", return_value="new-image"))
        stack.enter_context(patch.object(host.dark, "incumbent", return_value=self.baseline))

    def run_transaction(self, operation=host.reopen):
        return operation(self.controller, self.receipt, self.env, self.baseline, self.view, self.fenced, self.active)

    def test_reopen_and_close_preserve_same_ledger_and_attempt_receipts(self):
        before = host.retained_ledger()
        opened = self.run_transaction()
        self.assertEqual(opened["status"], "healthy-live")
        self.assertEqual(opened["pilot_containers"], self.containers)
        self.assertEqual(self.operations, [self.fenced, self.active])
        self.assertEqual(host.retained_ledger(), before)
        self.assertEqual(opened["retained_ledger_before_publication"], before[0])
        self.assertEqual(json.loads((self.reopened / opened["attempt"] / "active.json").read_bytes()), opened)
        self.assertFalse(self.pending.exists())
        closed = self.run_transaction(host.close_retained)
        self.assertEqual(closed["status"], "healthy-dark")
        self.assertEqual(closed["pilot_containers"], self.dark)
        self.assertEqual(self.operations, [self.fenced, self.active, self.fenced, "restore", None])
        self.assertEqual(host.retained_ledger(), before)
        self.assertEqual(json.loads((self.reopened / opened["attempt"] / "close.json").read_bytes()), closed)
        self.assertEqual(self.set_database_restart_policy.call_args_list,
                         [call("same-db", "unless-stopped"), call("same-db", "no")])
        self.assertEqual(self.verify_durable_relay.call_args_list,
                         [call(armed=True), call(armed=True), call(armed=False)])
        self.assertFalse(self.pending.exists())

    def test_reopen_refuses_a_different_ledger_before_any_mutation(self):
        self.ledger.write_bytes(b"different retained ledger")
        with self.assertRaisesRegex(host.dark.Refused, "inactive subscription"):
            self.run_transaction()
        self.assertFalse(self.pending.exists())
        self.assertFalse(self.reopened.exists())
        self.assertEqual(self.operations, [])
        self.docker.assert_not_called()

    def test_change_during_fenced_startup_keeps_hold_and_preserves_changed_ledger(self):
        changed = b"unexpected append"

        def change_ledger(*args):
            self.ledger.write_bytes(changed)
            return self.containers

        self.read_live_runtime.side_effect = change_ledger
        result = self.run_transaction()
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.operations, [self.fenced, self.fenced, "restore", None])
        self.assertEqual(self.ledger.read_bytes(), changed)
        self.assertTrue(self.pending.exists())
        self.assertFalse((self.reopened / "active.json").exists())
        self.probe_live.assert_not_called()

    def test_verified_rollback_can_retry_without_overwriting_first_attempt(self):
        self.probe_live.side_effect = host.dark.Refused("public metadata differs")
        failed = self.run_transaction()
        self.assertEqual(failed["status"], "rolled-back-dark")
        self.assertFalse(self.pending.exists())
        first = self.reopened / failed["attempt"] / "failed.json"
        self.assertEqual(json.loads(first.read_bytes()), failed)
        self.probe_live.side_effect = None
        opened = self.run_transaction()
        self.assertEqual(opened["status"], "healthy-live")
        self.assertNotEqual(opened["attempt"], failed["attempt"])
        self.assertEqual(json.loads(first.read_bytes()), failed)

    def test_close_requires_disarm_before_writing_pending(self):
        self.run_transaction()
        self.verify_durable_relay.side_effect = host.dark.Refused("relay is armed")
        with self.assertRaisesRegex(host.dark.Refused, "relay is armed"):
            self.run_transaction(host.close_retained)
        self.assertEqual(self.operations, [self.fenced, self.active])
        self.assertFalse(self.pending.exists())
        self.assertFalse((self.reopened / "close.json").exists())


if __name__ == "__main__":
    unittest.main()
