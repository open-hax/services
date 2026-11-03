import json
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foreign_axxium_adoption import execute, KnownFailure, Refused, validate_plan


def plan():
    return {'schema': 'axxium-foreign-adoption/v1', 'host': 'fixture.invalid',
            'retirement_sha256': 'd'*64, 'rollback_compatibility_sha256': 'e'*64,
            'backup_policy_sha256': 'f'*64,
            'incumbent': {'container_id': '1'*64, 'image_id': 'sha256:'+'2'*64,
                          'source_sha': None, 'runtime_sha256': '3'*64,
                          'database_sha256': '4'*64, 'environment_sha256': '5'*64},
            'candidate': {'source_sha': '6'*40, 'controller_sha': '7'*40,
                          'image_id': 'sha256:'+'8'*64, 'archive_sha256': '9'*64}}


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


class FileJournal:
    """Isolated test journal, NOT a production-authority implementation."""
    def __init__(self, directory):
        self.path = Path(directory)/'claimed'; self.events = []
    @contextmanager
    def lock(self):
        with self.path.with_name('lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
    def claim(self, digest, raw):
        try:
            with self.path.open('xb') as out:
                out.write(digest.encode()+b'\n'+raw); out.flush(); os.fsync(out.fileno())
            return True
        except FileExistsError:
            return False
    def record(self, digest, name, status):
        self.events.append((name, status))


class FakeBackend:
    def __init__(self, fail=None, mode='known'):
        self.calls = []; self.fail = fail; self.mode = mode; self.receipts = []
    def run(self, name, plan, digest):
        self.calls.append(name)
        if name == self.fail:
            if self.mode == 'known': raise KnownFailure()
            if self.mode == 'uncertain': raise TimeoutError()
            return False
        return True
    def verify(self,p,d,phase): return self.run('verify_'+phase,p,d)
    def backup(self,p,d,label): return self.run('backup_'+label,p,d)
    def verify_backup(self,p,d,label): return self.run('verify_backup_'+label,p,d)
    def stop_incumbent(self,p,d): return self.run('stop_incumbent',p,d)
    def start_candidate(self,p,d): return self.run('start_candidate',p,d)
    def verify_candidate_health(self,p,d): return self.run('candidate_health',p,d)
    def verify_rollback(self,p,d): return self.run('rollback_compatibility',p,d)
    def rollback_incumbent(self,p,d): return self.run('rollback_incumbent',p,d)
    def verify_incumbent_health(self,p,d): return self.run('incumbent_health',p,d)
    def commit_healthy_selection(self,p,d):
        result=self.run('commit_healthy_selection',p,d)
        if result: self.receipts.append({'source_sha':p['candidate']['source_sha'],
                                         'image_id':p['candidate']['image_id'], 'status':'healthy'})
        return result


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.journal=FileJournal(self.tmp.name); self.backend=FakeBackend(); self.raw=encode(plan())
    def test_healthy_receipt_after_both_verified_backups_and_final_health(self):
        self.assertEqual(execute(self.raw,self.backend,self.journal),'healthy')
        c=self.backend.calls
        self.assertLess(c.index('verify_backup_before'),c.index('stop_incumbent'))
        self.assertLess(c.index('candidate_health'),c.index('backup_after'))
        self.assertLess(c.index('verify_backup_after'),c.index('commit_healthy_selection'))
        self.assertLess(len(c)-1-c[::-1].index('candidate_health'),c.index('commit_healthy_selection'))
        self.assertEqual(self.backend.receipts,[{'source_sha':'6'*40,'image_id':'sha256:'+'8'*64,'status':'healthy'}])
        self.assertEqual(self.journal.events[-1],('transaction','healthy'))
        self.assertTrue(self.journal.path.read_bytes().endswith(self.raw))
    def test_rollback_only_after_failed_candidate_backup_and_compatibility(self):
        self.backend.fail='candidate_health'
        self.assertEqual(execute(self.raw,self.backend,self.journal),'rolled_back')
        c=self.backend.calls
        self.assertLess(c.index('verify_backup_failed'),c.index('rollback_compatibility'))
        self.assertLess(c.index('rollback_compatibility'),c.index('rollback_incumbent'))
        self.assertLess(c.index('rollback_incumbent'),c.index('incumbent_health'))
        self.assertFalse(self.backend.receipts)
        self.assertNotIn('backup_after',c)
    def test_missing_retirement_or_backup_refuses_without_runtime_change(self):
        for failure in ('verify_initial','verify_before_fence','backup_before','verify_backup_before','verify_pre_stop'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                b=FakeBackend(failure,mode='false')
                with self.assertRaises(Refused): execute(self.raw,b,FileJournal(tmp))
                self.assertNotIn('stop_incumbent',b.calls); self.assertFalse(b.receipts)
    def test_uncertain_start_never_rolls_back_or_claims_healthy(self):
        self.backend.fail='start_candidate'; self.backend.mode='uncertain'
        with self.assertRaises(Refused): execute(self.raw,self.backend,self.journal)
        self.assertNotIn('rollback_incumbent',self.backend.calls);self.assertFalse(self.backend.receipts)
        self.assertIn(('start_candidate','uncertain'),self.journal.events)
    def test_rollback_refused_if_failure_backup_or_compatibility_unverified(self):
        for second_failure in ('verify_failed_fence','backup_failed','verify_backup_failed','rollback_compatibility'):
            with self.subTest(second_failure=second_failure), tempfile.TemporaryDirectory() as tmp:
                class Failing(FakeBackend):
                    def run(self,name,p,d):
                        if name=='candidate_health': self.calls.append(name);raise KnownFailure()
                        return super().run(name,p,d)
                b=Failing(second_failure,mode='false')
                with self.assertRaises(Refused): execute(self.raw,b,FileJournal(tmp))
                self.assertNotIn('rollback_incumbent',b.calls); self.assertFalse(b.receipts)
    def test_no_replay_after_any_prior_attempt(self):
        self.backend.fail='verify_initial';self.backend.mode='false'
        with self.assertRaises(Refused):execute(self.raw,self.backend,self.journal)
        b=FakeBackend()
        with self.assertRaises(Refused):execute(self.raw,b,self.journal)
        self.assertEqual(b.calls,[])
    def test_rejects_unknown_source_fabrication_and_plan_changes(self):
        for mutate in (lambda p:p['incumbent'].update(source_sha='a'*40),
                       lambda p:p['candidate'].update(image_id=p['incumbent']['image_id']),
                       lambda p:p.update(extra='attested')):
            p=plan();mutate(p)
            with self.assertRaises(Refused):validate_plan(encode(p))
        with self.assertRaises(Refused):validate_plan(self.raw.replace(b'"schema":',b'"schema":"duplicate","schema":'))
        with self.assertRaises(Refused):validate_plan(json.dumps(plan()).encode())
    def test_publish_uncertainty_does_not_rollback_or_retry(self):
        self.backend.fail='commit_healthy_selection';self.backend.mode='uncertain'
        with self.assertRaises(Refused):execute(self.raw,self.backend,self.journal)
        self.assertNotIn('rollback_incumbent',self.backend.calls)
        self.assertFalse(self.backend.receipts)
        with self.assertRaises(Refused):execute(self.raw,FakeBackend(),self.journal)
    def test_after_backup_failure_never_publishes_or_assumes_rollback(self):
        for failure in ('verify_after_fence','backup_after','verify_backup_after','verify_pre_commit'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                b=FakeBackend(failure,mode='false')
                with self.assertRaises(Refused):execute(self.raw,b,FileJournal(tmp))
                self.assertNotIn('rollback_incumbent',b.calls)
                self.assertNotIn('commit_healthy_selection',b.calls)
                self.assertFalse(b.receipts)
    def test_journal_failure_stops_before_unrecorded_effect(self):
        class BadJournal(FileJournal):
            def record(self,digest,name,status):
                if name=='stop_incumbent' and status=='pending':raise OSError('synthetic storage failure')
                return super().record(digest,name,status)
        with self.assertRaises(OSError):execute(self.raw,self.backend,BadJournal(self.tmp.name))
        self.assertNotIn('stop_incumbent',self.backend.calls)
        self.assertFalse(self.backend.receipts)
    def test_backend_cannot_mutate_reviewed_plan(self):
        class Mutating(FakeBackend):
            def verify(self,p,d,phase):
                p['incumbent']['source_sha']='a'*40
                return True
        with self.assertRaises(Refused):execute(self.raw,Mutating(),self.journal)
        self.assertIn(('verify_initial','uncertain'),self.journal.events)

if __name__=='__main__': unittest.main()
