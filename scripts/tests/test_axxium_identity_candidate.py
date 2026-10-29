"""Boundary tests for a review artifact, not proof of a deployed ingress."""
import hashlib
import ipaddress
import importlib.util
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("candidate", ROOT / "scripts/axxium_identity_candidate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.old = ("axxium.promethean.rest, *.axxium.promethean.rest {\n" + module.OLD
                    + "\n\t\t}\n\t}\n\thandle {\n\t\treverse_proxy axxium-pds:3000\n\t}\n}\n").encode()
        self.sha = hashlib.sha256(self.old).hexdigest()

    def test_route_is_host_scoped_pds_preserved_and_all_axxium_paths_use_one_trusted_peer(self):
        result = module.candidate(self.old, self.sha).decode()
        self.assertIn("host axxium.promethean.rest\n\t\tpath / /health", result)
        self.assertIn(" /oidc /oidc/*\n", result)
        self.assertEqual(result.count("reverse_proxy axxium-identity-ingress:8787"), 1)
        self.assertIn("reverse_proxy axxium-pds:3000", result)
        self.assertEqual(result.replace(module.NEW, module.OLD).encode(), self.old)

    def test_changed_or_ambiguous_baseline_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "baseline"):
            module.candidate(self.old + b"# drift\n", self.sha)
        duplicate = self.old + self.old
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            module.candidate(duplicate, hashlib.sha256(duplicate).hexdigest())
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            module.candidate(b"unrelated", hashlib.sha256(b"unrelated").hexdigest())

    def test_subnet_collision_is_fail_closed(self):
        self.assertTrue(module.subnet_available(["default", "172.31.255.0/28", "172.18.0.0/16"]))
        self.assertFalse(module.subnet_available(["172.30.0.0/16"]))
        self.assertFalse(module.subnet_available(["172.30.115.2/32"]))
        with self.assertRaises(ValueError):
            module.subnet_available(["not-a-network"])

    def test_isolated_network_and_single_exact_proxy_identity(self):
        caddy = yaml.safe_load((ROOT / "digitalocean/services/caddy/compose.axxium-identity.candidate.yaml").read_text())
        axxium = yaml.safe_load((ROOT / "digitalocean/environments/axxium-identity-ingress.candidate.yaml").read_text())
        net = caddy["networks"]["axxium-identity-ingress"]
        self.assertIs(net["internal"], True)
        self.assertEqual(net["ipam"]["config"][0]["subnet"], str(module.SUBNET))
        self.assertTrue(axxium["networks"]["axxium-identity-ingress"]["external"])
        caddy_net = caddy["services"]["caddy"]["networks"]["axxium-identity-ingress"]
        axxium_net = axxium["services"]["axxium"]["networks"]["axxium-identity-ingress"]
        peer = caddy_net["ipv4_address"]
        self.assertEqual(axxium["services"]["axxium"]["environment"]["AXXIUM_TRUST_PROXY"], peer)
        self.assertEqual(axxium_net["aliases"], ["axxium-identity-ingress"])
        self.assertIn(ipaddress.ip_address(peer), module.SUBNET)
        self.assertIn(ipaddress.ip_address(axxium_net["ipv4_address"]), module.SUBNET)
        self.assertNotEqual(peer, axxium_net["ipv4_address"])
        self.assertNotIn("dev-ingress", caddy["services"]["caddy"]["networks"])
        service = caddy["services"]["caddy"]
        self.assertEqual(service["command"], ["caddy", "run", "--config", "/etc/caddy/identity-ingress/Caddyfile", "--adapter", "caddyfile"])
        self.assertEqual(service["healthcheck"]["test"], ["CMD", "caddy", "validate", "--config", "/etc/caddy/identity-ingress/Caddyfile", "--adapter", "caddyfile"])
        mount = service["volumes"][0]
        self.assertEqual(mount["source"], "${CADDY_STATE_PATH}/identity-ingress")
        self.assertEqual(mount["target"], "/etc/caddy/identity-ingress")
        self.assertIs(mount["read_only"], True)


if __name__ == "__main__":
    unittest.main()
