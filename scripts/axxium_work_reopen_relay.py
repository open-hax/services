#!/usr/bin/env python3
"""Arm or close the existing Caddy-only Axxium Work relay as an audited operator."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import sys

from axxium_work_dark_host import ROOT, SLOT, Refused, command, docker, require


BASE = "promethean-axxium-work-pilot"
SOCKET = BASE + ".socket"
RELAY = BASE + ".service"
LEGACY = (BASE + "-stop.timer", BASE + "-stop.service")
UNITS = Path("/etc/systemd/system")
BOOT_LINK = UNITS / "multi-user.target.wants" / SOCKET
TEMPLATES = ROOT / "digitalocean/services/axxium-work-pilot/ingress/units"
ROUTE = Path("/srv/open-hax/state/caddy/managed/axxium-work-pilot.caddy")
NETWORK = "caddy-dev-ingress"
GATEWAY = "172.31.255.1"
CADDY = "172.31.255.2"
PORT = 19073
COMMENT = "Axxium Work persistent relay"
BRIDGE = re.compile(r"br-[a-f0-9]{12}")


def systemd_properties(unit):
    result = command(
        ["systemctl", "show", unit,
         "--property=LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths,NeedDaemonReload,Listen"],
        "unable to inspect the exact relay unit",
    )
    return dict(line.split("=", 1) for line in result.decode().splitlines() if "=" in line)


def installed_units():
    for name in (SOCKET, RELAY):
        path = UNITS / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and path.read_bytes() == (TEMPLATES / name).read_bytes(),
                "installed relay unit differs from the reviewed file")
    units = {name: systemd_properties(name) for name in (SOCKET, RELAY, *LEGACY)}
    for name in (SOCKET, RELAY):
        props = units[name]
        require(props["LoadState"] == "loaded" and props["FragmentPath"] == str(UNITS / name)
                and not props["DropInPaths"], "loaded relay unit differs from the reviewed file")
    for name in LEGACY:
        props = units[name]
        require(props["ActiveState"] == "inactive" and props["UnitFileState"] in {"static", "disabled", ""},
                "legacy pilot deadline is active or enabled")
    return units


def boot_link():
    parent = BOOT_LINK.parent
    info = parent.lstat()
    require(parent.resolve(strict=True) == parent and stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0 and not info.st_mode & 0o022,
            "systemd target directory is not root-owned")
    if not os.path.lexists(BOOT_LINK):
        return False
    require(BOOT_LINK.is_symlink() and BOOT_LINK.lstat().st_uid == 0
            and os.readlink(BOOT_LINK) == str(UNITS / SOCKET),
            "an unrelated boot dependency occupies the pilot socket link")
    return True


def change_boot_link(enabled):
    if enabled:
        BOOT_LINK.symlink_to(UNITS / SOCKET)
    else:
        BOOT_LINK.unlink()
    fd = os.open(BOOT_LINK.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def caddy_bridge():
    network, = json.loads(docker("network", "inspect", NETWORK, reason="Caddy bridge is unavailable"))
    identity = network["Id"]
    require(re.fullmatch(r"[a-f0-9]{64}", identity) and network["Name"] == NETWORK
            and network["Driver"] == "bridge" and network["Scope"] == "local",
            "Caddy bridge identity differs")
    bridge = "br-" + identity[:12]
    require(network["Options"].get("com.docker.network.bridge.name", bridge) == bridge,
            "Caddy bridge interface differs")
    subnet, = network["IPAM"]["Config"]
    require(subnet["Subnet"] == "172.31.255.0/28" and subnet["Gateway"] == GATEWAY,
            "Caddy bridge subnet differs")
    member, = network["Containers"].values()
    require(member["Name"] == "caddy-caddy-1" and member["IPv4Address"] == CADDY + "/28",
            "Caddy is not the sole reviewed member of the relay bridge")
    addresses, = json.loads(command(["ip", "-json", "-4", "address", "show", "dev", bridge],
                                     "unable to read the Caddy interface"))
    require(addresses["ifname"] == bridge
            and [(a["local"], a["prefixlen"]) for a in addresses["addr_info"]] == [(GATEWAY, 28)],
            "Caddy bridge gateway differs")
    return bridge


def firewall_rules():
    persisted = command(["ufw", "show", "added"], "unable to read persisted UFW rules").decode()
    kernel = command(["iptables", "-w", "4", "-S", "ufw-user-input"],
                     "unable to read active IPv4 UFW input rules").decode()
    return ([tuple(shlex.split(line)) for line in persisted.splitlines() if line.startswith("ufw ")],
            [tuple(shlex.split(line)) for line in kernel.splitlines() if line.startswith("-A ")])


def firewall_rule(bridge):
    return ("ufw", "allow", "in", "on", bridge, "from", CADDY, "to", GATEWAY,
            "port", str(PORT), "proto", "tcp", "comment", COMMENT)


def kernel_rule_matches(rule, bridge):
    return (rule[:2] == ("-A", "ufw-user-input") and len(rule) == 16
            and dict(zip(rule[2::2], rule[3::2])) ==
            {"-s": CADDY + "/32", "-d": GATEWAY + "/32", "-i": bridge, "-p": "tcp",
             "-m": "tcp", "--dport": str(PORT), "-j": "ACCEPT"})


def rule_mentions_port(rule):
    for token in rule:
        for part in token.split("/")[0].split(","):
            match = re.fullmatch(r"(\d+)(?::(\d+))?", part)
            if match and int(match[1]) <= PORT <= int(match[2] or match[1]):
                return True
    return False


def owned_rules(rules):
    persisted, kernel = rules
    recorded = [rule for rule in persisted if COMMENT in rule]
    require(len(recorded) <= 1, "multiple persisted rules claim the pilot relay")
    if not recorded:
        return None, []
    rule, = recorded
    require(len(rule) > 4 and BRIDGE.fullmatch(rule[4]) and rule == firewall_rule(rule[4]),
            "a persisted rule with the pilot comment has unexpected scope")
    matches = [entry for entry in kernel if kernel_rule_matches(entry, rule[4])]
    require(len(matches) <= 1, "multiple kernel rules match the pilot relay")
    return rule, matches


def without(rules, persisted, kernel):
    return ([entry for entry in rules[0] if entry != persisted],
            [entry for entry in rules[1] if entry not in kernel])


def relay_state():
    units = installed_units()
    linked = boot_link()
    rules = firewall_rules()
    recorded, kernel = owned_rules(rules)
    sock = units[SOCKET]
    remaining_persisted, remaining_kernel = without(rules, recorded, kernel)
    competing_rule = any(rule_mentions_port(rule) for rule in remaining_persisted + remaining_kernel)
    stopped = sock["ActiveState"] == units[RELAY]["ActiveState"] == "inactive"
    if stopped and not linked and not recorded and not competing_rule:
        return "closed"
    if (linked and recorded and kernel and sock["ActiveState"] == "active"
            and sock["Listen"] == f"{GATEWAY}:{PORT} (Stream)"
            and all(units[name]["NeedDaemonReload"] == "no" for name in (SOCKET, RELAY))
            and not competing_rule):
        return "armed"
    return "partial"


def disarm():
    installed_units()
    linked = boot_link()
    command(["systemctl", "stop", SOCKET, RELAY], "unable to stop the relay socket and service")
    if linked:
        change_boot_link(False)
    command(["systemctl", "daemon-reload"], "unable to reload the closed socket dependencies")
    before = firewall_rules()
    rule, kernel = owned_rules(before)
    if rule:
        command(["ufw", "--force", "delete", *rule[1:]], "unable to remove the exact pilot UFW rule")
        require(firewall_rules() == without(before, rule, kernel),
                "the exact pilot rule removal or unrelated rule preservation did not verify")
    require(relay_state() == "closed", "relay stopped but its closed boundary did not verify")


def arm():
    bridge = caddy_bridge()
    status = command(["ufw", "status", "verbose"], "unable to verify the UFW default policy").decode()
    require("Status: active" in status.splitlines()
            and any(line.startswith("Default: deny (incoming),") for line in status.splitlines()),
            "UFW is not active with the reviewed default-deny inbound policy")
    rule = firewall_rule(bridge)
    state = relay_state()
    before = firewall_rules()
    if state == "armed":
        require(owned_rules(before)[0] == rule, "persistent relay uses a different Caddy bridge")
        return
    require(state == "closed" and not os.path.lexists(ROUTE)
            and not os.path.lexists(SLOT / ".axxium-work-production-pending")
            and not os.path.lexists(SLOT / "pending.json"),
            "relay arm requires a closed, unheld slot with no pilot route")
    try:
        command(list(rule), "unable to install the exact Caddy UFW rule")
        after = firewall_rules()
        added, kernel = owned_rules(after)
        require(added == rule and kernel and without(after, added, kernel) == before,
                "the exact Caddy rule or unrelated rule preservation did not verify")
        change_boot_link(True)
        command(["systemctl", "daemon-reload"], "unable to load the persistent socket dependency")
        command(["systemctl", "start", SOCKET], "unable to start the exact Caddy relay socket")
        require(relay_state() == "armed", "persistent relay did not verify after start")
    except (Refused, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        try:
            disarm()
        except (Refused, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt):
            raise Refused("relay arm failed and automatic disarm could not verify") from None
        raise Refused("relay arm failed and the relay was closed") from error


def operator_interrupted(_signal, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("arm", "disarm", "status"))
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0 and Path("/proc/self/loginuid").read_text().strip() == "1002",
                "run through the audited Codex SSH identity with sudo")
        signal.signal(signal.SIGTERM, operator_interrupted)
        lock_path = SLOT / ".lock"
        require(lock_path.resolve(strict=True) == lock_path, "pilot lock is not canonical")
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 1000 and stat.S_IMODE(info.st_mode) == 0o600,
                    "pilot lock differs from the reviewed slot")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "arm":
                arm()
            elif args.action == "disarm":
                disarm()
            state = relay_state()
        print(json.dumps({"relay": state, "persistent": state == "armed"}))
        return int(state == "partial")
    except (Refused, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        print(json.dumps({"relay": "unverified",
                          "reason": str(error) if isinstance(error, Refused) else "relay operator action refused"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
