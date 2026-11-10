import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("policy", ROOT / "scripts/knoxx_public_policy_release.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

BUNDLE = ROOT / "digitalocean/services/knoxx/public-policy"
INDEX = b'<!doctype html><div id="root"></div><script src="/cljs/app.js"></script>'
OLD = {"active": "incumbent"}
NEW = {"active": "policy"}


def response(body=b"", status=200, content_type="text/html"):
    return {"sha256": policy.digest(body), "status": status, "type": content_type}


class FakeHost:
    def __init__(self, path, artifacts):
        self.managed = path
        self.artifacts = path / policy.POLICY_DIR
        self.snippet = path / policy.SNIPPET
        self.receipt = path / policy.RECEIPT
        self.payloads = artifacts
        self.active = OLD
        self.reloads = 0
        self.mismatch = False
        self.timeout_next = None
        self.delayed = None

    def identities(self, _plan):
        return {"fixture": {"Id": "same"}}

    def index(self):
        return INDEX

    def configs(self, _snippet):
        return OLD, NEW

    def current(self):
        return self.active

    def reload(self, *, incumbent=False):
        self.reloads += 1
        target = OLD if incumbent else NEW
        if self.timeout_next == target:
            self.timeout_next = None
            self.delayed = target
            raise subprocess.TimeoutExpired("fixture reload", 45)
        self.active = target

    def complete_delayed_reload(self):
        self.active, self.delayed = self.delayed, None

    def public(self):
        result = {path: response(path.encode()) for path in policy.NEIGHBORS}
        for path, name in policy.HTTP_PATHS.items():
            result[path] = response(self.payloads[name] if self.active == NEW else INDEX)
            result[f"HEAD {path}"] = response()
        if self.mismatch and self.active == NEW:
            result["/privacy.html"] = response(b"wrong")
        return result


class HostTransactionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.artifacts = {
            "homepage.html": INDEX + b'<footer><a href="/privacy.html">Privacy Policy</a></footer>',
            "privacy.html": b"<!doctype html><p>Policy</p>",
            policy.SNIPPET: b"reviewed snippet fixture",
        }
        self.host = FakeHost(Path(temporary.name), self.artifacts)
        self.plan = {"incumbent_index_sha256": policy.digest(INDEX)}
        self.plan_hash = "a" * 64
        self.controller = "b" * 40
        routes_patch = patch.object(policy, "verify_route_delta")
        routes_patch.start()
        self.addCleanup(routes_patch.stop)

    def apply(self):
        return policy.release(self.host, self.plan, self.plan_hash, self.artifacts, self.controller)

    def test_activation_and_same_bundle_retry(self):
        self.assertEqual(self.apply()["result"], "active")
        self.assertEqual(self.host.reloads, 1)
        self.assertEqual(self.host.snippet.read_bytes(), self.artifacts[policy.SNIPPET])
        self.assertEqual(json.loads(self.host.receipt.read_bytes())["phase"], "active")
        self.assertEqual(self.apply()["result"], "already_active")
        self.assertEqual(self.host.reloads, 1)

    def test_public_mismatch_restores_caddy_and_removes_only_release_files(self):
        unrelated = self.host.managed / "unrelated.txt"
        unrelated.write_bytes(b"untouched")
        self.host.mismatch = True
        with self.assertRaisesRegex(policy.Refused, "incumbent was restored"):
            self.apply()
        self.assertEqual(self.host.current(), OLD)
        self.assertEqual(self.host.reloads, 2)
        self.assertFalse(self.host.snippet.exists())
        self.assertFalse(self.host.artifacts.exists())
        self.assertEqual(unrelated.read_bytes(), b"untouched")
        self.assertEqual(json.loads(self.host.receipt.read_bytes())["phase"], "rolled_back")

    def test_unknown_active_state_refuses_before_installing_anything(self):
        self.host.active = {"active": "unknown"}
        with self.assertRaisesRegex(policy.Refused, "unknown drift"):
            self.apply()
        self.assertEqual(list(self.host.managed.iterdir()), [])

    def test_unreceipted_snippet_refuses_even_when_exact(self):
        self.host.snippet.write_bytes(self.artifacts[policy.SNIPPET])
        with self.assertRaisesRegex(policy.Refused, "unreceipted"):
            self.apply()
        self.assertEqual(list(self.host.managed.iterdir()), [self.host.snippet])

    def test_interrupted_exact_activation_is_reconciled(self):
        before = self.host.public()
        policy.record(self.host.receipt, {
            "controller_sha": self.controller,
            "plan_sha256": self.plan_hash,
            "incumbent_config_sha256": policy.config_digest(OLD),
            "candidate_config_sha256": policy.config_digest(NEW),
            "containers": self.host.identities(self.plan),
            "phase": "prepared",
            "public_before": before,
            "reload_intent": None,
        })
        self.host.artifacts.mkdir()
        (self.host.artifacts / "homepage.html").write_bytes(self.artifacts["homepage.html"])
        self.assertEqual(self.apply()["result"], "active")
        self.assertEqual(self.host.reloads, 1)

    def assert_prepared(self, intent):
        state = json.loads(self.host.receipt.read_bytes())
        self.assertEqual(state["phase"], "prepared")
        self.assertEqual(state["reload_intent"], intent)
        self.assertEqual(self.host.snippet.read_bytes(), self.artifacts[policy.SNIPPET])
        for name in policy.HTTP_PATHS.values():
            self.assertEqual((self.host.artifacts / name).read_bytes(), self.artifacts[name])

    def test_timeout_then_delayed_activation_keeps_files_and_retries_same_direction(self):
        self.host.timeout_next = NEW
        with self.assertRaisesRegex(policy.ReloadUncertain, "outcome is unknown"):
            self.apply()
        self.assertEqual(self.host.current(), OLD)
        self.assertEqual(self.host.reloads, 1)
        self.assert_prepared("activate")

        self.host.complete_delayed_reload()
        self.host.mismatch = True
        with self.assertRaisesRegex(policy.Refused, "retried activation did not verify"):
            self.apply()
        self.assertEqual(self.host.current(), NEW)
        self.assertEqual(self.host.reloads, 2)
        self.assert_prepared("activate")

        self.host.mismatch = False
        self.assertEqual(self.apply()["result"], "active")
        self.assertEqual(self.host.reloads, 3)

    def test_timeout_during_rollback_keeps_files_until_acknowledged(self):
        self.host.timeout_next = OLD
        self.host.mismatch = True
        with self.assertRaisesRegex(policy.ReloadUncertain, "outcome is unknown"):
            self.apply()
        self.assertEqual(self.host.current(), NEW)
        self.assert_prepared("rollback")

        self.host.complete_delayed_reload()
        with self.assertRaisesRegex(policy.Refused, "interrupted policy rollback completed"):
            self.apply()
        self.assertEqual(self.host.current(), OLD)
        self.assertEqual(self.host.reloads, 3)
        self.assertFalse(self.host.snippet.exists())
        self.assertFalse(self.host.artifacts.exists())
        self.assertEqual(json.loads(self.host.receipt.read_bytes())["phase"], "rolled_back")

    def test_symlink_is_not_read_or_overwritten(self):
        outside = self.host.managed / "outside"
        outside.write_bytes(b"untouched")
        self.host.snippet.symlink_to(outside)
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertTrue(self.host.snippet.is_symlink())


class BundleTest(unittest.TestCase):
    def test_host_lock_refuses_second_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / policy.LOCK
            with policy.host_lock(path):
                with self.assertRaisesRegex(policy.Refused, "another policy controller"):
                    with policy.host_lock(path):
                        self.fail("lock contention did not refuse")
            with policy.host_lock(path):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_tracked_bundle_pins_all_artifact_bytes(self):
        plan, _, artifacts = policy.load_bundle(BUNDLE, ROOT)
        self.assertEqual(plan["host"], policy.HOST)
        self.assertEqual(set(artifacts), {"homepage.html", "privacy.html", policy.SNIPPET})

    def test_config_group_names_do_not_mask_group_changes(self):
        a = {"routes": [{"group": "group1"}, {"group": "group1"}]}
        b = {"routes": [{"group": "group4"}, {"group": "group4"}]}
        c = {"routes": [{"group": "group1"}, {"group": "group2"}]}
        self.assertEqual(policy.config_digest(a), policy.config_digest(b))
        self.assertNotEqual(policy.config_digest(a), policy.config_digest(c))


@unittest.skipUnless(os.environ.get("CADDY_TEST_BINARY"), "Caddy adapter binary unavailable")
class RealAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "digitalocean/services/caddy/Caddyfile").read_bytes()
        cls.snippet = (BUNDLE / policy.SNIPPET).read_bytes()
        cls.env = {
            key.decode(): key.decode().lower().replace("_", "-") + ".invalid" if key.endswith(b"HOST")
            else "fixture"
            for key in set(re.findall(rb"\{\$([A-Z0-9_]+)(?::[^}]+)?\}", cls.base))
        }
        cls.env["CADDY_KNOXX_HOST"] = policy.HOST

    def adapt(self, snippet):
        result = subprocess.run(
            [os.environ["CADDY_TEST_BINARY"], "adapt", "--config", "/dev/stdin", "--adapter", "caddyfile"],
            input=self.base.replace(policy.BASE_IMPORT, snippet),
            env={**os.environ, **self.env}, capture_output=True, check=True, timeout=10,
        )
        return json.loads(result.stdout)

    def test_exact_tracked_paths_leave_logger_and_all_other_routes_intact(self):
        incumbent = self.adapt(b"")
        policy.verify_route_delta(incumbent, self.adapt(self.snippet))

    def test_broadened_route_and_fallback_are_refused(self):
        incumbent = self.adapt(b"")
        for change in (
            self.snippet.replace(b"path /privacy.html", b"path /privacy*"),
            self.snippet.replace(b"reverse_proxy knoxx-frontend:80", b"reverse_proxy knoxx-backend:8000"),
            self.snippet.replace(b"no_hostname", b"output stderr"),
        ):
            with self.subTest(change=change[-80:]), self.assertRaises(policy.Refused):
                policy.verify_route_delta(incumbent, self.adapt(change))


if __name__ == "__main__":
    unittest.main()
