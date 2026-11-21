from copy import deepcopy
from email.message import Message
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import knoxx_policy_baseline_reconciliation as reconcile
from scripts.tests.test_knoxx_public_policy_release import BUNDLE, ROOT, response


policy = reconcile.policy
BASE = (ROOT / "digitalocean/services/caddy/Caddyfile").read_bytes()
SNIPPET = (BUNDLE / policy.SNIPPET).read_bytes()
PLAN_HASH = "a" * 64
CONTROLLER = "b" * 40
SITE = {"match": [{"host": [policy.HOST]}], "handle": [{"handler": "subroute", "routes": [
    {"match": [{"path": ["/mcp*"]}], "handle": [{"handler": "reverse_proxy",
                                               "upstreams": [{"dial": "knoxx-backend:8000"}]}]},
    {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": policy.FRONTEND_DIAL}]}]},
]}]}
CONTAINERS = {name: {"Id": name, "Image": name + "-image", "State.StartedAt": "fixture", "RestartCount": 0}
              for name in (policy.FRONTEND, policy.BACKEND, policy.CADDY)}


def config(label):
    site = deepcopy(SITE)
    if label == "bridge":
        policy.child_routes(site)[-1]["handle"][0]["upstreams"][0]["dial"] = policy.RETIREMENT_DIAL
    return {"apps": {"http": {"servers": {"fixture": {"routes": [site]}}}}, "fixture": label}


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.host = host = policy.Host.__new__(policy.Host)
        host.base = self.write("Caddyfile", BASE)
        host.managed = self.root / "managed"
        host.artifacts = host.managed / policy.POLICY_DIR
        host.artifacts.mkdir(parents=True)
        host.snippet = self.write("managed/" + policy.SNIPPET, SNIPPET)
        self.work = self.write("managed/axxium-work-pilot.caddy", b"fixture work")
        self.payloads = {name: (BUNDLE / name).read_bytes() for name in policy.HTTP_PATHS.values()}
        for name, raw in self.payloads.items():
            self.write("managed/" + policy.POLICY_DIR + "/" + name, raw)
        self.payloads[policy.SNIPPET] = SNIPPET
        native, active, bridge = (config(name) for name in ("native", "active", "bridge"))
        archived_native, archived_active = (config(name) for name in ("archived-native", "archived-active"))
        seen = {key: policy.config_digest(value) for key, value in
                (("native", native), ("active", active), ("bridge", bridge), ("current", active), ("startup", active))}
        archive = self.write("archive/Caddyfile", BASE + b"\n# historical fixture\n")
        owner = self.write("archive/owner.json", b'{ "published": true }\n')
        reopen = self.write("archive/reopen.json", b'{"receipt":"fixture"}\n')
        self.owner = owner

        def adapt(raw):
            self.assertNotIn(b"axxium-work-pilot.caddy", raw)
            self.assertIn(b"historical fixture", raw)
            return archived_active if SNIPPET in raw else archived_native

        host.adapt = adapt
        host.configs = lambda snippet: (native, active if snippet == SNIPPET else bridge)
        host.current = lambda: active
        host.startup_config = lambda: active
        host.shadow_absent = lambda: True
        host.identities = lambda _: CONTAINERS
        host.index = lambda: b"fixture index"
        self.plan = {"incumbent_index_sha256": policy.digest(host.index())}
        neighbors = {path: response(path.encode()) for path in policy.NEIGHBORS}
        public = dict(neighbors)
        for path, filename in policy.HTTP_PATHS.items():
            public[path] = response(self.payloads[filename])
            public["HEAD " + path] = response()
        host.public = lambda: public
        original = {"controller_sha": "c" * 40, "plan_sha256": PLAN_HASH, "phase": "active",
                    "incumbent_config_sha256": policy.config_digest(archived_native),
                    "candidate_config_sha256": policy.config_digest(archived_active),
                    "public_before": neighbors, "containers": CONTAINERS}
        host.receipt = self.write("original.json", json.dumps(original).encode())
        names = sorted((self.work, host.snippet))
        managed = [{"name": path.name, "sha256": self.sha(path)} for path in names]
        mcp = policy.child_routes(SITE)[0]
        self.certificate = {
            "original_v1": {"receipt_sha256": self.sha(host.receipt), "controller_sha": original["controller_sha"],
                            "plan_sha256": PLAN_HASH, "native_config_sha256": original["incumbent_config_sha256"],
                            "active_config_sha256": original["candidate_config_sha256"]},
            "host_canonical_receipts": [{"path": str(owner), "fileSha256": self.sha(owner),
                                         "canonicalReceiptSha256": reconcile.fixture.canonical({"published": True})}],
            "retained_work_reopen": {"path": str(reopen), "sha256": self.sha(reopen),
                                     "canonicalReceiptSha256": reconcile.fixture.canonical({"receipt": "fixture"})},
            "historical_reconstruction": {"archived_host_path": str(archive), "archived_base_sha256": self.sha(archive),
                                          "excluded_managed_names": [self.work.name]},
            "observed_current": {"base_sha256": self.sha(host.base), "managed": managed,
                                 "managed_sha256": reconcile.fixture.canonical(managed), "config_sha256": seen},
            "mcp_continuity": {"historical_knoxx_site_sha256": policy.config_digest(SITE),
                               "historical_mcp_route_sha256": policy.config_digest(mcp)},
            "public_before_retirement": public, "public_mcp_origin": "https://knoxx.promethean.rest/mcp",
        }

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def sha(self, path):
        return policy.digest(path.read_bytes())

    def observe(self, challenge=None):
        with patch.object(policy, "verify_retirement_bridge") as bridge, \
             patch.object(reconcile, "verify_mcp_challenge", side_effect=challenge):
            result = reconcile.observe(self.host, self.plan, PLAN_HASH, self.payloads, CONTROLLER, self.certificate)
        bridge.assert_called_once()
        return result

    def test_read_only_result_preserves_every_fixture_byte(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.observe()
        self.assertEqual(self.certificate["original_v1"]["receipt_sha256"], result["active_receipt_sha256"])
        self.assertEqual(reconcile.SCHEMA, result["schema"])
        self.assertEqual("baseline_reconciled", result["result"])
        self.assertNotIn("version", result)
        self.assertFalse(result["production_hold_cleared"])
        self.assertFalse(result["live_authorization"])
        self.assertTrue(result["mcp_bearer_challenge_verified"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_legacy_preflight_still_refuses_the_current_whole_config(self):
        with patch.object(policy, "verify_retirement_bridge"), self.assertRaisesRegex(policy.Refused, "active receipt"):
            policy.prepare_retirement(self.host, self.plan, PLAN_HASH, self.payloads, CONTROLLER)

    def test_host_receipt_uses_exact_frozen_bytes_and_rejects_same_content_symlink(self):
        self.owner.chmod(0o644)
        with self.assertRaisesRegex(policy.Refused, "unsafe historical"):
            self.observe()
        self.owner.chmod(0o600)
        self.owner.write_bytes(b'{"published":true}\n')
        with self.assertRaisesRegex(policy.Refused, "route-owner receipt"):
            self.observe()
        other = self.write("other.json", b'{ "published": true }\n')
        self.owner.unlink()
        self.owner.symlink_to(other)
        with self.assertRaisesRegex(policy.Refused, "unsafe historical"):
            self.observe()

    def test_rejects_unknown_managed_fragment(self):
        self.write("managed/unknown.caddy", b"# unrelated drift")
        with self.assertRaisesRegex(policy.Refused, "managed Caddy manifest"):
            self.observe()

    def test_rejects_live_startup_mismatch(self):
        self.host.startup_config = lambda: config("native")
        with self.assertRaisesRegex(policy.Refused, "live or startup"):
            self.observe()

    def test_rechecks_owner_receipt_after_public_probe(self):
        with self.assertRaisesRegex(policy.Refused, "route-owner receipt"):
            self.observe(lambda: self.owner.write_bytes(b'{"published":false}\n'))

    def test_exact_mcp_handler_refuses_a_substitute_even_against_matching_whole_config(self):
        changed = config("active")
        routes = policy.routes(changed)["fixture"]["routes"]
        policy.child_routes(routes[0])[0]["handle"][0]["upstreams"][0]["dial"] = "knoxx-frontend:80"
        self.host.configs = lambda snippet: (config("native"), changed if snippet == SNIPPET else config("bridge"))
        self.host.current = self.host.startup_config = lambda: changed
        digests = self.certificate["observed_current"]["config_sha256"]
        for key in ("active", "current", "startup"):
            digests[key] = policy.config_digest(changed)
        with self.assertRaisesRegex(policy.Refused, "MCP continuity"):
            self.observe()


class AdmissionTest(unittest.TestCase):
    def test_fixed_certificate_passes_and_replacement_refuses(self):
        self.assertFalse(reconcile.load_certificate()["live_authorization"])
        with patch.object(policy, "read_file", return_value=b"{}"), \
             self.assertRaisesRegex(policy.Refused, "reviewed baseline certificate differs"):
            reconcile.load_certificate()

    def test_challenge_requires_exact_anonymous_response(self):
        for status, challenges, accepted in ((401, [reconcile.MCP_CHALLENGE], True),
                                             (200, [reconcile.MCP_CHALLENGE], False),
                                             (401, ["Bearer"], False),
                                             (401, [reconcile.MCP_CHALLENGE] * 2, False)):
            with self.subTest(status=status, challenges=challenges), patch.object(policy, "build_opener") as builder:
                opened = builder.return_value.open
                reply = opened.return_value
                reply.code = status
                reply.headers = Message()
                for header in challenges:
                    reply.headers["WWW-Authenticate"] = header
                if accepted:
                    reconcile.verify_mcp_challenge()
                else:
                    with self.assertRaises(policy.Refused):
                        reconcile.verify_mcp_challenge()
                query = opened.call_args.args[0]
                self.assertEqual("https://knoxx.promethean.rest/mcp", query.full_url)
                self.assertIsNone(query.get_header("Authorization"))
                self.assertIsNone(query.get_header("Cookie"))


if __name__ == "__main__":
    unittest.main()
