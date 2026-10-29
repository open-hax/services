"""Safety and cross-process exclusion for the unactivated ingress lock primitive."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingress_lock import IngressLockError, ingress_lock


class IngressLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'ingress'
        self.directory.mkdir(mode=0o755)
        self.path = self.directory / 'transaction.lock'
        self.path.write_text('')
        self.path.chmod(0o644)
        self.owner = os.getuid()

    def test_excludes_a_second_process_and_releases_after_exit(self):
        program = '''from ingress_lock import ingress_lock, IngressLockError
import os,sys
try:
 with ingress_lock(sys.argv[1],owner=os.getuid()): print('acquired')
except IngressLockError: sys.exit(7)
'''
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        with ingress_lock(self.directory, owner=self.owner) as held:
            held.recheck()
            blocked = subprocess.run([sys.executable, '-c', program, str(self.directory)],
                                     env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(7, blocked.returncode, blocked.stderr)
        allowed = subprocess.run([sys.executable, '-c', program, str(self.directory)],
                                 env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        self.assertEqual('acquired', allowed.stdout.strip())

    def test_rejects_missing_link_unsafe_mode_and_wrong_owner(self):
        self.path.unlink()
        with self.assertRaises(IngressLockError):
            with ingress_lock(self.directory, owner=self.owner): pass
        self.path.symlink_to('/dev/null')
        with self.assertRaises(IngressLockError):
            with ingress_lock(self.directory, owner=self.owner): pass
        self.path.unlink()
        self.path.write_text('')
        self.path.chmod(0o666)
        with self.assertRaises(IngressLockError):
            with ingress_lock(self.directory, owner=self.owner): pass
        self.path.chmod(0o644)
        with self.assertRaises(IngressLockError):
            with ingress_lock(self.directory, owner=self.owner + 1): pass
        self.directory.chmod(0o777)
        with self.assertRaises(IngressLockError):
            with ingress_lock(self.directory, owner=self.owner): pass

    def test_detects_replaced_inode_before_effect(self):
        with ingress_lock(self.directory, owner=self.owner) as held:
            other = self.directory / 'replacement'
            other.write_text('')
            other.chmod(0o644)
            os.replace(other, self.path)
            with self.assertRaises(IngressLockError):
                held.recheck()

    def test_detects_replaced_directory_and_preserves_writer_error(self):
        with ingress_lock(self.directory, owner=self.owner) as held:
            previous = self.directory.with_name('old-ingress')
            self.directory.rename(previous)
            self.directory.mkdir(mode=0o755)
            with self.assertRaises(IngressLockError):
                held.recheck()
            self.directory.rmdir()
            previous.rename(self.directory)
        with self.assertRaisesRegex(OSError, 'writer failure'):
            with ingress_lock(self.directory, owner=self.owner):
                raise OSError('writer failure')

    def test_rejects_hardlinked_lock_and_directory_symlink(self):
        os.link(self.path, self.directory / 'second-name')
        with self.assertRaises(IngressLockError):
            with ingress_lock(self.directory, owner=self.owner): pass
        (self.directory / 'second-name').unlink()
        link = Path(self.tmp.name) / 'alias'
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(IngressLockError):
            with ingress_lock(link, owner=self.owner): pass


if __name__ == '__main__':
    unittest.main()
