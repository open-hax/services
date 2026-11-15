"""Docker and Caddy ingress driver for an independently admitted host plan."""

from copy import deepcopy
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import time

import axxium_ingress_caddy as caddy
import axxium_ingress_docker as docker
import axxium_ingress_host as controller
import axxium_ingress_transaction as journal
import axxium_minimal_release as production
from axxium_oidc_ingress import MAINTENANCE, SITE, UPSTREAM


ROUTE = "axxium-production.caddy"
SHARED = "open-hax"
ROOT = Path(__file__).resolve().parents[1]
NETWORK = ROOT / "digitalocean/ingress/axxium-production/network.json"
DEPENDENCIES = ("database", "pds")
SERVICES = ("proxy", "app")
SWITCHES = {"select_dedicated": "dedicated", "restore_shared": "shared"}
INVENTORY_PINS = {"image", "config_sha256", "environment_sha256", "mounts_sha256"}
BRIDGE_OWNER = "org.open-hax.axxium-ingress.plan"
DEDICATED_UPSTREAM = UPSTREAM.replace(b"axxium:8787", b"axxium-ingress:8787")


@dataclass(frozen=True)
class ComposeSelection:
    project: str
    directory: Path
    service: str
    inputs: tuple
    environment: tuple
    image: str
    image_id: str
    resolved_sha256: str

    def pins(self):
        return {"project": self.project, "directory": str(self.directory), "service": self.service,
                "inputs": [(str(path), digest) for path, digest in self.inputs],
                "environment": (str(self.environment[0]), self.environment[1]),
                "image": self.image, "image_id": self.image_id, "resolved_sha256": self.resolved_sha256}

    def recreate(self, host, trusted_peer=None):
        """Use the pre-admitted Compose inputs and only the selected service."""
        if (self.project, self.directory, self.service) not in (
            ("caddy", production.CADDY.parent, "caddy"),
            ("axxium", production.RUNTIME, "axxium"),
        ):
            raise journal.Held("Compose service is outside the ingress operation")
        args = ["docker", "compose", "--project-name", self.project, "--project-directory", str(self.directory)]
        for path, expected in (*self.inputs, self.environment):
            if path.resolve(strict=True) != path or production.file_digest(path) != expected:
                raise journal.Held("Admitted Compose input changed")
        args.extend(("--env-file", str(self.environment[0])))
        for path, _ in self.inputs:
            args.extend(("-f", str(path)))
        with tempfile.TemporaryDirectory(prefix="axxium-ingress-compose-") as scratch:
            if trusted_peer is not None:
                if self.service != "axxium":
                    raise journal.Held("Proxy peer override is only valid for Axxium")
                peer = str(ipaddress.IPv4Address(trusted_peer))
                override = Path(scratch) / "trust.json"
                host.private_write(override, journal.json_bytes(
                    {"services": {"axxium": {"environment": {"AXXIUM_TRUST_PROXY": peer}}}}
                ))
                args.extend(("-f", str(override)))
            resolved = json.loads(host.command([*args, "config", "--format", "json", self.service]))
            service = resolved["services"][self.service]
            if trusted_peer is not None and service["environment"].pop("AXXIUM_TRUST_PROXY") != peer:
                raise journal.Held("Resolved Compose trusted proxy differs")
            if (service["image"] != self.image
                    or production.digest(journal.json_bytes(resolved)) != self.resolved_sha256
                    or host.command(["docker", "image", "inspect", "--format", "{{.Id}}", self.image]).decode().strip()
                    != self.image_id):
                raise journal.Held("Resolved Compose configuration or local image differs")
            host.recheck_lock()
            host.command([*args, "up", "-d", "--force-recreate", "--no-deps", "--pull", "never",
                          "--wait", "--wait-timeout", "120", self.service],
                         timeout=660, effect=True)


@dataclass(frozen=True)
class RouteSelection:
    root: bytes
    snippet: bytes | None
    adapted_sha256: str


