import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_interrupted_handoff as handoff


class HandoffFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.operations = self.root / 'operations'; self.operations.mkdir(mode=0o700)
        self.pending = self.root / 'pending'; self.pending.write_bytes(b'hold'); self.pending.chmod(0o600)
        self.old = self.operations / handoff.SOURCE; self.old.mkdir(mode=0o700)
        self.dump = b'verified-before-synthetic-backup'
        self.put(self.old/'before.pgdump', self.dump)
        self.put(self.old/'before-backup.json', json.dumps({'path': str(self.old/'before.pgdump'), 'sha256': hashlib.sha256(self.dump).hexdigest()}).encode())
        self.put(self.old/'008-candidate_rollback_rehearsal-pending.json', b'old journal')
        self.snapshots = {'incumbent.compose.yaml': b'compose', 'incumbent.env': b'env', 'incumbent.Caddyfile': b'caddy'}
    def tearDown(self): self.tmp.cleanup()
    def put(self, path, data): path.write_bytes(data); path.chmod(0o600)
    def call(self, check=lambda: None):
        return handoff.handoff_files(operations=self.operations, pending=self.pending,
            expected_hold=b'hold', plan=b'fresh plan', snapshots=self.snapshots, recheck=check)
    def test_preserves_old_backup_and_continuous_hold(self):
        observations=[]
        result=self.call(lambda: observations.append(self.pending.read_bytes()))
        archive=Path(result['archived_operation'])
        self.assertEqual((archive/'before.pgdump').read_bytes(), self.dump)
        self.assertEqual(result['old_backup_record_path'], str(self.old/'before.pgdump'))
        self.assertEqual(Path(result['archived_backup_path']).read_bytes(), self.dump)
        self.assertEqual((archive/'008-candidate_rollback_rehearsal-pending.json').read_bytes(), b'old journal')
        self.assertEqual((self.old/'plan.json').read_bytes(), b'fresh plan')
        self.assertEqual(observations, [b'hold']*5)
        self.assertEqual(self.pending.read_bytes(), b'hold')
    def test_existing_archive_never_replaced(self):
        archive=self.operations/(handoff.SOURCE+'.interrupted.'+handoff.RUN)
        archive.mkdir(mode=0o700); self.put(archive/'keep', b'untouched')
        with self.assertRaises(FileExistsError): self.call()
        self.assertEqual((archive/'keep').read_bytes(), b'untouched')
        self.assertEqual((self.old/'before.pgdump').read_bytes(), self.dump)
        self.assertEqual(self.pending.read_bytes(), b'hold')
    def test_interruption_at_each_transition_keeps_hold_and_backup(self):
        # Inject exceptions before each persistence step; never start a deployment.
        original_write, original_rename, original_sync = handoff._write, handoff._rename_noreplace, handoff._sync
        for function, original, count in (('_write', original_write, 6), ('_rename_noreplace', original_rename, 2), ('_sync', original_sync, 10)):
            for at in range(1,count+1):
                with self.subTest(function=function,at=at), tempfile.TemporaryDirectory() as sub:
                    root=Path(sub); ops=root/'ops';ops.mkdir(mode=0o700); old=ops/handoff.SOURCE;old.mkdir(mode=0o700)
                    pending=root/'pending';self.put(pending,b'hold')
                    self.put(old/'before.pgdump',self.dump)
                    self.put(old/'before-backup.json',json.dumps({'path':str(old/'before.pgdump'),'sha256':hashlib.sha256(self.dump).hexdigest()}).encode())
                    calls=[0]
                    def fail(*args,**kwargs):
                        calls[0]+=1
                        if calls[0]==at: raise OSError('injected')
                        return original(*args,**kwargs)
                    with mock.patch.object(handoff,function,side_effect=fail), self.assertRaises(OSError):
                        handoff.handoff_files(operations=ops,pending=pending,expected_hold=b'hold',plan=b'plan',snapshots=self.snapshots,recheck=lambda:None)
                    self.assertEqual(pending.read_bytes(),b'hold')
                    saved=ops/(handoff.SOURCE+'.interrupted.'+handoff.RUN)/'before.pgdump'
                    self.assertTrue((old/'before.pgdump').exists() or saved.exists())
    def test_unmatched_backup_or_hold_refuses_before_handoff(self):
        self.pending.write_bytes(b'changed')
        with self.assertRaises(handoff.HandoffRefused): self.call()
        self.assertTrue((self.old/'before.pgdump').exists())
        self.assertEqual(len(list(self.operations.iterdir())),1)

if __name__ == '__main__': unittest.main()
