"""Exercise persistent relay transitions and restoration without root or a live firewall."""

from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_reopen_relay as relay


BRIDGE = "br-c324d11621f2"
OTHER_UFW = ("ufw", "allow", "22/tcp")
OTHER_KERNEL = ("-A", "ufw-user-input", "-p", "tcp", "-m", "tcp", "--dport", "22", "-j", "ACCEPT")
PILOT_KERNEL = ("-A", "ufw-user-input", "-s", "172.31.255.2/32", "-d", "172.31.255.1/32",
                "-i", BRIDGE, "-p", "tcp", "-m", "tcp", "--dport", "19073", "-j", "ACCEPT")


class RelayOperatorTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(TemporaryDirectory()))
        units = root / "systemd"
        wants = units / "multi-user.target.wants"
        wants.mkdir(parents=True)
        self.boot = wants / relay.SOCKET
        self.route = root / "pilot.caddy"
        self.rule = relay.firewall_rule(BRIDGE)
        self.persisted = [OTHER_UFW]
        self.kernel = [OTHER_KERNEL]
        self.active = False
        self.start_failure = False
        self.calls = []
        for name, value in {
            "UNITS": units,
            "BOOT_LINK": self.boot,
            "ROUTE": self.route,
            "SLOT": root,
            "installed_units": self.installed_units,
            "boot_link": self.boot.is_symlink,
            "caddy_bridge": lambda: BRIDGE,
            "firewall_rules": lambda: (self.persisted.copy(), self.kernel.copy()),
            "command": self.command,
        }.items():
            stack.enter_context(patch.object(relay, name, value))

    def installed_units(self):
        return {
            relay.SOCKET: {"ActiveState": "active" if self.active else "inactive",
                           "Listen": "172.31.255.1:19073 (Stream)", "NeedDaemonReload": "no"},
            relay.RELAY: {"ActiveState": "inactive", "NeedDaemonReload": "no"},
        }

    def command(self, args, _reason):
        args = tuple(args)
        self.calls.append(args)
        if args == ("ufw", "status", "verbose"):
            return b"Status: active\nDefault: deny (incoming), allow (outgoing), deny (routed)\n"
        if args == self.rule:
            self.persisted.append(self.rule)
            self.kernel.append(PILOT_KERNEL)
        elif args == ("ufw", "--force", "delete", *self.rule[1:]):
            self.persisted.remove(self.rule)
            self.kernel.remove(PILOT_KERNEL)
        elif args == ("systemctl", "start", relay.SOCKET):
            self.active = True
            if self.start_failure:
                raise relay.Refused("simulated failure after start")
        elif args == ("systemctl", "stop", relay.SOCKET, relay.RELAY):
            self.active = False
        elif args != ("systemctl", "daemon-reload"):
            self.fail("unexpected command " + repr(args))
        return b""

    def test_arm_and_disarm_preserve_unrelated_rules_and_live_route(self):
        self.assertEqual(relay.relay_state(), "closed")
        relay.arm()
        self.assertEqual(relay.relay_state(), "armed")
        self.assertEqual(self.boot.readlink(), relay.UNITS / relay.SOCKET)
        self.route.write_text("reviewed Caddy state")
        relay.arm()
        self.assertEqual(self.calls.count(self.rule), 1)

        with patch.object(relay, "caddy_bridge", side_effect=relay.Refused("bridge vanished")):
            relay.disarm()
            relay.disarm()
        self.assertEqual(relay.relay_state(), "closed")
        self.assertEqual(self.route.read_text(), "reviewed Caddy state")
        self.assertEqual(self.persisted, [OTHER_UFW])
        self.assertEqual(self.kernel, [OTHER_KERNEL])
        self.assertFalse(self.boot.is_symlink())

    def test_failed_start_after_socket_open_rolls_back_exact_rule_and_link(self):
        self.start_failure = True
        with self.assertRaisesRegex(relay.Refused, "relay was closed"):
            relay.arm()
        self.assertEqual(relay.relay_state(), "closed")
        self.assertEqual(self.persisted, [OTHER_UFW])
        self.assertEqual(self.kernel, [OTHER_KERNEL])
        self.assertFalse(self.boot.is_symlink())

    def test_competing_port_range_prevents_arm_and_is_never_removed(self):
        unrelated = ("ufw", "allow", "19000:19100/tcp")
        self.persisted.append(unrelated)
        with self.assertRaisesRegex(relay.Refused, "closed, unheld slot"):
            relay.arm()
        with self.assertRaisesRegex(relay.Refused, "closed boundary did not verify"):
            relay.disarm()
        self.assertEqual(self.persisted, [OTHER_UFW, unrelated])
        self.assertFalse(any(cmd[:3] == ("ufw", "--force", "delete") for cmd in self.calls))

    def test_foreign_rule_with_pilot_comment_is_refused_without_deleting_it(self):
        unexpected = ("ufw", "allow", "19073/tcp", "comment", relay.COMMENT)
        self.persisted.append(unexpected)
        with self.assertRaisesRegex(relay.Refused, "unexpected scope"):
            relay.disarm()
        self.assertEqual(self.persisted, [OTHER_UFW, unexpected])
        self.assertFalse(any(cmd[:3] == ("ufw", "--force", "delete") for cmd in self.calls))


if __name__ == "__main__":
    unittest.main()
