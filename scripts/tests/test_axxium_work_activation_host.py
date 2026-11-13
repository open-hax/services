"""Exercise the managed route, private authority, and host failure boundaries."""
import base64
import json
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_activation_host as host


class ActivationHostTest(unittest.TestCase):
    def test_fenced_https_probe_returns_the_first_503(self):
        requests = []

        class Fence(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"Work pilot unavailable.")

            def log_message(self, *_):
                pass

        # Keep the real curl options except the scheme for this loopback fixture.
        def loopback_curl(args, *details, **options):
            args[args.index("=https")] = "=http"
            return host.dark.command(args, *details, **options)

        with HTTPServer(("127.0.0.1", 0), Fence) as server:
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                with (patch.object(host, "ORIGIN", f"http://127.0.0.1:{server.server_port}"),
                      patch.object(host, "command", side_effect=loopback_curl)):
                    self.assertEqual(host.public_probe("/health", retry=True), "503")
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(requests, ["/health"])

    def test_fenced_https_probe_retries_a_transport_failure(self):
        with (patch.object(host, "command", side_effect=[host.dark.Refused("certificate pending"), b"503"]) as probe,
              patch.object(host.time, "sleep") as pause):
            self.assertEqual(host.public_probe("/health", retry=True), "503")
        self.assertEqual(probe.call_count, 2)
        pause.assert_called_once_with(3)

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

    def test_teardown_accepts_only_the_two_reviewed_active_route_versions(self):
        previous, active = b"previous\n", b"active\n"
        with TemporaryDirectory() as folder:
            route = Path(folder) / "pilot.caddy"
            with (patch.object(host, "ROUTE", route),
                  patch.object(host, "PRE_CONSENT_ROUTE_SHA256", host.digest(previous))):
                for expected in (previous, active):
                    route.write_bytes(expected)
                    self.assertEqual(host.installed_active_route(active), expected)
                route.write_bytes(b"unreviewed")
                with self.assertRaises(host.dark.Refused):
                    host.installed_active_route(active)

    def test_ingress_repair_preserves_the_active_containers_and_holds_uncertain_rollback(self):
        previous, active = b"previous\n", b"active\n"
        plan_bytes = (host.PACKAGE / "activation-release-plan.json").read_bytes()
        plan = json.loads(plan_bytes)
        containers = {"axxium": "fixture-app", "database": "fixture-db"}
        original = host.json_bytes({"status": "healthy-live", "origin": host.ORIGIN,
                                    "source_sha": plan["source_sha"], "plan_sha256": host.digest(plan_bytes),
                                    "image_manifest_digest": plan["image_manifest_digest"],
                                    "pilot_containers": containers, "stop_at": "fixture"})
        for error, expected_status in ((None, "ingress-repaired"),
                                       (host.dark.CommandTimeout("uncertain"), "held")):
            with (self.subTest(error=error),
                  patch.object(host, "private_read", side_effect=[original, b'{"stop_at":"fixture"}', b"{}", b""]),
                  patch.object(host, "validate_config"),
                  patch.object(host, "verify_relay"),
                  patch.object(host, "installed_active_route", side_effect=[previous, active]),
                  patch.object(host, "PRE_CONSENT_ROUTE_SHA256", host.digest(previous)),
                  patch.object(host.dark, "verified_image", return_value="fixture"),
                  patch.object(host, "read_live_runtime", return_value=containers),
                  patch.object(host, "absent", return_value=True),
                  patch.object(host, "write_json") as journal,
                  patch.object(host, "publish_route") as publish,
                  patch.object(host, "probe_live", side_effect=error),
                  patch.object(host.dark, "incumbent", return_value={}),
                  patch.object(host, "caddy_view", return_value=()),
                  patch.object(host, "clear_hold") as clear):
                result = host.repair_ingress("c" * 40, {}, (), active)
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["pilot_containers"], containers)
                self.assertEqual(journal.call_args_list[0].args[0], host.PENDING)
                if error:
                    self.assertEqual(publish.call_args_list, [call(active, previous, ()), call(previous, active, ())])
                    clear.assert_not_called()
                else:
                    publish.assert_called_once_with(active, previous, ())
                    clear.assert_called_once()

    def test_removed_legacy_units_do_not_block_an_armed_relay(self):
        def prop(unit, name):
            if unit in (host.TIMER, host.STOP):
                return {"ActiveState": "inactive", "UnitFileState": ""}[name]
            if name == "ActiveState":
                return "active"
            if name == "Listen":
                return "172.31.255.1:19073 (Stream)"
            return ""

        class Stat:
            st_uid = 0

        with (patch.object(host, "verify_relay_unit"), patch.object(host, "systemd_property", side_effect=prop),
              patch.object(host.Path, "is_symlink", return_value=True),
              patch.object(host.Path, "resolve", return_value=host.UNITS / host.SOCKET),
              patch.object(host.Path, "lstat", return_value=Stat())):
            host.verify_durable_relay(armed=True)
        with patch.object(host, "verify_relay_unit"), patch.object(
            host, "systemd_property", return_value="enabled",
        ):
            with self.assertRaisesRegex(host.dark.Refused, "active or enabled"):
                host.verify_durable_relay(armed=False)

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
              patch.object(host, "installed_active_route", return_value=active),
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
