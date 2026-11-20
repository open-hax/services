import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.tests.test_knoxx_public_policy_release import BUNDLE, ROOT, policy, response


BASE = (ROOT / "digitalocean/services/caddy/Caddyfile").read_bytes()
SNIPPET = (BUNDLE / policy.SNIPPET).read_bytes()
CADDY_IMAGE = "sha256:612f0ff47f33888e3b61a8db399ff2dc22c2cefb8cb652d86a619e52eabcd51f"
NGINX_IMAGE = "sha256:6769dc3a703c719c1d2756bda113659be28ae16cf0da58dd5fd823d6b9a050ea"
CONTROLLER = "b" * 40
PLAN_HASH = "a" * 64
CONFIGS = {name: {"fixture": name} for name in ("active", "bridge", "native")}
CONTAINERS = {
    name: {"Id": name, "Image": name + "-image", "State.StartedAt": "fixture", "RestartCount": 0}
    for name in (policy.FRONTEND, policy.BACKEND, policy.CADDY)
}


def active_receipt(before=None, configs=CONFIGS):
    return json.dumps({
        "controller_sha": "c" * 40,
        "plan_sha256": PLAN_HASH,
        "phase": "active",
        "incumbent_config_sha256": policy.config_digest(configs["native"]),
        "candidate_config_sha256": policy.config_digest(configs["active"]),
        "public_before": before,
        "containers": CONTAINERS,
    }).encode()


class RetirementCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.receipt = policy.retirement_record(active_receipt(), CONFIGS, CONTROLLER)

    def check(self, phase, disk, live, frontend, shadow="ready", verified=True, archived=False):
        policy.verify_retirement_checkpoint(
            {**self.receipt, "phase": phase}, self.receipt["configs"][disk], self.receipt["configs"][live],
            frontend, shadow=shadow, frontend_verified=verified, archive_verified=archived,
        )

    def test_native_and_restore_readbacks_survive_each_startup_boundary(self):
        self.assertEqual(self.receipt["active_receipt_sha256"], policy.digest(active_receipt()))
        for phase, disk, live, frontend in (
            ("install_bridge", "active", "active", "incumbent"),
            ("install_bridge", "bridge", "active", "incumbent"),
            ("install_bridge", "bridge", "bridge", "incumbent"),
            ("switch_frontend", "bridge", "bridge", "replacing"),
            ("install_native", "native", "bridge", "candidate"),
            ("install_native", "native", "native", "candidate"),
            ("restore_bridge", "bridge", "native", "candidate"),
            ("restore_bridge", "bridge", "bridge", "candidate"),
            ("restore_frontend", "bridge", "bridge", "replacing"),
            ("restore_active", "active", "bridge", "incumbent"),
            ("restore_active", "active", "active", "incumbent"),
        ):
            with self.subTest(phase=phase, disk=disk, live=live):
                self.check(phase, disk, live, frontend)
        self.check("retired", "native", "native", "candidate", shadow="absent", archived=True)
        self.check("restored", "active", "active", "incumbent", shadow="absent", archived=True)

    def test_refuses_unsafe_disk_live_or_frontend_state(self):
        for args in (
            ("install_bridge", "active", "bridge", "incumbent"),
            ("switch_frontend", "active", "bridge", "replacing"),
            ("switch_frontend", "bridge", "bridge", "replacing", "absent"),
            ("install_native", "bridge", "native", "candidate"),
            ("install_native", "native", "bridge", "candidate", "ready", False),
            ("restore_active", "active", "bridge", "candidate"),
            ("retired", "native", "native", "candidate", "unknown", True, True),
            ("retired", "native", "native", "candidate", "absent", True, False),
        ):
            with self.subTest(args=args), self.assertRaises(policy.Refused):
                self.check(*args)
        with self.assertRaises(policy.Refused):
            policy.verify_retirement_checkpoint(self.receipt, "d" * 64, "e" * 64, "incumbent",
                                                 shadow="absent", frontend_verified=True)
        malformed = active_receipt().replace(b'"active"', b'"prepared"')
        with self.assertRaises(policy.Refused):
            policy.retirement_record(malformed, CONFIGS, CONTROLLER)


