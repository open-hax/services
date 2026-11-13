#!/usr/bin/env python3
"""Verify an observed Caddy peer from narrowly scoped Docker inventory."""

import argparse
import ipaddress
import json
from pathlib import Path


CONTRACT = Path(__file__).with_name("network.json")


def verify_peer(mode, contract, network, caddy, axxium, app_trust, aliases):
    """Return the observed peer, or refuse an incomplete or inconsistent topology."""
    network_name = contract["name"] if mode == "dedicated" else "open-hax"
    app_alias = contract["appAlias"] if mode == "dedicated" else "axxium"
    if (network["Name"] != network_name or network["Driver"] != "bridge"
            or network["Scope"] != "local" or not isinstance(network["Id"], str) or not network["Id"]):
        raise ValueError("Proxy network differs")
    members = network["Containers"]
    if not isinstance(members, dict) or caddy == axxium or caddy not in members or axxium not in members:
        raise ValueError("Both distinct runtime identities must be on the proxy network")
    if (not isinstance(aliases, dict) or set(aliases) != set(members)
            or any(not isinstance(names, list) or not all(isinstance(name, str) for name in names)
                   for names in aliases.values())):
        raise ValueError("Alias inventory does not cover the current network members")
    if {member for member, names in aliases.items() if app_alias in names} != {axxium}:
        raise ValueError("Axxium upstream alias does not uniquely select the admitted app")

    proxy_interface = ipaddress.IPv4Interface(members[caddy]["IPv4Address"])
    app_interface = ipaddress.IPv4Interface(members[axxium]["IPv4Address"])
    if proxy_interface.network != app_interface.network or proxy_interface.ip == app_interface.ip:
        raise ValueError("Caddy and Axxium must have distinct addresses on the same IPv4 subnet")
    configs = network["IPAM"]["Config"]
    if not any(config.get("Subnet") == str(proxy_interface.network) for config in configs):
        raise ValueError("Observed IPv4 subnet does not match network IPAM")
    if app_trust != str(proxy_interface.ip):
        raise ValueError("Axxium does not trust exactly the observed Caddy address")

    if mode == "dedicated":
        expected_ipam = {
            "Subnet": contract["subnet"], "Gateway": contract["gateway"], "IPRange": contract["dynamicPool"]
        }
        if (network["Internal"] is not True
                or set(members) != {caddy, axxium}
                or len(configs) != 1
                or any(configs[0].get(key) != value for key, value in expected_ipam.items())
                or proxy_interface.network != ipaddress.IPv4Network(contract["subnet"])
                or str(proxy_interface.ip) != contract["proxyAddress"]
                or app_interface.ip not in ipaddress.IPv4Network(contract["dynamicPool"])):
            raise ValueError("Dedicated bridge layout differs from the tracked contract")
    return {
        "network": network_name, "network_id": network["Id"], "caddy_id": caddy,
        "axxium_id": axxium, "upstream": f"{app_alias}:8787", "proxy_address": str(proxy_interface.ip),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["dedicated", "shared"])
    parser.add_argument("inventory", type=Path)
    args = parser.parse_args()
    try:
        contract = json.loads(CONTRACT.read_bytes())
        observed = json.loads(args.inventory.read_bytes())
        result = verify_peer(args.mode, contract, observed["network"], observed["caddy"],
                             observed["axxium"], observed["appTrust"], observed["aliases"])
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        parser.exit(2, "Axxium proxy peer verification refused the supplied inventory\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
