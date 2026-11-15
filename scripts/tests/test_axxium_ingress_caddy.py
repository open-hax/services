import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_ingress_caddy as caddy


class ManagedCaddyReadbackTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        self.root = self.directory / "Caddyfile"
        self.root.write_bytes(b"production root")
        self.managed = self.directory / "managed"
        self.managed.mkdir()

    def test_observed_staging_sibling_may_share_a_protected_root_inode(self):
        source = self.directory / "staging-ingress.caddy"
        source.write_bytes(b"independently published staging route")
        source.chmod(0o644)
        sibling = self.managed / "axxium-pds-staging.caddy"
        os.link(source, sibling)
        inode = source.stat().st_ino
        fstat, lstat = os.fstat, Path.lstat
        owner = 0

        def published_info(info):
            if info.st_ino != inode:
                return info
            values = list(info)
            values[4] = owner
            return os.stat_result(values)

        with (patch.object(os, "fstat", lambda fd: published_info(fstat(fd))),
              patch.object(Path, "lstat", lambda path: published_info(lstat(path)))):
            self.assertEqual({sibling.name: source.read_bytes()}, caddy._files(self.root, self.managed).routes)
            owned = self.managed / caddy.ROUTE_NAME
            os.link(source, owned)
            with self.assertRaisesRegex(caddy.production.Refused, "file protection"):
                caddy._files(self.root, self.managed)
            owned.unlink()
            owner = 1000
            with self.assertRaisesRegex(caddy.production.Refused, "file protection"):
                caddy._files(self.root, self.managed)
            owner = 0
            source.chmod(0o664)
            with self.assertRaisesRegex(caddy.production.Refused, "file protection"):
                caddy._files(self.root, self.managed)

    def test_root_writer_refuses_a_link_created_after_readback(self):
        before = caddy._files(self.root, self.managed)
        os.link(self.root, self.directory / "unexpected-link")
        with self.assertRaisesRegex(caddy.production.Uncertain, "changed before"):
            caddy._write_root(self.root, before, b"candidate", Mock())
        self.assertEqual(before.root, self.root.read_bytes())