def _validate_routes(routes):
    shared = routes["shared_live"]
    dedicated = routes["dedicated_live"]
    if (not dedicated.snippet or not dedicated.snippet.startswith(SITE)
            or dedicated.snippet.count(DEDICATED_UPSTREAM) != 1):
        raise journal.Held("Dedicated Axxium route does not preserve the incumbent site")
    inline = dedicated.snippet.replace(DEDICATED_UPSTREAM, UPSTREAM, 1)
    if shared.root.count(SITE) != 1 or shared.root.count(inline) != 1 or shared.root.count(UPSTREAM) != 1:
        raise journal.Held("The admitted live Axxium site is not the exact incumbent")
    base = shared.root.replace(inline, b"", 1)
    shared_fence = shared.root.replace(UPSTREAM, MAINTENANCE, 1)
    dedicated_fence = dedicated.snippet.replace(DEDICATED_UPSTREAM, MAINTENANCE, 1)
    expected = {
        "shared_live": (shared.root, None),
        "shared_fenced": (shared_fence, None),
        "dedicated_fenced": (base, dedicated_fence),
        "dedicated_live": (base, dedicated.snippet),
        "restored_fenced": (shared_fence, None),
        "restored_live": (shared.root, None),
    }
    if any((route.root, route.snippet) != expected[state] or not journal.sha256(route.adapted_sha256)
           for state, route in routes.items()):
        raise journal.Held("Ingress route changes extend outside the admitted Axxium site or fence")


