"""Nonroot state copy preserves bytes, rejects unsafe and changing sources."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import caddy_state_candidate as candidate


class StateCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        for path in (self.source / "data/caddy/certs", self.source / "config/caddy"):
            path.mkdir(parents=True)
        (self.source / "data/caddy/certs/key.pem").write_bytes(b"fixture-private-key\x00bytes")
        (self.source / "config/caddy/autosave.json").write_bytes(b'{"fixture":true}')
        self.destination = self.root / "candidate"

    def test_exact_bytes_private_ownership_and_no_source_changes(self):
        before = {k: candidate.inventory(self.source / k) for k in ("data", "config")}
        result = candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())
        self.assertTrue(result["copy_verified"] and result["source_unchanged"])
        self.assertEqual(before, {k: candidate.inventory(self.source / k) for k in ("data", "config")})
        self.assertEqual((self.destination / "data/caddy/certs/key.pem").read_bytes(), b"fixture-private-key\x00bytes")
        self.assertEqual((self.destination / "data/caddy/certs/key.pem").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.destination / "VERIFIED").exists())
        with self.assertRaises(FileExistsError):
            candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())

    def test_symlink_or_hardlink_refuses_without_candidate(self):
        (self.source / "data/link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "link"):
            candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())
        self.assertFalse(self.destination.exists())
        (self.source / "data/link").unlink()
        os.link(self.source / "data/caddy/certs/key.pem", self.source / "data/second")
        with self.assertRaisesRegex(ValueError, "link"):
            candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())

    def test_source_change_preserves_pending_candidate(self):
        original = candidate.inventory
        calls = 0
        def changing(root):
            nonlocal calls
            calls += 1
            if calls == 3:
                (self.source / "data/caddy/certs/key.pem").write_bytes(b"changed")
            return original(root)
        with patch.object(candidate, "inventory", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "changed during copy"):
                candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())
        self.assertTrue((self.destination / "PENDING").exists())
        self.assertFalse((self.destination / "VERIFIED").exists())

    def test_missing_or_symlink_root_refuses_before_creating_candidate(self):
        config = self.source / "config"
        config.rename(self.source / "real-config")
        with self.assertRaises(FileNotFoundError):
            candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())
        self.assertFalse(self.destination.exists())
        config.symlink_to(self.source / "real-config", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory"):
            candidate.copy_verified(self.source, self.destination, uid=os.getuid(), gid=os.getgid())
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
