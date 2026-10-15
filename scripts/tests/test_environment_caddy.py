"""Inspect real Caddy 2.8.4 adapter output; no listener or backend is started.

These controls prove configuration routing, not live HTTP, authentication or MCP
protocol behavior. The owning workflow supplies the checksum-pinned test tool.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


CONFIG = Path(__file__).resolve().parents[2] / "digitalocean/environments/KnoxxFrontend.Caddyfile"


class EnvironmentCaddyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = os.environ.get("CADDY_TEST_BINARY")
        if not binary:
            raise RuntimeError("CADDY_TEST_BINARY must name the verified Caddy 2.8.4 tool")
        binary = Path(binary).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError("CADDY_TEST_BINARY is not an executable file")
        temporary = tempfile.TemporaryDirectory(prefix="environment caddy ")
        cls.addClassCleanup(temporary.cleanup)
        environment = {"HOME": temporary.name, "PATH": os.defpath}
        version = subprocess.run([str(binary), "version"], env=environment,
                                 capture_output=True, text=True, timeout=10, check=True)
        if version.stdout.split()[0] != "v2.8.4":
            raise RuntimeError("Routing controls require the pinned Caddy 2.8.4 tool")
        adapted = subprocess.run(
            [str(binary), "adapt", "--config", str(CONFIG), "--adapter", "caddyfile"],
            env=environment, capture_output=True, text=True, timeout=10, check=True,
        )
        cls.adapted = json.loads(adapted.stdout)
        cls.server = cls.adapted["apps"]["http"]["servers"]["srv0"]
        cls.routes = cls.server["routes"]

    def route(self, pattern):
        matches = [route for route in self.routes
                   if route.get("match") == [{"path": [pattern]}]]
        self.assertEqual(len(matches), 1, f"Actual adapter output needs one {pattern} route")
        return matches[0]

    def handlers(self, route):
        subroute = route["handle"][0]
        self.assertEqual(subroute["handler"], "subroute")
        return [handler for nested in subroute["routes"] for handler in nested["handle"]]

    def backend_proxy(self, route):
        handlers = self.handlers(route)
        self.assertEqual([handler["handler"] for handler in handlers], ["reverse_proxy"])
        proxy = handlers[0]
        self.assertEqual(proxy["upstreams"], [{"dial": "backend:8000"}])
        self.assertNotIn("rewrite", proxy, "Preserve the root endpoint path")
        fallback = [item for item in self.routes if "match" not in item]
        self.assertEqual(len(fallback), 1)
        self.assertLess(self.routes.index(route), self.routes.index(fallback[0]))
        self.assertEqual(route["group"], fallback[0]["group"])
        return proxy

    def test_root_mcp_preserves_backend_streaming_and_header_boundary(self):
        proxy = self.backend_proxy(self.route("/mcp*"))
        self.assertEqual(proxy["flush_interval"], -1)
        self.assertEqual(proxy["transport"]["read_timeout"], 3600 * 10**9)
        self.assertEqual(proxy["transport"]["write_timeout"], 3600 * 10**9)
        self.assertIn("x-open-hax-bridge-auth",
                      [header.lower() for header in proxy["headers"]["request"]["delete"]])

    def test_root_oauth_discovery_reaches_backend_before_spa(self):
        proxy = self.backend_proxy(self.route("/.well-known/oauth-*"))
        self.assertIn("x-open-hax-bridge-auth",
                      [header.lower() for header in proxy["headers"]["request"]["delete"]])

    def test_api_health_and_websocket_routes_stay_backend_proxies(self):
        for pattern in ("/api/*", "/health", "/ws*"):
            with self.subTest(pattern=pattern):
                self.backend_proxy(self.route(pattern))

    def test_ordinary_ui_keeps_final_spa_fallback(self):
        fallback = self.routes[-1]
        self.assertNotIn("match", fallback)
        handlers = self.handlers(fallback)
        self.assertEqual([handler["handler"] for handler in handlers],
                         ["vars", "rewrite", "file_server"])
        self.assertEqual(handlers[0]["root"], "/srv")
        files = fallback["handle"][0]["routes"][1]["match"][0]["file"]["try_files"]
        self.assertEqual(files, ["{http.request.uri.path}", "/index.html"])

    def test_private_frontend_keeps_admin_and_automatic_https_off(self):
        self.assertTrue(self.adapted["admin"]["disabled"])
        self.assertTrue(self.server["automatic_https"]["disable"])
        self.assertEqual(self.server["listen"], [":8080"])


if __name__ == "__main__":
    unittest.main()
