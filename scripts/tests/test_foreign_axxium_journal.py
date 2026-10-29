"""Real-filesystem storage tests; these do not establish host writer enrollment."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from foreign_axxium_adoption import Refused, execute, validate_plan
from foreign_axxium_journal import DurableAdoptionJournal
from test_foreign_axxium_adoption import FakeBackend, encode, plan


class TestLease:
    def __init__(self, fd, path):
        self.fd, self.path, self.allowed = fd, path, True
    def recheck(self):
        info, current = os.fstat(self.fd), self.path.lstat()
        return (self.allowed and stat.S_ISREG(current.st_mode)
                and (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino))


@contextmanager
def test_lock(path):
    """Actual process exclusion fixture, not the installed global host lock."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield TestLease(fd, path)
    finally:
        os.close(fd)


class NativeJournalTests(unittest.TestCase):
    def setUp(self):
        # /tmp is intentionally an untrusted writable ancestor. The storage
        # contract requires a controller-owned hierarchy for these tests too.
        self.tmp = tempfile.TemporaryDirectory(prefix='adoption-journal-test-',
                                               dir=Path.home()/'.local/share')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.directory = self.base/'journal'; self.directory.mkdir(mode=0o700)
        self.lock_path = self.base/'host.lock'; self.lock_path.write_bytes(b'')
        self.lock_path.chmod(0o600)
        self.raw = encode(plan()); self.digest = validate_plan(self.raw)[1]
        self.backend = FakeBackend()
        self.journal = self.new_journal()

    def new_journal(self, factory=None, owner=None):
        return DurableAdoptionJournal(factory or (lambda: test_lock(self.lock_path)),
            self.directory, owner=os.getuid() if owner is None else owner)

    def test_fsync_backed_plan_and_ordered_immutable_records(self):
        self.assertEqual(execute(self.raw, self.backend, self.journal), 'healthy')
        self.assertEqual((self.directory/'plan.json').read_bytes(), self.raw)
        files = sorted(p for p in self.directory.iterdir() if p.name != 'plan.json')
        events = [json.loads(p.read_text()) for p in files]
        self.assertEqual([e['sequence'] for e in events], list(range(len(events))))
        self.assertEqual(events[0]['name'], 'verify_initial')
        self.assertEqual(events[0]['status'], 'pending')
        self.assertEqual((events[-1]['name'], events[-1]['status']), ('transaction', 'healthy'))
        self.assertTrue(all(e['plan_sha256'] == self.digest for e in events))
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600
                            for p in self.directory.iterdir()))
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        other = FakeBackend()
        with self.assertRaises(Refused): execute(self.raw, other, self.new_journal())
        self.assertEqual(other.calls, [])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_crash_after_external_effect_retains_pending_and_refuses_replay(self):
        program = '''import os,sys
from pathlib import Path
from foreign_axxium_adoption import execute
from foreign_axxium_journal import DurableAdoptionJournal
from test_foreign_axxium_adoption import FakeBackend,encode,plan
from test_foreign_axxium_journal import test_lock
base=Path(sys.argv[1])
class Crashing(FakeBackend):
 def stop_incumbent(self,p,d):
  (base/'effect').write_bytes(b'observed')
  os._exit(42)
j=DurableAdoptionJournal(lambda:test_lock(base/'host.lock'),base/'journal',owner=os.getuid())
execute(encode(plan()),Crashing(),j)
'''
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(SCRIPTS), str(SCRIPTS/'tests')))}
        result = subprocess.run([sys.executable, '-c', program, str(self.base)],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.base/'effect').read_bytes(), b'observed')
        names = [p.name for p in self.directory.iterdir()]
        self.assertTrue(any(name.endswith('-stop_incumbent-pending.json') for name in names))
        self.assertFalse(any(name.endswith('-stop_incumbent-succeeded.json') for name in names))
        with self.assertRaises(Refused): execute(self.raw, self.backend, self.journal)
        self.assertEqual(self.backend.calls, [])

    def test_other_process_cannot_enter_the_provided_lock(self):
        program = '''import os,sys
from pathlib import Path
from foreign_axxium_journal import DurableAdoptionJournal
from test_foreign_axxium_journal import test_lock
base=Path(sys.argv[1])
j=DurableAdoptionJournal(lambda:test_lock(base/'host.lock'),base/'journal',owner=os.getuid())
try:
 with j.lock(): pass
except BlockingIOError: sys.exit(17)
'''
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(SCRIPTS), str(SCRIPTS/'tests')))}
        with self.journal.lock():
            result = subprocess.run([sys.executable, '-c', program, str(self.base)],
                                    env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)

    def test_operations_require_lock_and_exact_claim(self):
        with self.assertRaises(Refused): self.journal.claim(self.digest, self.raw)
        with self.journal.lock():
            with self.assertRaises(Refused): self.journal.claim('0'*64, self.raw)
            self.assertTrue(self.journal.claim(self.digest, self.raw))
            for digest, name, status in ((self.digest, '../escape', 'pending'),
                    ('0'*64, 'verify_initial', 'pending'),
                    (self.digest, 'verify_initial', 'healthy')):
                with self.assertRaises(Refused): self.journal.record(digest, name, status)
            self.journal.record(self.digest, 'transaction', 'healthy')
            with self.assertRaises(Refused): self.journal.record(self.digest, 'verify_after', 'pending')

    def test_missing_wrong_owner_modes_and_symlink_ancestor_refuse(self):
        with self.assertRaises(Refused):
            with self.new_journal(owner=os.getuid()+1).lock(): pass
        self.directory.chmod(0o755)
        with self.assertRaises(Refused):
            with self.journal.lock(): pass
        self.directory.chmod(0o700)
        self.base.chmod(0o777)
        with self.assertRaises(Refused):
            with self.journal.lock(): pass
        self.base.chmod(0o700)
        alias = self.base/'alias'; alias.symlink_to(self.directory, target_is_directory=True)
        linked = DurableAdoptionJournal(lambda:test_lock(self.lock_path), alias, owner=os.getuid())
        with self.assertRaises(OSError):
            with linked.lock(): pass

    def test_existing_plan_symlink_and_partial_claim_are_never_repaired(self):
        target = self.base/'target'; target.write_bytes(b'preserve')
        p = self.directory/'plan.json'; p.symlink_to(target)
        with self.assertRaises(Refused): execute(self.raw, self.backend, self.journal)
        self.assertTrue(p.is_symlink()); self.assertEqual(target.read_bytes(), b'preserve')
        p.unlink(); p.write_bytes(b'partial claim')
        with self.assertRaises(Refused): execute(self.raw, self.backend, self.new_journal())
        self.assertEqual(p.read_bytes(), b'partial claim'); self.assertEqual(self.backend.calls, [])

    def test_plan_file_or_directory_replacement_stops_before_next_effect(self):
        for replace_directory in (False, True):
            with self.subTest(directory=replace_directory):
                if (self.directory/'plan.json').exists():
                    for p in self.directory.iterdir(): p.unlink()
                directory = self.directory
                class Replacing(FakeBackend):
                    def verify(self, p, d, phase):
                        self.calls.append('verify_'+phase)
                        if replace_directory:
                            directory.rename(directory.with_name('moved'))
                            directory.mkdir(mode=0o700)
                        else:
                            replacement=directory/'other';replacement.write_bytes(encode(plan()))
                            replacement.chmod(0o600);os.replace(replacement,directory/'plan.json')
                        return True
                b = Replacing()
                with self.assertRaises(Refused): execute(self.raw,b,self.new_journal())
                self.assertEqual(b.calls, ['verify_initial'])

    def test_changed_plan_contents_or_hardlink_refuse_next_record(self):
        with self.journal.lock():
            self.journal.claim(self.digest,self.raw)
            os.link(self.directory/'plan.json',self.base/'plan-link')
            with self.assertRaises(Refused):self.journal.record(self.digest,'verify_initial','pending')
            (self.base/'plan-link').unlink()
            # Fixing the observed object cannot silently resume the same turn.
            with self.assertRaises(Refused):self.journal.record(self.digest,'verify_initial','pending')
            (self.directory/'plan.json').write_bytes(b'changed')
            with self.assertRaises(Refused):self.journal.record(self.digest,'verify_initial','pending')

    def test_sync_uncertainty_cannot_retry_another_phase_in_same_lock(self):
        with self.journal.lock():
            self.journal.claim(self.digest,self.raw)
            with mock.patch('foreign_axxium_journal.os.fsync',side_effect=OSError('synthetic')):
                with self.assertRaises(OSError):self.journal.record(self.digest,'verify_initial','pending')
            before={p.name:p.read_bytes() for p in self.directory.iterdir()}
            with self.assertRaises(Refused):self.journal.record(self.digest,'different_phase','pending')
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir()})

    def test_fifo_plan_replacement_refuses_without_blocking(self):
        with self.journal.lock():
            self.journal.claim(self.digest,self.raw)
            (self.directory/'plan.json').unlink()
            os.mkfifo(self.directory/'plan.json',0o600)
            with self.assertRaises(Refused):self.journal.record(self.digest,'verify_initial','pending')

    def test_in_place_plan_content_change_refuses_next_record(self):
        with self.journal.lock():
            self.journal.claim(self.digest,self.raw)
            (self.directory/'plan.json').write_bytes(b'changed')
            with self.assertRaises(Refused):self.journal.record(self.digest,'verify_initial','pending')

    def test_fsync_failure_keeps_claim_and_prevents_unrecorded_effect(self):
        with mock.patch('foreign_axxium_journal.os.fsync',side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):execute(self.raw,self.backend,self.journal)
        self.assertTrue((self.directory/'plan.json').exists())
        self.assertEqual(self.backend.calls,[])
        with self.assertRaises(Refused):execute(self.raw,self.backend,self.new_journal())

    def test_phase_fsync_failure_preserves_pending_without_entering_backend(self):
        real_fsync=os.fsync; calls=[]
        def fail_phase(fd):
            calls.append(fd)
            if len(calls)==3:raise OSError('synthetic phase fsync failure')
            return real_fsync(fd)
        with mock.patch('foreign_axxium_journal.os.fsync',side_effect=fail_phase):
            with self.assertRaises(OSError):execute(self.raw,self.backend,self.journal)
        self.assertEqual(self.backend.calls,[])
        self.assertTrue((self.directory/'000000-verify_initial-pending.json').exists())
        with self.assertRaises(Refused):execute(self.raw,self.backend,self.new_journal())

    def test_existing_phase_is_never_overwritten(self):
        path=self.directory/'000000-verify_initial-pending.json';path.write_bytes(b'partial')
        with self.assertRaises(FileExistsError):execute(self.raw,self.backend,self.journal)
        self.assertEqual(path.read_bytes(),b'partial');self.assertEqual(self.backend.calls,[])


if __name__ == '__main__': unittest.main()
