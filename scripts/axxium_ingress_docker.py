"""Read back the Docker side of an admitted Axxium ingress state."""

import importlib.util
import ipaddress
import json
from pathlib import Path
import re

import axxium_minimal_release as production


PEER_PATH = (Path(__file__).resolve().parents[1] / "digitalocean" / "ingress"
             / "axxium-production" / "verify-proxy-peer.py")
PEER_SPEC = importlib.util.spec_from_file_location("axxium_ingress_peer", PEER_PATH)
PEER = importlib.util.module_from_spec(PEER_SPEC)
PEER_SPEC.loader.exec_module(PEER)
DOCKER_ID = re.compile(r"[0-9a-f]{64}")
DOCKER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _require(condition, reason):
    if not condition:
        raise production.Refused(reason)


def _inspect(host, kind, selectors):
    command = (["docker", "inspect", "--type", "container"] if kind == "container"
               else ["docker", "network", "inspect"])
    _require(all(isinstance(selector, str) and DOCKER_NAME.fullmatch(selector)
                 for selector in selectors), "Docker selector is invalid")
    objects = json.loads(host.command([*command, *selectors]))
    _require(isinstance(objects, list) and len(objects) == len(selectors),
             "Docker inventory does not cover the requested objects")
    for selector, observed in zip(selectors, objects, strict=True):
        _require(DOCKER_ID.fullmatch(observed["Id"]) and
                 (selector == observed["Id"] or selector == observed["Name"].removeprefix("/")),
                 "Docker object identity differs")
    _require(len({observed["Id"] for observed in objects}) == len(objects),
             "Docker selectors identify the same object")
    return objects


def _environment(container):
    entries = container["Config"].get("Env") or []
    result = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        _require(name and separator and name not in result, "Docker environment is ambiguous")
        result[name] = value
    return result


def _digest(value):
    return production.digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _container_receipt(container, environment):
    health = container["State"].get("Health")
    _require(container["State"]["Running"] is True and
             (health is None or health["Status"] == "healthy"),
             "Docker service is stopped or unhealthy")
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", container["Image"]),
             "Docker service image identity is unavailable")
    config = dict(container["Config"])
    config.pop("Env", None)
    if config.get("Hostname") == container["Id"][:12]:
        config.pop("Hostname")
    labels = dict(config.get("Labels") or {})
    labels.pop("com.docker.compose.config-hash", None)
    labels.pop("com.docker.compose.project.config_files", None)
    config["Labels"] = labels
    mounts = sorted(container["Mounts"], key=lambda mount: mount["Destination"])
    _require(len({mount["Destination"] for mount in mounts}) == len(mounts),
             "Docker service mounts are ambiguous")
    return {
        "id": container["Id"],
        "image": container["Image"],
        "config_sha256": _digest({"config": config, "host_config": container["HostConfig"]}),
        "environment_sha256": _digest(environment),
        "mounts_sha256": _digest(mounts),
    }


def _verify_endpoint(network, member, container):
    endpoint = container["NetworkSettings"]["Networks"][network["Name"]]
    _require(member["Name"] == container["Name"].removeprefix("/") and
             network["Id"] == endpoint["NetworkID"] and
             DOCKER_ID.fullmatch(member["EndpointID"]) and member["EndpointID"] == endpoint["EndpointID"],
             "Docker network member and endpoint identities differ")
    for member_key, address_key, prefix_key in (
        ("IPv4Address", "IPAddress", "IPPrefixLen"),
        ("IPv6Address", "GlobalIPv6Address", "GlobalIPv6PrefixLen"),
    ):
        address = endpoint.get(address_key) or ""
        observed = f"{address}/{endpoint[prefix_key]}" if address else ""
        listed = member.get(member_key) or ""
        _require(bool(observed) == bool(listed) and
                 (not observed or ipaddress.ip_interface(observed) == ipaddress.ip_interface(listed)),
                 "Docker network member and endpoint addresses differ")
    return endpoint


def _membership(network, inventory):
    aliases = {}
    for identity, member in network["Containers"].items():
        container = inventory[identity]
        endpoint = _verify_endpoint(network, member, container)
        groups = [endpoint.get(key) for key in ("Aliases", "DNSNames")]
        _require(all(group is None or (isinstance(group, list) and
                                      all(isinstance(name, str) for name in group))
                     for group in groups), "Docker endpoint names are invalid")
        aliases[identity] = [member["Name"], *(name for group in groups if group for name in group)]
    # Check service-side listings as well, including an endpoint missing from
    # the network-side inspection.
    for identity, container in inventory.items():
        networks = container["NetworkSettings"]["Networks"]
        _require((network["Name"] in networks) == (identity in aliases),
                 "Docker network inventory disagrees about membership")
    return aliases


def readback(host, *, proxy, app, database, pds, shared, dedicated, contract, mode,
             shared_alias="axxium"):
    """Verify Docker membership on both sides and return redacted facts only."""
    try:
        _require(mode in {"shared", "dedicated"} and dedicated == contract["name"]
                 and shared != dedicated and isinstance(shared_alias, str)
                 and DOCKER_NAME.fullmatch(shared_alias) and shared_alias == shared_alias.lower(),
                 "Docker ingress selection is invalid")
        services = dict(zip(("proxy", "app", "database", "pds"),
                            _inspect(host, "container", (proxy, app, database, pds)), strict=True))
        networks = _inspect(host, "network", (shared, dedicated) if mode == "dedicated" else (shared,))
        by_id = {container["Id"]: container for container in services.values()}
        member_ids = {identity for network in networks for identity in network["Containers"]}
        others = sorted(member_ids - by_id.keys())
        if others:
            by_id.update((container["Id"], container) for container in _inspect(host, "container", others))
        memberships = [_membership(network, by_id) for network in networks]
        proxy_id, app_id, pds_id = (services[role]["Id"] for role in ("proxy", "app", "pds"))
        _require({proxy_id, app_id, pds_id} <= memberships[0].keys(),
                 "Caddy, Axxium and PDS must retain the shared network")
        if mode == "shared":
            _require(all(dedicated not in service["NetworkSettings"]["Networks"]
                         for service in services.values()),
                     "Shared state has an unexpected dedicated ingress attachment")
        app_env = _environment(services["app"])
        trust = app_env.pop("AXXIUM_TRUST_PROXY", None)
        selected = networks[-1]
        receipt = PEER.verify_peer(mode, contract, selected, proxy_id, app_id, trust,
                                   memberships[-1], shared_network=shared, shared_alias=shared_alias)
        members = selected["Containers"]
        receipt["app_address"] = str(ipaddress.IPv4Interface(members[app_id]["IPv4Address"]).ip)
        receipt["shared_network_id"] = networks[0]["Id"]
        return {
            "containers": {role: _container_receipt(container, app_env if role == "app"
                                                     else _environment(container))
                           for role, container in services.items()},
            "network": receipt,
            "membership": {role: members[identity]["EndpointID"]
                           for role, identity in (("proxy", proxy_id), ("app", app_id))},
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        raise production.Refused("Docker ingress readback was incomplete or inconsistent") from None
