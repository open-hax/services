"""Verify proxy trust against a dedicated or restored shared bridge."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "digitalocean/ingress/axxium-production"
SPEC = importlib.util.spec_from_file_location("verify_peer", CANDIDATE / "verify-proxy-peer.py")
PEER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PEER)
CONTRACT = json.loads((CANDIDATE / "network.json").read_bytes())


class ProxyPeerTest(unittest.TestCase):
    def setUp(self):
        self.network = {
            "Name": "axxium-ingress", "Id": "dedicated-net",
            "Driver": "bridge", "Scope": "local", "Internal": True,
            "IPAM": {"Config": [{"Subnet": "172.30.115.0/28", "Gateway": "172.30.115.1",
                                 "IPRange": "172.30.115.8/29"}]},
            "Containers": {"proxy": {"IPv4Address": "172.30.115.2/28"},
                           "app": {"IPv4Address": "172.30.115.9/28"}},
        }
        self.aliases = {"proxy": ["caddy"], "app": ["axxium-ingress"]}

    def test_accepts_dedicated_peer_and_refuses_topology_drift(self):
        def verify(network, aliases):
            return PEER.verify_peer("dedicated", CONTRACT, network, "proxy", "app",
                                    "172.30.115.2", aliases)

        self.assertEqual({"network": "axxium-ingress", "network_id": "dedicated-net",
                          "caddy_id": "proxy", "axxium_id": "app", "upstream": "axxium-ingress:8787",
                          "proxy_address": "172.30.115.2"}, verify(self.network, self.aliases))
        changed_pool = copy.deepcopy(self.network)
        changed_pool["IPAM"]["Config"][0]["IPRange"] = "172.30.115.0/28"
        implicit_gateway = copy.deepcopy(self.network)
        implicit_gateway["IPAM"]["Config"][0]["Gateway"] = "172.30.115.8"
        extra = copy.deepcopy(self.network)
        extra["Containers"]["unrelated"] = {"IPv4Address": "172.30.115.10/28"}
        wrong_address = copy.deepcopy(self.network)
        wrong_address["Containers"]["app"]["IPv4Address"] = "172.30.115.3/28"
        for network, aliases in (
            (changed_pool, self.aliases),
            (implicit_gateway, self.aliases),
            (extra, {**self.aliases, "unrelated": ["unrelated"]}),
            (wrong_address, self.aliases),
            (self.network, {"proxy": ["axxium-ingress"], "app": ["axxium-ingress"]}),
            (self.network, {"proxy": [], "app": "not-axxium-ingress"}),
            (self.network, {"app": ["axxium-ingress"]}),
        ):
            with self.subTest(network=network, aliases=aliases), self.assertRaises(ValueError):
                verify(network, aliases)

    def test_rollback_requires_restored_apps_trust_to_match_new_shared_peer(self):
        shared = {"Name": "open-hax", "Id": "shared-net", "Driver": "bridge", "Scope": "local",
                  "IPAM": {"Config": [{"Subnet": "172.18.0.0/16"}]},
                  "Containers": {"proxy-restored": {"IPv4Address": "172.18.0.14/16"},
                                 "app-restored": {"IPv4Address": "172.18.0.6/16"},
                                 "pds": {"IPv4Address": "172.18.0.3/16"}}}
        aliases = {"proxy-restored": ["caddy"], "app-restored": ["axxium"], "pds": ["axxium-pds"]}
        with self.assertRaisesRegex(ValueError, "exactly the observed Caddy address"):
            PEER.verify_peer("shared", CONTRACT, shared, "proxy-restored", "app-restored",
                             "172.18.0.9", aliases)
        self.assertEqual({"network": "open-hax", "network_id": "shared-net", "upstream": "axxium:8787",
                          "caddy_id": "proxy-restored", "axxium_id": "app-restored",
                          "proxy_address": "172.18.0.14"},
                         PEER.verify_peer("shared", CONTRACT, shared, "proxy-restored",
                                          "app-restored", "172.18.0.14", aliases))
