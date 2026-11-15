import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_inplace_ingress as subject


SOURCE = ("# preserved managed routes\nimport /etc/caddy/managed/*.caddy\n"
          "axxium.promethean.rest, *.axxium.promethean.rest {\n"
          "\t# preserved DID route and PDS fallback\n" + subject.OLD + "\n\t}\n}\n").encode()


class InplaceIngressTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.config = self.root / "Caddyfile"
        self.config.write_bytes(SOURCE)
        self.inode = self.config.stat().st_ino
        self.journals = self.root / "journals"
        self.journals.mkdir(mode=0o700)
        self.events = []
        self.kwargs = dict(lease_held=lambda: True, inspect_identity=lambda: ("container-id", self.inode),
                           validate=lambda: self.events.append("validate"),
                           reload_config=lambda: self.events.append("reload"),
                           verify=lambda changed: self.events.append(("verify", changed)))

    def apply(self):
        return subject.apply(self.config, self.journals, hashlib.sha256(SOURCE).hexdigest(), **self.kwargs)

    def test_commit_retains_bound_inode_and_all_other_routes(self):
        result = self.apply()
        self.assertEqual(self.config.stat().st_ino, self.inode)
        self.assertEqual(self.config.read_bytes(), SOURCE.replace(subject.OLD.encode(), subject.NEW.encode()))
        self.assertEqual(self.events, ["validate", "reload", ("verify", True)])
        self.assertEqual((Path(result["journal"]) / "before").read_bytes(), SOURCE)
        self.assertEqual((Path(result["journal"]) / "complete").read_bytes(), b"committed\n")
        self.assertEqual((Path(result["journal"]) / "before").stat().st_mode & 0o777, 0o600)

    def test_failed_validation_restores_and_reloads_same_inode(self):
        def validate():
            self.events.append("validate")
            if self.config.read_bytes() != SOURCE:
                raise RuntimeError("invalid candidate")
        self.kwargs["validate"] = validate
        with self.assertRaisesRegex(RuntimeError, "invalid candidate"):
            self.apply()
        self.assertEqual(self.config.read_bytes(), SOURCE)
        self.assertEqual(self.config.stat().st_ino, self.inode)
        self.assertEqual(self.events, ["validate", "validate", "reload", ("verify", False)])
        self.assertEqual(next(self.journals.iterdir()).joinpath("complete").read_bytes(), b"rolled-back\n")

    def test_failed_candidate_health_restores(self):
        def verify(changed):
            if changed:
                raise RuntimeError("public route failed")
        self.kwargs["verify"] = verify
        with self.assertRaisesRegex(RuntimeError, "public route failed"):
            self.apply()
        self.assertEqual(self.config.read_bytes(), SOURCE)

    def test_partial_write_retains_before_image_and_blocks_next_attempt(self):
        def interrupted(fd, data):
            os.write(fd, b"PARTIAL")
            raise OSError("disk full")
        with patch.object(subject, "write_same_inode", interrupted):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.apply()
        txn = next(self.journals.iterdir())
        self.assertEqual((txn / "before").read_bytes(), SOURCE)
        self.assertFalse((txn / "complete").exists())
        with self.assertRaisesRegex(ValueError, "explicit recovery"):
            self.apply()

    def test_other_writer_is_not_overwritten(self):
        def verify(_):
            self.config.write_bytes(b"NEWER WRITER")
        self.kwargs["verify"] = verify
        with self.assertRaisesRegex(ValueError, "changed during verification"):
            self.apply()
        self.assertEqual(self.config.read_bytes(), b"NEWER WRITER")
        self.assertFalse(next(self.journals.iterdir()).joinpath("complete").exists())

    def test_inode_replacement_retains_journal(self):
        def verify(_):
            replacement = self.root / "replacement"
            replacement.write_bytes(b"replacement")
            replacement.replace(self.config)
        self.kwargs["verify"] = verify
        with self.assertRaisesRegex(ValueError, "changed during verification"):
            self.apply()
        self.assertEqual(self.config.read_bytes(), b"replacement")
        self.assertFalse(next(self.journals.iterdir()).joinpath("complete").exists())

    def test_missing_lease_refuses_before_any_effect(self):
        self.kwargs["lease_held"] = lambda: False
        with self.assertRaisesRegex(ValueError, "lease"):
            self.apply()
        self.assertEqual(list(self.journals.iterdir()), [])
        self.assertEqual(self.config.read_bytes(), SOURCE)

    def test_baseline_drift_refuses(self):
        self.config.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "baseline changed"):
            self.apply()
        self.assertEqual(list(self.journals.iterdir()), [])

    def test_symlink_and_hardlink_refuse(self):
        real = self.root / "real"
        self.config.rename(real)
        self.config.symlink_to(real)
        with self.assertRaises(OSError):
            self.apply()
        self.config.unlink()
        os.link(real, self.config)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.apply()

    def test_duplicate_route_refuses(self):
        twice = SOURCE + SOURCE
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            subject.candidate(twice, subject.digest(twice))


if __name__ == "__main__":
    unittest.main()
