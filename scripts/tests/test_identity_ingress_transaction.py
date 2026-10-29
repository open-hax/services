"""Concurrency and rollback boundaries for the unactivated host transaction."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ingress_lock import ingress_lock
from identity_ingress_transaction import IngressTransactionError, replace_under_lease


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lockdir = self.root / "lease"
        self.lockdir.mkdir(mode=0o755)
        (self.lockdir / "transaction.lock").write_bytes(b"")
        (self.lockdir / "transaction.lock").chmod(0o644)
        self.directory = self.root / "routes"
        self.directory.mkdir(mode=0o755)
        self.target = self.directory / "Caddyfile"
        self.target.write_bytes(b"old configuration\n")
        self.target.chmod(0o644)
        self.baseline = hashlib.sha256(self.target.read_bytes()).hexdigest()
        self.journal_counter = 0

    def replace(self, *args, **kwargs):
        self.journal_counter += 1
        journal = self.root / f"journal-{self.journal_counter}"
        journal.mkdir(mode=0o700)
        return replace_under_lease(*args, journal=journal, **kwargs)

    def test_success_and_stale_baseline_refusal(self):
        seen = []
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            result = self.replace(lease, self.target, b"new\n", self.baseline,
                                         lambda: seen.append(self.target.read_bytes()))
            self.assertEqual(result, hashlib.sha256(b"new\n").hexdigest())
            with self.assertRaisesRegex(IngressTransactionError, "baseline"):
                self.replace(lease, self.target, b"stale\n", self.baseline, lambda: None)
        self.assertEqual(seen, [b"new\n"])
        self.assertEqual(self.target.read_bytes(), b"new\n")

    def test_validation_failure_restores_and_reloads_exact_old_bytes(self):
        seen = []
        def reload():
            seen.append(self.target.read_bytes())
            if len(seen) == 1:
                raise RuntimeError("fixture validation failed")
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with self.assertRaisesRegex(IngressTransactionError, "restored and reloaded"):
                self.replace(lease, self.target, b"bad\n", self.baseline, reload)
        self.assertEqual(seen, [b"bad\n", b"old configuration\n"])
        self.assertEqual(self.target.read_bytes(), b"old configuration\n")

    def test_real_post_rename_fsync_failure_restores_and_reloads_original(self):
        original_replace, original_fsync = os.replace, os.fsync
        directory = self.directory.stat()
        state = {'renamed': False, 'failed': False}
        def replace(src, dst, **kwargs):
            result = original_replace(src, dst, **kwargs)
            if dst == self.target.name:
                state['renamed'] = True
            return result
        def fsync(fd):
            info = os.fstat(fd)
            if state['renamed'] and not state['failed'] and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                state['failed'] = True
                raise OSError('injected post-rename fsync failure')
            return original_fsync(fd)
        observed = []
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with patch.object(os, 'replace', side_effect=replace), patch.object(os, 'fsync', side_effect=fsync):
                with self.assertRaisesRegex(IngressTransactionError, 'restored and reloaded'):
                    self.replace(lease, self.target, b'candidate\n', self.baseline,
                                 lambda: observed.append(self.target.read_bytes()))
        self.assertEqual(self.target.read_bytes(), b'old configuration\n')
        self.assertEqual(observed, [b'old configuration\n'])
        self.assertFalse((self.root / 'journal-1/pending').exists())

    def test_persistent_post_rename_fsync_failure_keeps_durable_guard(self):
        original_replace, original_fsync = os.replace, os.fsync
        directory = self.directory.stat()
        state = {'renamed': False}
        def replace(src, dst, **kwargs):
            result = original_replace(src, dst, **kwargs)
            if dst == self.target.name:
                state['renamed'] = True
            return result
        def fsync(fd):
            info = os.fstat(fd)
            if state['renamed'] and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                raise OSError('persistent target directory fsync failure')
            return original_fsync(fd)
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with patch.object(os, 'replace', side_effect=replace), patch.object(os, 'fsync', side_effect=fsync):
                with self.assertRaisesRegex(IngressTransactionError, 'rollback could not be confirmed'):
                    self.replace(lease, self.target, b'candidate\n', self.baseline, lambda: None)
        self.assertTrue((self.root / 'journal-1/pending').exists())

    def test_post_rename_fsync_failure_restores_original_absence(self):
        self.target.unlink()
        original_replace, original_fsync = os.replace, os.fsync
        directory = self.directory.stat()
        state = {'renamed': False, 'failed': False}
        def replace(src, dst, **kwargs):
            result = original_replace(src, dst, **kwargs)
            if dst == self.target.name:
                state['renamed'] = True
            return result
        def fsync(fd):
            info = os.fstat(fd)
            if state['renamed'] and not state['failed'] and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                state['failed'] = True
                raise OSError('post-rename failure')
            return original_fsync(fd)
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with patch.object(os, 'replace', side_effect=replace), patch.object(os, 'fsync', side_effect=fsync):
                with self.assertRaisesRegex(IngressTransactionError, 'restored and reloaded'):
                    self.replace(lease, self.target, b'new route', None, lambda: None)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / 'journal-1/pending').exists())

    def test_new_file_install_and_failed_install_restores_absence(self):
        self.target.unlink()
        observed = []
        def reload():
            observed.append(self.target.read_bytes() if self.target.exists() else None)
            if len(observed) == 1:
                raise RuntimeError("invalid new route")
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with self.assertRaisesRegex(IngressTransactionError, "restored and reloaded"):
                self.replace(lease, self.target, b"bad\n", None, reload)
            self.assertFalse(self.target.exists())
            self.replace(lease, self.target, b"good\n", None, lambda: None)
            self.assertEqual(self.target.read_bytes(), b"good\n")
            with self.assertRaisesRegex(IngressTransactionError, "baseline"):
                self.replace(lease, self.target, b"other\n", None, lambda: None)
        self.assertEqual(observed, [b"bad\n", None])

    def test_uncooperative_newer_write_is_not_overwritten_during_rollback(self):
        def concurrent_write():
            self.target.write_bytes(b"other writer\n")
            raise RuntimeError("fixture failure after concurrent write")
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with self.assertRaisesRegex(IngressTransactionError, "rollback could not be confirmed"):
                self.replace(lease, self.target, b"ours\n", self.baseline, concurrent_write)
        self.assertEqual(self.target.read_bytes(), b"other writer\n")

    def test_replaced_directory_refuses_rollback_instead_of_writing_old_inode(self):
        parked = self.root / "parked"
        def replace_directory():
            self.directory.rename(parked)
            self.directory.mkdir(mode=0o755)
            self.target.write_bytes(b"new directory owner\n")
            raise RuntimeError("fixture failure")
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with self.assertRaisesRegex(IngressTransactionError, "rollback could not be confirmed"):
                self.replace(lease, self.target, b"ours\n", self.baseline, replace_directory)
        self.assertEqual(self.target.read_bytes(), b"new directory owner\n")
        self.assertEqual((parked / "Caddyfile").read_bytes(), b"ours\n")

    def test_other_process_cannot_write_while_common_lease_held(self):
        code = '''from ingress_lock import ingress_lock,IngressLockError
from identity_ingress_transaction import replace_under_lease
from pathlib import Path
import os,sys
try:
 with ingress_lock(sys.argv[1],owner=os.getuid()) as lease:
  replace_under_lease(lease,Path(sys.argv[2]),b"other",sys.argv[3],lambda:None)
except IngressLockError:sys.exit(7)
'''
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            proc = subprocess.run([sys.executable, "-c", code, str(self.lockdir), str(self.target), self.baseline],
                                  env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 7, proc.stderr)
            self.assertEqual(self.target.read_bytes(), b"old configuration\n")
            self.replace(lease, self.target, b"ours\n", self.baseline, lambda: None)
        self.assertEqual(self.target.read_bytes(), b"ours\n")

    def test_symlink_target_and_untrusted_parent_refuse(self):
        self.target.unlink()
        self.target.symlink_to(self.root / "elsewhere")
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            with self.assertRaises(OSError):
                self.replace(lease, self.target, b"bad", self.baseline, lambda: None)
            self.target.unlink()
            self.target.write_bytes(b"old configuration\n")
            self.directory.chmod(0o777)
            with self.assertRaisesRegex(IngressTransactionError, "directory"):
                self.replace(lease, self.target, b"bad", self.baseline, lambda: None)


if __name__ == "__main__":
    unittest.main()
