"""Check pinned Caddy adaptation and serve the frontend on isolated loopback."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
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
        cls.binary = str(binary)
        cls.environment = {"HOME": temporary.name, "PATH": os.defpath}
        version = subprocess.run([cls.binary, "version"], env=cls.environment,
                                 capture_output=True, text=True, timeout=10, check=True)
        if version.stdout.split()[0] != "v2.8.4":
            raise RuntimeError("Routing controls require the pinned Caddy 2.8.4 tool")
        adapted = subprocess.run(
            [cls.binary, "adapt", "--config", str(CONFIG), "--adapter", "caddyfile"],
            env=cls.environment, capture_output=True, text=True, timeout=10, check=True,
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

    def test_anonymous_privacy_uses_the_document_and_fails_closed_if_missing(self):
        with tempfile.TemporaryDirectory(prefix="knoxx frontend ") as temporary:
            root = Path(temporary) / "site"
            document = root / "bridge/legal/privacy.html"
            document.parent.mkdir(parents=True)
            shell = b"<!doctype html><title>Knoxx application</title>"
            privacy = b"<!doctype html><title>Knoxx Privacy Policy</title>"
            (root / "index.html").write_bytes(shell)
            document.write_bytes(privacy)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            config = Path(temporary) / "Caddyfile"
            source = CONFIG.read_text().replace(":8080 {", f"http://127.0.0.1:{port} {{", 1)
            config.write_text(source.replace("root * /srv", f"root * {json.dumps(str(root))}"))
            with subprocess.Popen(
                [self.binary, "run", "--config", str(config), "--adapter", "caddyfile"],
                env=self.environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            ) as caddy:
                try:
                    self.wait_for_listener(caddy, port)
                    status, headers, body = self.request(port, "GET", "/privacy.html?from=home")
                    self.assertEqual((status, body), (200, privacy))
                    self.assertEqual(headers["content-type"].split(";")[0], "text/html")
                    self.assertNotIn("location", headers)
                    status, headers, body = self.request(port, "HEAD", "/privacy.html")
                    self.assertEqual((status, headers["content-length"], body), (200, str(len(privacy)), b""))
                    for method in ("POST", "OPTIONS"):
                        with self.subTest(method=method):
                            status, headers, body = self.request(port, method, "/privacy.html")
                            self.assertEqual((status, headers["allow"], body), (405, "GET, HEAD", b""))

                    document.unlink()
                    for method in ("GET", "HEAD"):
                        with self.subTest(missing_document=method):
                            status, _, body = self.request(port, method, "/privacy.html")
                            self.assertEqual((status, body), (404, b""))
                    for path in ("/", "/privacy.html-other", "/Privacy.html"):
                        with self.subTest(spa_route=path):
                            status, _, body = self.request(port, "GET", path)
                            self.assertEqual((status, body), (200, shell))
                finally:
                    caddy.terminate()
                    try:
                        caddy.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        caddy.kill()
                        caddy.communicate(timeout=5)

    def wait_for_listener(self, caddy, port):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if caddy.poll() is not None:
                self.fail(caddy.stderr.read().decode(errors="replace"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        self.fail("Isolated frontend Caddy did not start")

    @staticmethod
    def request(port, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            headers = {name.lower(): value for name, value in response.getheaders()}
            return response.status, headers, response.read()
        finally:
            connection.close()

    def test_private_frontend_keeps_admin_and_automatic_https_off(self):
        self.assertTrue(self.adapted["admin"]["disabled"])
        self.assertTrue(self.server["automatic_https"]["disable"])
        self.assertEqual(self.server["listen"], [":8080"])


class ManagedAxxiumCaddyTest(unittest.TestCase):
    """Adapt actual managed source sections without host imports or listeners."""

    @classmethod
    def setUpClass(cls):
        binary = Path(os.environ["CADDY_TEST_BINARY"]).resolve(strict=True)
        temporary = tempfile.TemporaryDirectory(prefix="managed axxium caddy ")
        cls.addClassCleanup(temporary.cleanup)
        environment = {"HOME": temporary.name, "PATH": os.defpath}
        version = subprocess.run([str(binary), "version"], env=environment,
                                 capture_output=True, text=True, timeout=10, check=True)
        if version.stdout.split()[0] != "v2.8.4":
            raise RuntimeError("Managed ingress controls require pinned Caddy 2.8.4")
        source = (CONFIG.parents[1] / "services/caddy/Caddyfile").read_text()
        markers = ("(common) {", "(knoxx_mcp_backend) {",
                   "# BEGIN managed Axxium host environments", "# END managed Axxium host environments")
        if any(source.count(marker) != 1 for marker in markers):
            raise RuntimeError("Actual ingress sections must have unique source delimiters")
        common = source[source.index(markers[0]):source.index(markers[1])]
        sites = source[source.index(markers[2]):source.index(markers[3])]
        # Include the current Knoxx sites even before managed markers are added,
        # so missing markers are an assertion failure rather than a setup error.
        knoxx_sites = source[source.index("stealth.knoxx.promethean.rest {"):
                             source.index("# Durable separately managed environment routes")]
        cls.ingress_source = source
        config = Path(temporary.name) / "Caddyfile"
        config.write_text(common + sites + knoxx_sites)
        adapted = subprocess.run([str(binary), "adapt", "--config", str(config), "--adapter", "caddyfile"],
                                 env=environment, capture_output=True, text=True, timeout=10, check=True)
        cls.adapted = json.loads(adapted.stdout)

    @staticmethod
    def objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from ManagedAxxiumCaddyTest.objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from ManagedAxxiumCaddyTest.objects(child)

    def site(self, host):
        routes = [value for server in self.adapted["apps"]["http"]["servers"].values()
                  for value in self.objects(server.get("routes", []))
                  if value.get("match") == [{"host": [host]}]]
        self.assertEqual(1, len(routes), host)
        return list(self.objects(routes[0]))

    def test_both_managed_axxium_upstreams_and_acme_issuers_remain(self):
        for name, port in (("stealth", 19077), ("yoga", 19078)):
            host = f"{name}.axxium.promethean.rest"
            with self.subTest(host=host):
                proxies = [value for value in self.site(host) if value.get("handler") == "reverse_proxy"]
                self.assertEqual(1, len(proxies))
                self.assertEqual([{"dial": f"172.31.255.1:{port}"}], proxies[0]["upstreams"])
                policies = self.adapted["apps"]["tls"]["automation"]["policies"]
                self.assertTrue(any(host in policy["subjects"] and policy["issuers"] == [{"module": "acme"}]
                                    for policy in policies))

    def test_both_managed_axxium_sites_strip_bridge_header(self):
        for name in ("stealth", "yoga"):
            with self.subTest(host=name):
                proxy = next(value for value in self.site(f"{name}.axxium.promethean.rest")
                             if value.get("handler") == "reverse_proxy")
                deleted = proxy.get("headers", {}).get("request", {}).get("delete", [])
                self.assertIn("x-open-hax-bridge-auth", [header.lower() for header in deleted])

    def test_both_managed_axxium_sites_keep_common_security_headers(self):
        expected = {"Strict-Transport-Security": ["max-age=31536000; includeSubDomains"],
                    "X-Content-Type-Options": ["nosniff"], "X-Frame-Options": ["SAMEORIGIN"],
                    "Referrer-Policy": ["strict-origin-when-cross-origin"]}
        for name in ("stealth", "yoga"):
            with self.subTest(host=name):
                responses = [value["response"] for value in self.site(f"{name}.axxium.promethean.rest")
                             if value.get("handler") == "headers" and "response" in value]
                self.assertEqual(1, len(responses))
                self.assertEqual(expected, responses[0]["set"])
                self.assertIn("server", [header.lower() for header in responses[0]["delete"]])

    def test_knoxx_hosts_have_unique_managed_section_ownership(self):
        begin = "# BEGIN managed Knoxx host environments"
        end = "# END managed Knoxx host environments"
        self.assertEqual(1, self.ingress_source.count(begin))
        self.assertEqual(1, self.ingress_source.count(end))
        start, stop = self.ingress_source.index(begin), self.ingress_source.index(end)
        self.assertLess(start, stop)
        for name in ("stealth", "yoga"):
            site = f"{name}.knoxx.promethean.rest {{"
            with self.subTest(host=name):
                self.assertEqual(1, self.ingress_source.count(site))
                self.assertLess(start, self.ingress_source.index(site))
                self.assertLess(self.ingress_source.index(site), stop)

    def test_all_managed_host_sites_select_acme(self):
        policies = self.adapted["apps"]["tls"]["automation"]["policies"]
        for service in ("axxium", "knoxx"):
            for name in ("stealth", "yoga"):
                host = f"{name}.{service}.promethean.rest"
                with self.subTest(host=host):
                    self.assertTrue(any(host in policy["subjects"]
                                        and policy.get("issuers") == [{"module": "acme"}]
                                        for policy in policies), host)

    def test_all_managed_host_sites_return_uncached_503_for_upstream_errors(self):
        for service in ("axxium", "knoxx"):
            for name in ("stealth", "yoga"):
                host = f"{name}.{service}.promethean.rest"
                with self.subTest(host=host):
                    errors = [route for server in self.adapted["apps"]["http"]["servers"].values()
                              for route in server.get("errors", {}).get("routes", [])
                              if route.get("match") == [{"host": [host]}]]
                    self.assertEqual(1, len(errors), host)
                    handlers = list(self.objects(errors[0]))
                    responses = [value for value in handlers
                                 if value.get("handler") == "static_response"]
                    self.assertEqual(1, len(responses))
                    self.assertEqual(503, responses[0]["status_code"])
                    self.assertEqual("Service not configured or unavailable.", responses[0]["body"])
                    headers = [value["response"]["set"] for value in handlers
                               if value.get("handler") == "headers"]
                    self.assertEqual([{"Cache-Control": ["no-store"]}], headers)


if __name__ == "__main__":
    unittest.main()