class NativeIngress:
    """Bind concrete host effects and readback to the journal phase machine.

    A protected caller supplies admitted immutable snapshots, resolved Compose
    pins and the baseline container fingerprints. It owns the production queue.
    """

    def __init__(self, *, operation_id, routes, managed_siblings, inventory, compositions, did_sha256):
        self.operation_id = operation_id
        self.routes = dict(routes)
        self.managed_siblings = dict(managed_siblings)
        self.inventory = deepcopy(inventory)
        self.compositions = dict(compositions)
        self.did_sha256 = did_sha256
        self.network = json.loads(NETWORK.read_bytes())
        states = journal.STATES - {"unknown"}
        if (set(self.routes) != states or set(self.inventory) != states or ROUTE in self.managed_siblings
                or set(self.compositions) != {"shared", "dedicated"} or not journal.sha256(did_sha256)):
            raise journal.Held("Admitted ingress plan does not cover the phase machine")
        _validate_routes(self.routes)
        baseline = self.inventory["shared_live"]
        for state, records in self.inventory.items():
            if set(records) != set((*SERVICES, *DEPENDENCIES)):
                raise journal.Held("Admitted ingress inventory must bind every service")
            for service, pins in records.items():
                include_id = state.startswith("shared_") or service in DEPENDENCIES
                expected_keys = INVENTORY_PINS | ({"id"} if include_id else set())
                if set(pins) != expected_keys or any(not value for value in pins.values()):
                    raise journal.Held("Admitted service fingerprints are incomplete")
                if (pins["image"] != baseline[service]["image"]
                        or (include_id and pins["id"] != baseline[service]["id"])
                        or (service in DEPENDENCIES and pins != baseline[service])):
                    raise journal.Held("Admitted service image or dependency changes across phases")
        for proxy, app in self.compositions.values():
            if (proxy.project, proxy.service, app.project, app.service) != ("caddy", "caddy", "axxium", "axxium"):
                raise journal.Held("Admitted Compose selections are in the wrong phase role")
            if proxy.image_id != baseline["proxy"]["image"] or app.image_id != baseline["app"]["image"]:
                raise journal.Held("Compose selections must preserve the incumbent images")

    def plan_sha256(self):
        return production.digest(journal.json_bytes({
            "operation": self.operation_id,
            "routes": {state: {"root": production.digest(route.root),
                               "snippet": None if route.snippet is None else production.digest(route.snippet),
                               "adapted": route.adapted_sha256} for state, route in self.routes.items()},
            "managed_siblings": self.managed_siblings, "inventory": self.inventory,
            "compositions": {mode: [selection.pins() for selection in selections]
                             for mode, selections in self.compositions.items()},
            "network": self.network, "did_sha256": self.did_sha256,
        }))

    @staticmethod
    def mode(state):
        return "dedicated" if state.startswith("dedicated_") else "shared"

    def _caddy(self, host):
        inspected = host.inspect(production.PROXY)
        proxy = inspected["Id"]
        asset = production.CADDY.with_name("axxium-did.json")
        target = "/etc/caddy/axxium-identity/did.json"
        mounts = [mount for mount in inspected["Mounts"] if mount["Destination"] == target]
        if (len(mounts) != 1 or mounts[0]["Type"] != "bind" or mounts[0]["Source"] != str(asset)
                or mounts[0]["RW"] is not False or asset.is_symlink() or not asset.is_file()
                or production.file_digest(asset) != self.did_sha256
                or production.digest(host.command(["docker", "exec", proxy, "cat", target])) != self.did_sha256):
            raise journal.Held("Caddy DID mount differs from the admitted asset")
        return proxy, caddy.readback(host, proxy, production.CADDY, production.MANAGED)

    def _runtime(self, host, mode):
        return docker.readback(host, proxy=production.PROXY, app=production.APP, database=production.DB,
                               pds=production.PDS, shared=SHARED, dedicated=self.network["name"],
                               contract=self.network, mode=mode)

    def _route_matches(self, state, observed):
        route = self.routes[state]
        managed = dict(self.managed_siblings)
        if route.snippet is not None:
            managed[ROUTE] = production.digest(route.snippet)
        return observed == {"root_sha256": production.digest(route.root), "managed": managed,
                            "startup_sha256": route.adapted_sha256, "active_sha256": route.adapted_sha256}

    def _matches_inventory(self, state, runtime):
        actual = runtime["containers"]
        expected = self.inventory[state]
        return (all(all(actual[service].get(key) == value for key, value in expected[service].items())
                    for service in (*SERVICES, *DEPENDENCIES))
                and (state.startswith("shared_")
                     or all(actual[service]["id"] != self.inventory["shared_live"][service]["id"]
                            for service in SERVICES)))

    def _http(self, host, path, *, status, check_body=None):
        name = "axxium.promethean.rest"
        raw = host.command(["curl", "--silent", "--show-error", "--noproxy", "*", "--proto", "=https",
                            "--resolve", f"{name}:443:127.0.0.1", "--max-time", "8", "--max-filesize", "1048576",
                            "--write-out", "\n%{http_code}", f"https://{name}{path}"], timeout=10)
        body, _, code = raw.rpartition(b"\n")
        if code != str(status).encode():
            raise journal.Held("Origin TLS probe differs from the admitted exposure")
        if check_body is not None:
            check_body(body)
        return status

    def _verify_origin(self, host, state, runtime):
        fenced = state.endswith("_fenced")
        expected = 503 if fenced else 200
        upstream = runtime["network"]["upstream"]
        address = runtime["network"]["app_address"]
        proxy = runtime["containers"]["proxy"]["id"]
        private = host.command(["docker", "exec", proxy, "wget", "-qO", "-", "--header",
                                "Host: axxium.promethean.rest", "--header", "X-Forwarded-Proto: https",
                                f"http://{address}:8787/oidc/.well-known/openid-configuration"], timeout=10)
        production.NativeHost.discovery(private)
        dns = host.command(["docker", "exec", proxy, "getent", "hosts", upstream.split(":")[0]], timeout=10)
        if {record.split()[0] for record in dns.splitlines() if record.split()} != {address.encode()}:
            raise journal.Held("Caddy does not resolve one exact admitted Axxium upstream")

        def did(body):
            if production.digest(body) != self.did_sha256:
                raise journal.Held("Origin DID bytes differ")

        return {
            "health": self._http(host, "/health", status=expected),
            "issuer": self._http(host, "/oidc/.well-known/openid-configuration", status=expected,
                                 check_body=None if fenced else production.NativeHost.discovery),
            "did": self._http(host, "/.well-known/did.json", status=200, check_body=did),
            "pds": self._http(host, "/xrpc/_health", status=200),
        }

    def observe(self, host, states):
        proxy, observed = self._caddy(host)
        bridge = self._existing_bridge(host)
        for state in sorted(states):
            if not self._route_matches(state, observed) or bool(bridge) != (self.mode(state) == "dedicated"):
                continue
            runtime = self._runtime(host, self.mode(state))
            if runtime["containers"]["proxy"]["id"] != proxy or not self._matches_inventory(state, runtime):
                continue
            probes = self._verify_origin(host, state, runtime)
            if (self._caddy(host) != (proxy, observed) or self._runtime(host, self.mode(state)) != runtime
                    or self._existing_bridge(host) != bridge):
                raise journal.Held("Ingress inventory changed during observation")
            stable = journal.json_bytes({"state": state, "caddy": observed, "runtime": runtime})
            evidence = journal.json_bytes({"probes": probes, "state_sha256": production.digest(stable),
                                           "observed_at_ns": time.time_ns()})
            return journal.Observation(state, production.digest(stable), production.digest(evidence),
                                       production.digest(journal.json_bytes(runtime)))
        raise journal.Held("No admitted combination of Caddy exposure and Docker topology was observed")

    def _existing_bridge(self, host):
        name = self.network["name"]
        listing = host.command(["docker", "network", "ls", "--format", "{{.Name}}"]).decode().splitlines()
        if name not in listing:
            return None
        bridge, = json.loads(host.command(["docker", "network", "inspect", name]))
        if (bridge["Labels"].get(BRIDGE_OWNER) != self.plan_sha256() or bridge["Name"] != name
                or bridge["Driver"] != "bridge" or bridge["Internal"] is not True
                or bridge["IPAM"]["Config"] != [{"Subnet": self.network["subnet"],
                                                "Gateway": self.network["gateway"],
                                                "IPRange": self.network["dynamicPool"]}]):
            raise journal.Held("Existing ingress bridge differs from the admitted operation")
        return bridge

    def _bridge(self, host):
        if self._existing_bridge(host) is not None:
            raise journal.Held("Dedicated ingress bridge already exists")
        subnet = ipaddress.IPv4Network(self.network["subnet"])
        routes = json.loads(host.command(["ip", "-j", "-4", "route", "show", "table", "all"]))
        for row in routes:
            destination = row.get("dst", "default")
            if destination != "default" and subnet.overlaps(ipaddress.IPv4Network(destination, strict=False)):
                raise journal.Held("Dedicated ingress subnet intersects an existing host route")
        host.command(["docker", "network", "create", "--driver", "bridge", "--internal",
                      "--label", f"{BRIDGE_OWNER}={self.plan_sha256()}",
                      "--subnet", self.network["subnet"], "--gateway", self.network["gateway"],
                      "--ip-range", self.network["dynamicPool"], self.network["name"]], effect=True)

    def effect(self, host, phase):
        sources, target_state = journal.PHASES[phase]
        target = self.routes[target_state]
        proxy, before = self._caddy(host)
        if not any(self._route_matches(state, before) for state in sources):
            raise journal.Held("Caddy no longer has an admitted phase source")
        if phase == "select_dedicated":
            host.recheck_lock()
            self._bridge(host)
        caddy.publish(host, proxy, production.CADDY, production.MANAGED, root_bytes=target.root,
                      route_name=ROUTE, route_bytes=target.snippet, expected_before=before,
                      expected_adapted_sha256=target.adapted_sha256)
        if phase in SWITCHES:
            self._http(host, "/health", status=503)
            mode = SWITCHES[phase]
            proxy_select, app_select = self.compositions[mode]
            incumbent = host.inspect(production.APP)["Id"]
            host.command(["docker", "stop", "--time", "610", incumbent], timeout=630, effect=True)
            if host.inspect(incumbent)["State"]["Running"]:
                raise journal.Held("Incumbent Axxium did not stop behind the fence")
            proxy_select.recreate(host)
            if not self._route_matches(target_state, self._caddy(host)[1]):
                raise journal.Held("Recreated Caddy did not load the admitted fence")
            self._http(host, "/health", status=503)
            name = self.network["name"] if mode == "dedicated" else SHARED
            peer = host.inspect(production.PROXY)["NetworkSettings"]["Networks"][name]["IPAddress"]
            if mode == "dedicated" and peer != self.network["proxyAddress"]:
                raise journal.Held("Selected Caddy does not have the admitted proxy address")
            app_select.recreate(host, trusted_peer=peer)
            if mode == "shared":
                bridge = self._existing_bridge(host)
                if bridge is not None:
                    if bridge["Containers"]:
                        raise journal.Held("Dedicated ingress bridge still has members")
                    host.command(["docker", "network", "rm", bridge["Id"]], effect=True)


def execute(adapter, operation_id, admitted_plan_sha256, action, phase=None):
    """Use canonical production lock and hold paths for an admitted adapter."""
    if os.getuid() != 1000:
        raise journal.Held("The canonical deploy UID1000 is required")
    if adapter.operation_id != operation_id or adapter.plan_sha256() != admitted_plan_sha256:
        raise journal.Held("Ingress adapter inputs differ from the admitted plan")
    return controller.execute(adapter, operation_id, admitted_plan_sha256, action, phase)
