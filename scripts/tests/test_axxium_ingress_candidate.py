"""Exercise the isolated production ingress candidate and its Compose merge."""

import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "digitalocean/ingress/axxium-production"
SERVICES = ROOT / "digitalocean/services"
SPEC = importlib.util.spec_from_file_location("render_ingress", CANDIDATE / "render-caddy-root.py")
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)


class AxxiumIngressCandidateTest(unittest.TestCase):
    def setUp(self):
        self.snippet = (CANDIDATE / "axxium-production.caddy").read_bytes()
        self.incumbent_site = self.snippet.replace(RENDER.NEW_UPSTREAM, RENDER.OLD_UPSTREAM)
        self.before_site = b"{\n\ton_demand_tls {\n" + RENDER.PDS_ASK + b"\t}\n}\n\n"
        self.after_site = b"\nother.invalid {\n\trespond 200\n}\n" + RENDER.MANAGED_IMPORT
        self.incumbent = self.before_site + self.incumbent_site + self.after_site

    def render(self, content):
        return RENDER.render(content, hashlib.sha256(content).hexdigest(), self.snippet)

    def test_extraction_changes_only_exact_incumbent_site(self):
        self.assertEqual(self.before_site + self.after_site, self.render(self.incumbent))
        for changed in (
            self.incumbent.replace(b" /oidc/*", b""),
            self.incumbent + self.incumbent_site,
            self.incumbent.replace(RENDER.MANAGED_IMPORT, b""),
            self.incumbent.replace(RENDER.PDS_ASK, b""),
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.render(changed)
        pinned = hashlib.sha256(self.incumbent).hexdigest()
        with self.assertRaises(ValueError):
            RENDER.render(self.incumbent + b"\n", pinned, self.snippet)
        with self.assertRaises(ValueError):
            RENDER.render(self.incumbent, pinned, self.snippet.replace(b" /oidc/*", b""))

    def test_candidate_output_is_private_and_cannot_replace_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / "Caddyfile.candidate"
            RENDER.write_new_private(candidate, self.incumbent)
            self.assertEqual(0o600, candidate.stat().st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                RENDER.write_new_private(candidate, b"changed")
            self.assertEqual(self.incumbent, candidate.read_bytes())
            self.assertEqual([candidate], list(directory.iterdir()))

    def test_did_matches_incumbent_public_bytes(self):
        data = (CANDIDATE / "axxium-did.json").read_bytes()
        self.assertEqual("9205d70921e983850369de190cbb4130f0d22c48fc1a2704eef45db2b32d6343",
                         hashlib.sha256(data).hexdigest())

    @unittest.skipUnless(os.environ.get("CADDY_TEST_BINARY"), "Caddy adapter binary unavailable")
    def test_real_adapter_preserves_other_routes_when_importing_the_candidate(self):
        root = self.incumbent.replace(self.before_site, self.before_site + b"(common) {\n\tencode gzip\n}\n")
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            config = fixture / "Caddyfile"
            managed = fixture / "managed"
            managed.mkdir()
            (managed / "neighbor.caddy").write_bytes(b"neighbor.invalid {\n\trespond 200\n}\n")

            def adapt(contents):
                config.write_bytes(contents.replace(RENDER.MANAGED_IMPORT, b"\nimport managed/*.caddy\n"))
                result = subprocess.run([os.environ["CADDY_TEST_BINARY"], "adapt", "--config", str(config),
                                         "--adapter", "caddyfile"], capture_output=True, check=True, timeout=10)

                def normalize(item):
                    if item.get("handler") == "file_server":
                        item.pop("hide", None)  # Caddy includes each source config path.
                    return item

                return json.loads(result.stdout, object_hook=normalize)

            baseline = adapt(root)
            (managed / "axxium-production.caddy").write_bytes(self.snippet)
            candidate = adapt(self.render(root))
        candidate_json = json.dumps(candidate)
        self.assertEqual(1, candidate_json.count("axxium-ingress:8787"))
        self.assertEqual(baseline, json.loads(candidate_json.replace("axxium-ingress:8787", "axxium:8787")))

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is required for the overlay integration check")
    def test_compose_overlays_keep_existing_dependencies_and_mounts(self):
        network = json.loads((CANDIDATE / "network.json").read_bytes())
        bridge = ipaddress.ip_network(network["subnet"])
        dynamic = ipaddress.ip_network(network["dynamicPool"])
        peer = ipaddress.ip_address(network["proxyAddress"])
        self.assertTrue(dynamic.subnet_of(bridge))
        self.assertTrue(network["internal"])
        self.assertEqual("bridge", network["driver"])
        self.assertIn(peer, bridge)
        self.assertNotIn(peer, dynamic)
        self.assertNotEqual(str(peer), network["gateway"])

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            oidc = runtime / "retained-oidc.yaml"
            oidc.write_text("services:\n  axxium:\n    volumes:\n"
                            f"      - {runtime}/oidc:/run/axxium/oidc:ro\n")
            environment = {**os.environ, "CADDY_STATE_PATH": str(runtime / "caddy"),
                           "AXXIUM_STATE_PATH": str(runtime / "axxium"), "AXXIUM_IMAGE": "fixture:local",
                           "AXXIUM_PUBLIC_HOST": "axxium.promethean.rest"}
            for name in (
                "KNOXX_PUBLIC_HOST", "PROXX_PUBLIC_HOST", "WEBSITE_PUBLIC_HOST", "CADDY_ACME_EMAIL",
                "CADDY_KNOXX_DEV_HOST", "CADDY_OPENCODE_DEV_HOST", "CADDY_SHADOW_DEV_HOST",
                "DEV_BASIC_AUTH_USER", "DEV_BASIC_AUTH_HASH", "AXXIUM_DB_PASSWORD", "AXXIUM_JWT_SECRET",
                "AXXIUM_BOOTSTRAP_ADMIN_EMAIL", "PDS_JWT_SECRET", "PDS_ADMIN_PASSWORD",
                "PDS_PLC_ROTATION_KEY_K256_PRIVATE_KEY_HEX",
            ):
                environment[name] = "synthetic.invalid"

            def config(service, *layers):
                args = ["docker", "compose", "--env-file", "/dev/null",
                        "-f", str(SERVICES / service / "compose.yaml")]
                for layer in layers:
                    args.extend(("-f", str(layer)))
                result = subprocess.run([*args, "config", "--format", "json"], env=environment,
                                        capture_output=True, text=True, timeout=10, check=True)
                return json.loads(result.stdout)

            caddy_base = config("caddy")["services"]["caddy"]
            caddy = config("caddy", CANDIDATE / "caddy.overlay.yaml")
            axxium_base = config("axxium", oidc)["services"]
            axxium = config("axxium", oidc, CANDIDATE / "axxium.overlay.yaml")

        bridge_name = network["name"]
        for candidate in (caddy, axxium):
            external = candidate["networks"][bridge_name]
            self.assertEqual(bridge_name, external["name"])
            self.assertIs(True, external["external"])
            self.assertFalse(external.get("ipam"))
        caddy_service = caddy["services"]["caddy"]
        self.assertEqual(str(peer), caddy_service["networks"][bridge_name]["ipv4_address"])
        self.assertEqual(caddy_base["networks"],
                         {k: v for k, v in caddy_service["networks"].items() if k != bridge_name})
        caddy_mounts = {entry["target"]: entry for entry in caddy_service["volumes"]}
        self.assertTrue(all(caddy_mounts[entry["target"]] == entry for entry in caddy_base["volumes"]))
        did_mount = caddy_mounts["/etc/caddy/axxium-identity/did.json"]
        self.assertEqual(str(SERVICES / "caddy" / "axxium-did.json"), did_mount["source"])
        self.assertTrue(did_mount["read_only"])
        self.assertFalse(did_mount.get("bind", {}).get("create_host_path", False))

        services = axxium["services"]
        app = services["axxium"]
        self.assertEqual(str(peer), app["environment"]["AXXIUM_TRUST_PROXY"])
        self.assertEqual([network["appAlias"]], app["networks"][bridge_name]["aliases"])
        self.assertEqual(axxium_base["axxium"]["networks"],
                         {k: v for k, v in app["networks"].items() if k != bridge_name})
        self.assertEqual(axxium_base["axxium"]["volumes"], app["volumes"])
        for name in ("axxium-db", "pds"):
            self.assertEqual(axxium_base[name], services[name])