class BridgeInputTest(unittest.TestCase):
    def test_bridge_only_rewrites_the_two_frontend_dials(self):
        bridge = policy.retirement_bridge(BASE, SNIPPET)
        self.assertTrue(bridge.startswith(SNIPPET.replace(policy.FRONTEND_DIAL.encode(),
                                                         policy.RETIREMENT_DIAL.encode())))
        self.assertEqual(bridge.count(policy.RETIREMENT_DIAL.encode()), 2)
        self.assertIn(b"{$CADDY_KNOXX_HOST}/* {", bridge)
        with self.assertRaises(policy.Refused):
            policy.retirement_bridge(BASE.replace(b"{$CADDY_KNOXX_HOST} {", b"other {"), SNIPPET)
        with self.assertRaises(policy.Refused):
            policy.retirement_bridge(BASE, SNIPPET + SNIPPET)


class ReadOnlyRetirementPreflightTest(unittest.TestCase):
    def test_active_receipt_and_disk_are_read_and_left_byte_exact(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            host = policy.Host.__new__(policy.Host)
            host.base = root / "Caddyfile"
            host.base.write_bytes(BASE)
            host.managed = root / "managed"
            host.artifacts = host.managed / policy.POLICY_DIR
            host.artifacts.mkdir(parents=True)
            host.snippet = host.managed / policy.SNIPPET
            host.snippet.write_bytes(SNIPPET)
            payloads = {name: (BUNDLE / name).read_bytes() for name in policy.HTTP_PATHS.values()}
            for name, content in payloads.items():
                (host.artifacts / name).write_bytes(content)
            before = {name: response(name.encode()) for name in policy.NEIGHBORS}
            after = dict(before)
            for url, filename in policy.HTTP_PATHS.items():
                after[url] = response(payloads[filename])
                after["HEAD " + url] = response()
            host.receipt = root / policy.RECEIPT
            original = active_receipt(before)
            host.receipt.write_bytes(original)
            host.receipt.chmod(0o600)
            host.identities = lambda _plan: CONTAINERS
            host.index = lambda: b"incumbent index"
            host.configs = lambda snippet: (CONFIGS["native"], CONFIGS["active" if snippet == SNIPPET else "bridge"])
            host.current = lambda: CONFIGS["active"]
            host.startup_config = lambda: CONFIGS["active"]
            host.public = lambda: after
            host.shadow_absent = lambda: True
            plan = {"incumbent_index_sha256": policy.digest(host.index())}
            payloads[policy.SNIPPET] = SNIPPET
            with patch.object(policy, "verify_retirement_bridge"):
                result = policy.prepare_retirement(host, plan, PLAN_HASH, payloads, CONTROLLER)
                self.assertEqual(result["result"], "incumbent_verified")
                self.assertEqual(result["active_receipt_sha256"], policy.digest(original))
                self.assertEqual(host.receipt.read_bytes(), original)
                self.assertEqual(host.snippet.read_bytes(), SNIPPET)
                host.startup_config = lambda: CONFIGS["native"]
                with self.assertRaises(policy.Refused):
                    policy.prepare_retirement(host, plan, PLAN_HASH, payloads, CONTROLLER)


@unittest.skipUnless(os.environ.get("KNOXX_RETIREMENT_DOCKER_FIXTURE") == "1", "owned Docker rehearsal is opt-in")
class NativeRestartTest(unittest.TestCase):
    def docker(self, *args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, check=check, timeout=20).stdout

    def adapt_routes(self, ingress, snippet, content, host=policy.HOST):
        if content is None:
            snippet.unlink(missing_ok=True)
        else:
            snippet.write_bytes(content)
        inputs = {
            "CADDY_KNOXX_HOST": host, "CADDY_PROXX_HOST": "proxx.promethean.rest",
            "CADDY_WEBSITE_HOST": "www.promethean.rest", "CADDY_ACME_EMAIL": "fixture@example.invalid",
            "CADDY_KNOXX_DEV_HOST": "knoxx-dev.promethean.rest",
            "CADDY_OPENCODE_DEV_HOST": "opencode-dev.promethean.rest",
            "CADDY_SHADOW_DEV_HOST": "shadow-dev.promethean.rest", "DEV_BASIC_AUTH_USER": "fixture",
            "DEV_BASIC_AUTH_HASH": "$2a$10$8K1p/a0dR1x8gwz/E4E0UeUSzCIISh3Vcg9r37vQ2Hro.iL93ULdq",
        }
        args = [arg for key, value in inputs.items() for arg in ("--env", f"{key}={value}")]
        return json.loads(self.docker("run", "--rm", "--pull", "never", "--network", "none", *args,
                                      "-v", f"{ingress}:/etc/caddy:ro", CADDY_IMAGE,
                                      "caddy", "adapt", "--config", "/etc/caddy/Caddyfile"))

    def start(self, name, image, *args):
        self.docker("run", "-d", "--pull", "never", "--name", name, "--network", self.network, *args, image)
        self.addCleanup(self.docker, "rm", "-f", name, check=False)

    def public(self, path):
        for _ in range(20):
            try:
                return self.docker("exec", self.caddy, "wget", "-qO-", "--timeout=2",
                                   "--header", f"Host: {policy.HOST}",
                                   "--header", "x-open-hax-bridge-auth: fixture-untrusted-value",
                                   f"http://127.0.0.1{path}")
            except subprocess.CalledProcessError:
                time.sleep(0.15)
        self.fail(f"fixture Caddy did not serve {path}")

    def restart(self):
        self.docker("restart", "--time", "1", self.caddy)

    def checkpoint(self, receipt, phase, frontend, *, shadow="ready", archived=False):
        startup = self.docker("exec", self.caddy, "caddy", "adapt", "--config", "/etc/caddy/Caddyfile")
        live = self.docker("exec", self.caddy, "wget", "-qO-", "http://127.0.0.1:2019/config/")
        policy.verify_retirement_checkpoint(
            {**receipt, "phase": phase}, policy.config_digest(json.loads(startup)),
            policy.config_digest(json.loads(live)), frontend, shadow=shadow,
            frontend_verified=frontend != "replacing", archive_verified=archived,
        )

    def test_restart_before_and_after_native_commit_and_reverse(self):
        with tempfile.TemporaryDirectory(prefix="knoxx-retirement-fixture-") as scratch:
            root = Path(scratch)
            ingress = root / "ingress"
            managed = ingress / "managed"
            artifacts = managed / policy.POLICY_DIR
            artifacts.mkdir(parents=True)
            homepage = (BUNDLE / "homepage.html").read_bytes()
            privacy = (BUNDLE / "privacy.html").read_bytes()
            (artifacts / "homepage.html").write_bytes(homepage)
            (artifacts / "privacy.html").write_bytes(privacy)
            (ingress / "Caddyfile").write_bytes(BASE)
            snippet = managed / policy.SNIPPET
            bridge = policy.retirement_bridge(BASE, SNIPPET)
            native_config = self.adapt_routes(ingress, snippet, None)
            active_config = self.adapt_routes(ingress, snippet, SNIPPET)
            bridge_config = self.adapt_routes(ingress, snippet, bridge)
            policy.verify_retirement_bridge(native_config, active_config, bridge_config)
            changed = policy.retirement_bridge(BASE, SNIPPET).replace(
                b"header_up -x-open-hax-bridge-auth", b"header_up X-Mutated yes", 1,
            )
            with self.assertRaises(policy.Refused):
                policy.verify_retirement_bridge(native_config, active_config,
                                                self.adapt_routes(ingress, snippet, changed))

            # Plain HTTP only on our internal fixture network. The same adapter
            # and managed-file restart behavior were verified above at the
            # exact production host and configuration with networking disabled.
            (ingress / "Caddyfile").write_bytes(BASE.split(b"\n# Proxx ", 1)[0] + b"\n" + policy.BASE_IMPORT + b"\n")
            http_snippet = SNIPPET.replace(b"https://", b"http://")
            http_bridge = bridge.replace(b"https://", b"http://")
            host = f"http://{policy.HOST}"
            configs = {stage: self.adapt_routes(ingress, snippet, content, host)
                       for stage, content in (("native", None), ("active", http_snippet), ("bridge", http_bridge))}
            original_receipt = active_receipt(configs=configs)
            receipt = policy.retirement_record(original_receipt, configs, CONTROLLER)
            snippet.write_bytes(http_snippet)
            name = "knoxx-retirement-fixture-" + uuid.uuid4().hex[:10]
            self.network = name
            self.caddy = name + "-caddy"
            self.docker("network", "create", "--internal", self.network)
            self.addCleanup(self.docker, "network", "rm", self.network, check=False)
            backend_conf = root / "backend.conf"
            backend_conf.write_text(
                'server { listen 8000; location / { if ($http_x_open_hax_bridge_auth != "") '
                '{ return 400; } return 200 "backend"; } }\n'
            )
            self.start(name + "-backend", NGINX_IMAGE, "--network-alias", "knoxx-backend",
                       "-v", f"{backend_conf}:/etc/nginx/conf.d/default.conf:ro")
            frontend_conf = root / "frontend.conf"
            frontend_conf.write_text(
                'server { listen 80; root /usr/share/nginx/html; location /api/ '
                '{ proxy_pass http://knoxx-backend:8000; } }\n'
            )
            old, candidate = root / "old", root / "candidate"
            old.mkdir()
            candidate.mkdir()
            (old / "index.html").write_bytes(b"old SPA")
            (old / "app.css").write_bytes(b"incumbent asset")
            (candidate / "index.html").write_bytes(b"native homepage")
            (candidate / "privacy.html").write_bytes(privacy)
            (candidate / "app.css").write_bytes(b"candidate asset")

            def start_frontend(container, alias, webroot):
                self.start(container, NGINX_IMAGE, "--network-alias", alias,
                           "-v", f"{webroot}:/usr/share/nginx/html:ro",
                           "-v", f"{frontend_conf}:/etc/nginx/conf.d/default.conf:ro")

            normal = name + "-normal"
            shadow = name + "-shadow"
            start_frontend(normal, "knoxx-frontend", old)
            self.start(self.caddy, CADDY_IMAGE, "--env", f"CADDY_KNOXX_HOST={host}",
                       "--env", "CADDY_ACME_EMAIL=fixture@example.invalid",
                       "-v", f"{ingress}:/etc/caddy:ro")
            self.assertEqual(self.public("/"), homepage)
            self.assertEqual(self.public("/privacy.html"), privacy)
            self.checkpoint(receipt, "prepared", "incumbent", shadow="absent")

            start_frontend(shadow, policy.RETIREMENT_FRONTEND, old)
            policy.write_atomic(snippet, http_bridge, 0o644)
            self.checkpoint(receipt, "install_bridge", "incumbent")
            self.restart()
            self.docker("rm", "-f", normal)
            self.restart()
            self.assertEqual(self.public("/"), homepage)
            self.assertEqual(self.public("/privacy.html"), privacy)
            self.assertEqual(self.public("/app.css"), b"incumbent asset")
            for path in ("/api/auth/config", "/.well-known/oauth-protected-resource"):
                self.assertEqual(self.public(path), b"backend")
            self.checkpoint(receipt, "switch_frontend", "replacing")

            start_frontend(normal, "knoxx-frontend", candidate)
            self.assertEqual(self.public("/app.css"), b"incumbent asset")
            snippet.rename(root / "retired-snippet")
            self.assertEqual(self.public("/"), homepage, "confirmed live Caddy can retain the overlay until reload")
            self.checkpoint(receipt, "install_native", "candidate")
            self.restart()
            self.assertEqual(self.public("/"), b"native homepage")
            self.assertEqual(self.public("/privacy.html"), privacy)
            self.assertEqual(self.public("/app.css"), b"candidate asset")
            artifacts.rename(root / "archived-policy")
            (root / "active-receipt.json").write_bytes(original_receipt)
            self.docker("rm", "-f", shadow)
            self.restart()
            self.assertEqual(self.public("/privacy.html"), privacy)
            self.checkpoint(receipt, "retired", "candidate", shadow="absent", archived=True)

            start_frontend(shadow, policy.RETIREMENT_FRONTEND, old)
            (root / "archived-policy").rename(artifacts)
            policy.write_atomic(snippet, http_bridge, 0o644)
            self.checkpoint(receipt, "restore_bridge", "candidate")
            self.restart()
            self.docker("rm", "-f", normal)
            self.restart()
            self.assertEqual(self.public("/app.css"), b"incumbent asset")
            self.checkpoint(receipt, "restore_frontend", "replacing")
            start_frontend(normal, "knoxx-frontend", old)
            policy.write_atomic(snippet, http_snippet, 0o644)
            self.checkpoint(receipt, "restore_active", "incumbent")
            self.restart()
            self.docker("rm", "-f", shadow)
            self.assertEqual(self.public("/"), homepage)
            self.assertEqual(self.public("/privacy.html"), privacy)
            self.assertEqual(self.public("/app.css"), b"incumbent asset")
            self.assertEqual((root / "active-receipt.json").read_bytes(), original_receipt)
            self.checkpoint(receipt, "restored", "incumbent", shadow="absent", archived=True)


if __name__ == "__main__":
    unittest.main()
