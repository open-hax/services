"""Exercise the managed route, private authority, and host failure boundaries."""
import base64
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_activation_host as host


class ActivationHostTest(unittest.TestCase):
    def test_validation_keeps_siblings_in_order_without_importing_old_pilot(self):
        root = b"before\nimport /etc/caddy/managed/*.caddy\nafter\n"
        candidate = b"exact-pilot-site {\n}\n"
        siblings = {"z.caddy": "digest", "aa.caddy": "digest"}
        self.assertEqual(host.render_validation(root, siblings, candidate),
                         b"before\nimport /etc/caddy/managed/aa.caddy\n"
                         + candidate + b"import /etc/caddy/managed/z.caddy\nafter\n")
        self.assertEqual(host.render_validation(root, siblings, b""),
                         b"before\nimport /etc/caddy/managed/aa.caddy\n"
                         b"import /etc/caddy/managed/z.caddy\nafter\n")
        for bad_root in (b"before\nafter\n", root + root):
            with self.assertRaises(host.dark.Refused):
                host.render_validation(bad_root, siblings, candidate)

    def test_private_authority_rejects_wrong_origin_role_or_client(self):
        binding = {"sender_did": "did:plc:" + "a" * 24, "recipient_did": "did:plc:" + "b" * 24,
                   "stop_at": "2026-11-10T23:00:00Z"}
        authority = {"issuer": host.ISSUER, "mailbox-resource": host.RESOURCE, "mailbox-client-id": host.CLIENT,
                     "clients": [{"id": host.CLIENT, "secret": "A" * 64, "redirect-uris": [host.CALLBACK]}]}
        seal = base64.b64encode(b"x" * 32)
        self.assertEqual(host.validate_config(binding, authority, seal),
                         datetime(2026, 11, 10, 23, tzinfo=timezone.utc))
        with self.assertRaises(host.dark.Refused):
            host.validate_config({**binding, "sender_did": binding["recipient_did"]}, authority, seal)
        for field, value in (("issuer", "https://axxium.promethean.rest/oidc"),
                             ("mailbox-resource", "https://unrelated.example/api/mailbox/mcp"),
                             ("allow-loopback?", True)):
            with self.subTest(field=field), self.assertRaises(host.dark.Refused):
                host.validate_config(binding, {**authority, field: value}, seal)
        for field, value in (("redirect-uris", [host.CALLBACK, "https://unrelated.example"]),
                             ("device-enabled?", True), ("secret", "short")):
            wrong = deepcopy(authority)
            wrong["clients"][0][field] = value
            with self.subTest(field=field), self.assertRaises(host.dark.Refused):
                host.validate_config(binding, wrong, seal)
        with self.assertRaises(host.dark.Refused):
            host.validate_config(binding, authority, base64.b64encode(b"x" * 31))

    def test_rollback_fences_before_restoring_dark_and_keeps_hold_on_timeout(self):
        fenced, active = b"fenced\n", b"active\n"
        view, baseline, receipt = object(), object(), object()
        for error, expected_status in ((host.dark.Refused("failed"), "rolled-back-dark"),
                                       (host.dark.CommandTimeout("uncertain"), "held")):
            with (self.subTest(error=type(error).__name__), TemporaryDirectory() as folder,
                  patch.object(host, "publish_route") as publish,
                  patch.object(host, "restore_dark") as restore,
                  patch.object(host.dark, "incumbent", return_value=baseline),
                  patch.object(host, "caddy_view", return_value=view),
                  patch.object(host, "write_json") as write, patch.object(host, "clear_hold") as clear):
                route = Path(folder) / "pilot.caddy"
                route.write_bytes(active)
                operations = []
                publish.side_effect = lambda candidate, *_: operations.append(candidate)
                restore.side_effect = lambda _: operations.append("restored")
                with patch.object(host, "ROUTE", route):
                    result = host.rollback_activation(receipt, baseline, view, fenced, active, True, error,
                                                      {"status": "held"})
                self.assertEqual(operations, [fenced, "restored", None])
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(clear.call_count, expected_status == "rolled-back-dark")
                self.assertEqual(write.call_count, expected_status == "rolled-back-dark")

    def test_operator_must_fence_before_work_teardown(self):
        with (patch.object(host, "systemd_property", return_value="active"),
              patch.object(host, "public_probe") as probe, self.assertRaises(host.dark.Refused)):
            host.verify_teardown_fence()
        probe.assert_not_called()
        with (patch.object(host, "systemd_property", return_value="inactive"),
              patch.object(host, "public_probe", return_value="503")):
            host.verify_teardown_fence()

    def test_teardown_leaves_hold_if_public_fence_is_not_verified(self):
        fenced, active = b"fenced\n", b"active\n"
        view, baseline, receipt = object(), object(), object()
        plan = json.loads((host.PACKAGE / "activation-release-plan.json").read_bytes())
        prior = host.json_bytes({"status": "healthy-live", "origin": host.ORIGIN, "source_sha": plan["source_sha"],
                                 "image_manifest_digest": plan["image_manifest_digest"]})
        with (patch.object(host, "private_read", return_value=prior), patch.object(host, "absent", return_value=True),
              patch.object(host, "verify_teardown_fence"),
              patch.object(host, "write_json") as journal, patch.object(host, "publish_route") as publish,
              patch.object(host, "public_probe", return_value="401"), patch.object(host, "restore_dark") as restore,
              patch.object(host, "clear_hold") as clear):
            result = host.teardown("b" * 40, receipt, baseline, view, fenced, active)
        self.assertEqual(result["status"], "held")
        self.assertEqual(journal.call_count, 1)
        self.assertEqual(journal.call_args.args[0], host.PENDING)
        self.assertEqual(publish.call_args_list, [call(fenced, active, view)])
        restore.assert_not_called()
        clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
