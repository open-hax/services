"""Multi-file lease, recovery and cross-writer contention fixtures."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from caddy_bundle_transaction import BundleItem, apply_bundle_under_lease
from identity_ingress_transaction import IngressTransactionError
from ingress_lock import ingress_lock


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lockdir = self.root / "lock"
        self.routes = self.root / "routes"
        self.journal = self.root / "journal"
        for path in (self.lockdir, self.routes): path.mkdir(mode=0o755)
        self.journal.mkdir(mode=0o700)
        (self.lockdir / "transaction.lock").write_bytes(b"")
        (self.lockdir / "transaction.lock").chmod(0o644)
        self.caddy = self.routes / "Caddyfile"
        self.compose = self.routes / "compose.yaml"
        self.caddy.write_bytes(b"old routes")
        self.compose.write_bytes(b"old compose")
        self.caddy.chmod(0o644)
        self.compose.chmod(0o644)
        self.items = [BundleItem(self.caddy, b"new routes", self.sha(b"old routes")),
                      BundleItem(self.compose, b"new compose", self.sha(b"old compose"))]

    @staticmethod
    def sha(value): return hashlib.sha256(value).hexdigest()

    def run_bundle(self, apply, restore):
        with ingress_lock(self.lockdir, owner=os.getuid()) as lease:
            return apply_bundle_under_lease(lease, self.items, self.journal, apply, restore)

    def test_success_preserves_private_backups_and_verified_result(self):
        calls = []
        result = self.run_bundle(lambda: calls.append("healthy"), lambda: calls.append("restore"))
        self.assertEqual(calls, ["healthy"])
        self.assertEqual([row["after_sha256"] for row in result], [self.sha(b"new routes"), self.sha(b"new compose")])
        self.assertEqual((self.journal / "before-0").read_bytes(), b"old routes")
        self.assertEqual((self.journal / "before-1").read_bytes(), b"old compose")
        self.assertEqual((self.journal / "before-0").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.journal / "result").read_text().strip(), "applied-and-verified")
        self.assertFalse((self.journal / "pending").exists())

    def test_failed_recreate_restores_the_entire_bundle_and_verifies(self):
        calls = []
        def apply():
            calls.append("apply")
            raise RuntimeError("compose failed")
        with self.assertRaisesRegex(IngressTransactionError, "prior bundle restored"):
            self.run_bundle(apply, lambda: calls.append("restored"))
        self.assertEqual(calls, ["apply", "restored"])
        self.assertEqual((self.caddy.read_bytes(), self.compose.read_bytes()), (b"old routes", b"old compose"))
        self.assertEqual((self.journal / "result").read_text().strip(), "restored-and-verified")
        self.assertFalse((self.journal / "pending").exists())

    def test_foreign_write_refuses_all_rollback_and_keeps_pending(self):
        def apply():
            self.compose.write_bytes(b"newer operator change")
            raise RuntimeError("health failed")
        with self.assertRaisesRegex(IngressTransactionError, "manual recovery"):
            self.run_bundle(apply, lambda: self.fail("must not overwrite a newer writer"))
        self.assertEqual(self.compose.read_bytes(), b"newer operator change")
        self.assertEqual(self.caddy.read_bytes(), b"new routes")
        self.assertTrue((self.journal / "pending").exists())

    def test_interruption_retains_pending_and_later_run_refuses(self):
        def interrupt(): raise SystemExit(42)
        with self.assertRaises(SystemExit):
            self.run_bundle(interrupt, lambda: None)
        self.assertTrue((self.journal / "pending").exists())
        with self.assertRaisesRegex(IngressTransactionError, "empty private"):
            self.run_bundle(lambda: None, lambda: None)

    def test_stale_baseline_refuses_before_any_write_or_journal(self):
        self.compose.write_bytes(b"changed before lease")
        with self.assertRaisesRegex(IngressTransactionError, "baseline changed"):
            self.run_bundle(lambda: self.fail("must not apply"), lambda: None)
        self.assertEqual(self.caddy.read_bytes(), b"old routes")
        self.assertEqual(list(self.journal.iterdir()), [])

    def test_real_post_rename_directory_fsync_failure_restores_originals(self):
        original_replace, original_fsync = os.replace, os.fsync
        state = {'renamed': False, 'failed': False}
        directory = self.routes.stat()
        def replace(src, dst, **kwargs):
            result = original_replace(src, dst, **kwargs)
            if dst == self.caddy.name:
                state['renamed'] = True
            return result
        def fsync(fd):
            info = os.fstat(fd)
            if state['renamed'] and not state['failed'] and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                state['failed'] = True
                raise OSError('injected post-rename directory fsync failure')
            return original_fsync(fd)
        with patch.object(os, 'replace', side_effect=replace), patch.object(os, 'fsync', side_effect=fsync):
            with self.assertRaisesRegex(IngressTransactionError, 'prior bundle restored'):
                self.run_bundle(lambda: self.fail('failed fsync must not apply'), lambda: None)
        self.assertTrue(state['failed'])
        self.assertEqual((self.caddy.read_bytes(), self.compose.read_bytes()), (b'old routes', b'old compose'))
        self.assertFalse((self.journal / 'pending').exists())

    def test_persistent_post_rename_fsync_failure_retains_pending_guard(self):
        original_replace, original_fsync = os.replace, os.fsync
        state = {'renamed': False}
        directory = self.routes.stat()
        def replace(src, dst, **kwargs):
            result = original_replace(src, dst, **kwargs)
            if dst == self.caddy.name:
                state['renamed'] = True
            return result
        def fsync(fd):
            info = os.fstat(fd)
            if state['renamed'] and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                raise OSError('persistent target directory fsync failure')
            return original_fsync(fd)
        with patch.object(os, 'replace', side_effect=replace), patch.object(os, 'fsync', side_effect=fsync):
            with self.assertRaisesRegex(IngressTransactionError, 'manual recovery'):
                self.run_bundle(lambda: None, lambda: None)
        self.assertTrue((self.journal / 'pending').exists())

    def test_recovery_callback_mutating_restored_file_retains_guard(self):
        def apply():
            raise RuntimeError('fixture failure')
        def restore():
            self.caddy.write_bytes(b'changed after restore')
        with self.assertRaisesRegex(IngressTransactionError, 'manual recovery'):
            self.run_bundle(apply, restore)
        self.assertTrue((self.journal / 'pending').exists())

    def test_guard_unlink_fsync_failure_does_not_undo_verified_apply(self):
        original_unlink, original_fsync = os.unlink, os.fsync
        directory = self.journal.stat()
        state = {'unlinked': False, 'failed': False}
        def unlink(name, **kwargs):
            result = original_unlink(name, **kwargs)
            if name == 'pending':
                state['unlinked'] = True
            return result
        def fsync(fd):
            info = os.fstat(fd)
            if state['unlinked'] and not state['failed'] and (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                state['failed'] = True
                raise OSError('injected guard settlement fsync failure')
            return original_fsync(fd)
        with patch.object(os, 'unlink', side_effect=unlink), patch.object(os, 'fsync', side_effect=fsync):
            with self.assertRaisesRegex(IngressTransactionError, 'settlement requires recovery'):
                self.run_bundle(lambda: None, lambda: self.fail('verified apply must not be rolled back'))
        self.assertTrue((self.journal / 'pending').exists())
        self.assertEqual((self.journal / 'result').read_text(), 'applied-and-verified\n')
        self.assertEqual((self.caddy.read_bytes(), self.compose.read_bytes()), (b'new routes', b'new compose'))

    def test_website_writer_process_cannot_acquire_lease_mid_bundle(self):
        runtime, managed = self.root / "website-runtime", self.root / "managed"
        runtime.mkdir(mode=0o755)
        managed.mkdir(mode=0o755)
        (runtime / "website-staging.caddy.candidate").write_bytes(b"new website")
        (runtime / "website-staging.caddy.candidate").chmod(0o644)
        (managed / "website-staging.caddy").write_bytes(b"old website")
        (managed / "website-staging.caddy").chmod(0o644)
        command = '''from ingress_lock import ingress_lock,IngressLockError
from website_ingress_writer import run_under_lease
from pathlib import Path
import hashlib,json,os,sys
runtime,managed=Path(sys.argv[2]),Path(sys.argv[3])
def docker(args):
 if args[:3]==["inspect","--format","{{json .Mounts}}"]:
  return json.dumps([{"Source":str(managed),"Destination":"/etc/caddy/managed","RW":False}]).encode()
 if args[:3]==["inspect","--format","{{json .Config.Cmd}}"]:
  return json.dumps(["caddy","run","--config","/etc/caddy/Caddyfile","--adapter","caddyfile"]).encode()
 if "cat" in args:
  return b"import /etc/caddy/managed/*.caddy\\n" if args[-1]=="/etc/caddy/Caddyfile" else (managed/"website-staging.caddy").read_bytes()
 return b"ok"
try:
 with ingress_lock(sys.argv[1],owner=os.getuid()) as lease:
  run_under_lease(lease,hashlib.sha256(b"new website").hexdigest(),runtime=runtime,managed=managed,docker_run=docker,journal=runtime/"journal")
except IngressLockError:sys.exit(7)
'''
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        args = [sys.executable, "-c", command, str(self.lockdir), str(runtime), str(managed)]
        def apply():
            proc = subprocess.run(args, env=env, capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 7, proc.stderr)
            self.assertEqual((self.caddy.read_bytes(), self.compose.read_bytes()), (b"new routes", b"new compose"))
            self.assertEqual((managed / "website-staging.caddy").read_bytes(), b"old website")
        self.run_bundle(apply, lambda: None)
        proc = subprocess.run(args, env=env, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((managed / "website-staging.caddy").read_bytes(), b"new website")


if __name__ == "__main__":
    unittest.main()
