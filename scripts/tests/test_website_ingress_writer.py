"""Website writer fixture; no host controller or Caddy service is changed."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ingress_lock import ingress_lock
from identity_ingress_transaction import IngressTransactionError
from website_ingress_writer import CANDIDATE, CONTAINER, TARGET, run_under_lease, selected_config


class WebsiteWriterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.managed = self.root / "managed"
        self.lockdir = self.root / "lock"
        for directory in (self.runtime, self.managed, self.lockdir):
            directory.mkdir(mode=0o755)
        (self.lockdir / "transaction.lock").write_bytes(b"")
        (self.lockdir / "transaction.lock").chmod(0o644)
        (self.runtime / CANDIDATE).write_bytes(b"new site\n")
        (self.runtime / CANDIDATE).chmod(0o644)
        self.target = self.managed / TARGET
        self.target.write_bytes(b"old site\n")
        self.target.chmod(0o644)
        self.expected = hashlib.sha256(b"new site\n").hexdigest()
        self.calls = []
        self.fail_first_validate = False
        self.inside_mismatch = False
        self.missing_import = False

    def fake_docker(self, args):
        self.calls.append(args)
        if args[:2] == ["inspect", "--format"]:
            if args[2] == "{{json .Mounts}}":
                return json.dumps([{"Source": str(self.managed), "Destination": "/etc/caddy/managed", "RW": False}]).encode()
            if args[2] == "{{json .Config.Cmd}}":
                return json.dumps(["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]).encode()
        if args[:4] == ["exec", "--user", "1000:1000", CONTAINER]:
            if args[4] == "cat":
                if args[5] in ("/etc/caddy/Caddyfile", "/etc/caddy/identity-ingress/Caddyfile"):
                    return b"# missing import\n" if self.missing_import else b"import /etc/caddy/managed/*.caddy\n"
                if self.inside_mismatch:
                    return b"wrong mounted inode"
                if not self.target.exists():
                    raise subprocess.CalledProcessError(1, args)
                return self.target.read_bytes()
            if args[4:6] == ["caddy", "validate"] and self.fail_first_validate:
                self.fail_first_validate = False
                raise subprocess.CalledProcessError(1, args)
            if args[4:6] in (["caddy", "validate"], ["caddy", "reload"]):
                return b"ok"
        raise AssertionError("Unexpected Docker command " + repr(args))

    def run_writer(self, expected=None):
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            return run_under_lease(lease, expected or self.expected, runtime=self.runtime, managed=self.managed,
                                   owner=os.getuid(), docker_run=self.fake_docker)

    def test_validates_reloads_and_preserves_nonroot_exec(self):
        self.assertEqual(self.run_writer(), self.expected)
        self.assertEqual(self.target.read_bytes(), b"new site\n")
        commands = [args for args in self.calls if args[:4] == ["exec", "--user", "1000:1000", CONTAINER]]
        self.assertEqual([args[4] for args in commands], ["cat", "cat", "caddy", "caddy"])
        self.assertEqual([args[5] for args in commands[2:]], ["validate", "reload"])

    def test_failed_validation_restores_original_and_reloads_it(self):
        self.fail_first_validate = True
        with self.assertRaisesRegex(IngressTransactionError, "restored and reloaded"):
            self.run_writer()
        self.assertEqual(self.target.read_bytes(), b"old site\n")
        commands = [args[5] for args in self.calls if args[:5] == ["exec", "--user", "1000:1000", CONTAINER, "caddy"]]
        self.assertEqual(commands, ["validate", "validate", "reload"])

    def test_wrong_hash_or_mounted_content_refuses_before_write(self):
        with self.assertRaisesRegex(IngressTransactionError, "candidate hash"):
            self.run_writer("0" * 64)
        self.inside_mismatch = True
        with self.assertRaisesRegex(IngressTransactionError, "same incumbent"):
            self.run_writer()
        self.assertEqual(self.target.read_bytes(), b"old site\n")

    def test_missing_import_refuses_before_write(self):
        self.missing_import = True
        with self.assertRaisesRegex(IngressTransactionError, "does not import"):
            self.run_writer()
        self.assertEqual(self.target.read_bytes(), b"old site\n")

    def test_first_install_failure_restores_absence(self):
        self.target.unlink()
        self.fail_first_validate = True
        with self.assertRaisesRegex(IngressTransactionError, "restored and reloaded"):
            self.run_writer()
        self.assertFalse(self.target.exists())

    def test_unexpected_caddy_command_or_writable_mount_is_rejected(self):
        mount = {"Source": str(self.managed), "Destination": "/etc/caddy/managed", "RW": False}
        cmd = ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
        self.assertEqual(selected_config([mount], cmd, self.managed), "/etc/caddy/Caddyfile")
        future = ["caddy", "run", "--config", "/etc/caddy/identity-ingress/Caddyfile", "--adapter", "caddyfile"]
        self.assertEqual(selected_config([mount], future, self.managed), future[3])
        for bad_mounts, bad_command in [([{**mount, "RW": True}], cmd), ([mount, mount], cmd),
                                        ([mount], [*cmd[:3], "/elsewhere", *cmd[4:]]), ([mount], ["caddy"])]:
            with self.assertRaises(IngressTransactionError):
                selected_config(bad_mounts, bad_command, self.managed)


if __name__ == "__main__":
    unittest.main()
