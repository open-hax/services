from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

SCRIPTS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPTS))
import axxium_minimal_release as release


def plan():
    return json.loads((SCRIPTS.parent/'digitalocean/services/axxium/minimal-production-plan.json').read_text())


class FakeHost:
    """Transaction ordering double, never native qualification evidence."""
    def __init__(self,fail=None,uncertain=False):
        self.calls=[];self.events=[];self.fail=fail;self.uncertain=uncertain
    @contextmanager
    def lock(self):yield
    def call(self,name):
        self.calls.append(name)
        if self.fail==name:
            raise (release.Uncertain if self.uncertain else release.KnownFailure)('fixture')
    def preflight(self,p):self.call('preflight')
    def claim(self,p):self.call('claim')
    def recovery_preflight(self,p):self.call('recovery_preflight')
    def recovery_claim(self,p):self.call('recovery_claim')
    def record(self,n,s):self.events.append((n,s))
    def fence(self,p):self.call('fence')
    def stop_app(self,p):self.call('stop')
    def backup(self,p,label):self.call('backup_'+label)
    def provision_identity(self,p):self.call('provision_identity')
    def rehearse(self,p,label,*,candidate):self.call('rehearse_'+label+('_candidate_old' if candidate else '_old'))
    def start_candidate(self,p):self.call('start')
    def private_health(self,p):self.call('private_health')
    def final_check(self,p):self.call('final_check')
    def publish(self,p):self.call('publish')
    def public_health(self,p):self.call('public_health')
    def commit(self,p):self.call('commit')
    def rollback(self,p,stopped,candidate_attempted):
        self.call('rollback');self.rollback_flags=(stopped,candidate_attempted)
    def receipt(self,s):return {'status':s}
    def finish(self,p,s):self.call('finish_'+s)


class MinimalTransitionTests(unittest.TestCase):
    def test_fixed_plan_and_closed_native_observation(self):
        self.assertEqual(release.validate_plan(plan())['source_sha'],release.SOURCE)
        for mutate in (lambda p:p.update(source_sha='1'*40),lambda p:p.update(image_id='sha256:'+'1'*64),
                       lambda p:p.update(extra='unreviewed'),lambda p:p['expected'].update(caddy_proxy_ip='true')):
            p=plan();mutate(p)
            with self.assertRaises(release.Refused):release.validate_plan(p)

    def test_rollback_receipt_never_attests_candidate_source_to_foreign_image(self):
        h=release.NativeHost();h.incumbent_image_id=plan()['expected']['app_image_id']
        result=h.receipt('rolled_back')
        self.assertIsNone(result['source_sha']);self.assertEqual(result['requested_source_sha'],release.SOURCE)
        self.assertEqual(result['image_id'],h.incumbent_image_id)
        self.assertNotEqual(result['image_id'],result['candidate_image_id'])

    def test_fenced_backup_rehearsal_precedes_candidate_and_publication(self):
        h=FakeHost();self.assertEqual(release.execute(plan(),h),{'status':'healthy'})
        self.assertEqual(h.calls,['preflight','claim','fence','stop','backup_before','provision_identity',
            'rehearse_before_candidate_old','start','private_health','stop','backup_after',
            'rehearse_after_old','start','final_check','publish','public_health','commit','finish_healthy'])
        self.assertEqual(h.events[-1],('transaction','healthy'))

    def test_exact_recovery_runs_fresh_backup_and_rehearsal_before_candidate(self):
        h=FakeHost();self.assertEqual(release.execute(plan(),h,recover_interrupted=True),{'status':'healthy'})
        self.assertEqual(h.calls[:8],['recovery_preflight','recovery_claim','fence','stop',
            'backup_before','provision_identity','rehearse_before_candidate_old','start'])
        self.assertNotIn('preflight',h.calls);self.assertNotIn('claim',h.calls)

    def test_recovery_refusal_or_uncertainty_never_runs_deployment(self):
        for step in ('recovery_preflight','recovery_claim'):
            h=FakeHost(step,uncertain=True)
            with self.assertRaises(release.Uncertain):release.execute(plan(),h,recover_interrupted=True)
            self.assertNotIn('fence',h.calls);self.assertNotIn('start',h.calls)
            self.assertEqual(h.events,[])

    def test_recovery_preflight_requires_complete_identity(self):
        guard=SimpleNamespace(verify=mock.Mock())
        h=release.NativeHost();h._preflight_shared=mock.Mock()
        with mock.patch.object(release.os,'getuid',return_value=1000), mock.patch.dict(sys.modules,{'axxium_incident_guard':guard}):
            for status in ('ready','recoverable'):
                h.command=mock.Mock(return_value=json.dumps({'verified':True,'status':status}).encode())
                with self.assertRaises(release.Refused):h.recovery_preflight(plan())
            h.command=mock.Mock(return_value=b'{"verified":true,"status":"complete"}')
            h.recovery_preflight(plan())
        self.assertEqual(guard.verify.call_count,4)

    def test_recovery_preflight_refusal_does_not_proceed(self):
        class GuardBlocked(RuntimeError):pass
        guard=SimpleNamespace(verify=mock.Mock(side_effect=GuardBlocked('synthetic')),
                              Blocked=GuardBlocked)
        h=release.NativeHost();h._preflight_shared=mock.Mock()
        with mock.patch.object(release.os,'getuid',return_value=1000), mock.patch.dict(sys.modules,{'axxium_incident_guard':guard}):
            with self.assertRaises(release.Refused):h.recovery_preflight(plan())
        h._preflight_shared.assert_not_called()
        guard.verify.assert_called_once()

    def test_recovery_preflight_refuses_wrong_uid_before_incident_check(self):
        guard=SimpleNamespace(verify=mock.Mock())
        h=release.NativeHost();h._preflight_shared=mock.Mock()
        with mock.patch.object(release.os,'getuid',return_value=1001), mock.patch.dict(sys.modules,{'axxium_incident_guard':guard}):
            with self.assertRaises(release.Refused):h.recovery_preflight(plan())
        guard.verify.assert_not_called()
        h._preflight_shared.assert_not_called()

    def test_recovery_claim_failure_marks_uncertain_and_retains_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);runtime=base/'runtime';runtime.mkdir();state=base/'state';state.mkdir()
            caddy=base/'Caddyfile';caddy.write_bytes(b'caddy')
            (runtime/'compose.yaml').write_bytes(b'compose');(runtime/'.env').write_bytes(b'env')
            h=release.NativeHost();h.operation=state/'operations'/release.SOURCE
            h.pending_bytes=b'original hold';h.recheck_lock=mock.Mock()
            handoff=SimpleNamespace(handoff_files=mock.Mock(side_effect=OSError('synthetic')))
            with mock.patch.multiple(release,RUNTIME=runtime,STATE=state,CADDY=caddy), mock.patch.dict(sys.modules,{'axxium_interrupted_handoff':handoff}):
                with self.assertRaises(release.Uncertain):h.recovery_claim(plan())
            self.assertTrue(h.uncertain);self.assertEqual(h.pending_bytes,b'original hold')
            self.assertFalse(h.require_existing_identity)

    def test_recovery_claim_archives_prior_backup_and_initializes_fresh_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);runtime=base/'runtime';runtime.mkdir();state=base/'state';state.mkdir()
            ops=state/'operations';ops.mkdir(mode=0o700);old=ops/release.SOURCE;old.mkdir(mode=0o700)
            caddy=base/'Caddyfile';caddy.write_bytes(b'caddy')
            (runtime/'compose.yaml').write_bytes(b'compose');(runtime/'.env').write_bytes(b'env')
            pending=runtime/'pending';pending.write_bytes(b'hold');pending.chmod(0o600)
            dump=old/'before.pgdump';dump.write_bytes(b'backup');dump.chmod(0o600)
            backup=old/'before-backup.json';backup.write_text(json.dumps({'path':str(dump),
                'sha256':hashlib.sha256(b'backup').hexdigest()}));backup.chmod(0o600)
            h=release.NativeHost();h.operation=old;h.pending_bytes=b'hold';h.recheck_lock=mock.Mock()
            with mock.patch.multiple(release,RUNTIME=runtime,STATE=state,CADDY=caddy,PENDING=pending):
                h.recovery_claim(plan())
            manifest=json.loads((old/'parent-handoff.json').read_text())
            self.assertEqual(Path(manifest['archived_backup_path']).read_bytes(),b'backup')
            self.assertEqual(json.loads((old/'plan.json').read_text()),plan())
            self.assertEqual((old/'incumbent.env').read_bytes(),b'env')
            self.assertEqual(h.sequence,0);self.assertEqual(h.backups,{})
            self.assertTrue(h.require_existing_identity);self.assertEqual(pending.read_bytes(),b'hold')

    def test_recovery_provisioning_passes_require_existing(self):
        h=release.NativeHost();h.operation=Path('/synthetic');h.lock_fd=7
        h.backups={'before':{'sha256':'synthetic'}};h.require_existing_identity=True
        h.unchanged_dependencies=mock.Mock();h.recheck_lock=mock.Mock();h.identity_check=mock.Mock()
        h.command=mock.Mock(return_value=b'')
        h.provision_identity(plan())
        args=h.command.call_args.args[0]
        self.assertIn('--require-existing',args)
        self.assertEqual(h.command.call_args.kwargs['pass_fds'],(7,))

    def test_preflight_failure_does_not_claim_or_change_runtime(self):
        h=FakeHost('preflight')
        with self.assertRaises(release.KnownFailure):release.execute(plan(),h)
        self.assertEqual(h.calls,['preflight']);self.assertEqual(h.events,[])

    def test_known_candidate_failure_requires_native_rollback(self):
        h=FakeHost('start');self.assertEqual(release.execute(plan(),h),{'status':'rolled_back'})
        self.assertEqual(h.rollback_flags,(True,True))
        self.assertNotIn('publish',h.calls);self.assertNotIn('commit',h.calls)
        self.assertEqual(h.events[-1],('transaction','rolled_back'))

    def test_unknown_candidate_effect_never_rolls_back_or_commits(self):
        h=FakeHost('start',uncertain=True)
        with self.assertRaises(release.Uncertain):release.execute(plan(),h)
        self.assertNotIn('rollback',h.calls);self.assertNotIn('commit',h.calls)
        self.assertEqual(h.events[-1],('start_candidate','pending'))

    def test_failed_journal_after_effect_does_not_authorize_compensation(self):
        class BadJournal(FakeHost):
            def record(self,n,s):
                if (n,s)==('start_candidate','succeeded'):raise OSError('fixture storage failure')
                super().record(n,s)
        h=BadJournal()
        with self.assertRaises(OSError):release.execute(plan(),h)
        self.assertNotIn('rollback',h.calls);self.assertNotIn('publish',h.calls)

    def test_private_input_refuses_links_modes_fifo_and_unbounded_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'plan';p.write_bytes(b'{}');p.chmod(0o600)
            self.assertEqual(release.private_read(p),b'{}')
            p.chmod(0o644)
            with self.assertRaises(release.Refused):release.private_read(p)
            p.unlink();os.mkfifo(p,0o600)
            with self.assertRaises(release.Refused):release.private_read(p)
            p.unlink();p.symlink_to('/dev/null')
            with self.assertRaises(OSError):release.private_read(p)

    def test_lock_excludes_other_process_and_detects_inode_replacement(self):
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(release,'LOCK',Path(tmp)/'lock'):
            h=release.NativeHost()
            with h.lock():
                program="import sys;from pathlib import Path;import axxium_minimal_release as m;m.LOCK=Path(sys.argv[1]);h=m.NativeHost();\nwith h.lock():pass"
                p=subprocess.run([sys.executable,'-c',program,str(release.LOCK)],
                    env={**os.environ,'PYTHONPATH':str(SCRIPTS)},capture_output=True,timeout=5)
                self.assertNotEqual(p.returncode,0)
                replacement=Path(tmp)/'other';replacement.write_bytes(b'');replacement.chmod(0o600)
                os.replace(replacement,release.LOCK)
                with self.assertRaises(release.Uncertain):h.recheck_lock()

    def test_rehearsal_env_never_reuses_production_database_or_alternate_url(self):
        original={'DB_HOST':'axxium-db','DB_PORT':'5432','DB_NAME':'axxium',
                  'DB_USER':'axxium','DB_PASSWORD':'literal$dollar','JWT_SECRET':'retained'}
        name='axxium_verify_'+'a'*24
        values=release.rehearsal_environment(original,name,'172.18.0.9')
        self.assertEqual(values['DB_NAME'],name);self.assertEqual(values['DB_PASSWORD'],'literal$dollar')
        self.assertEqual(original['DB_NAME'],'axxium')
        for key in ('DB_URL','DATABASE_URL','PGDATABASE','PGHOST','PGSERVICE'):
            with self.assertRaises(release.Refused):
                release.rehearsal_environment({**original,key:'production'},name,'172.18.0.9')
        for bad in ('axxium','postgres','axxium_verify_not-generated'):
            with self.assertRaises(release.Refused):release.rehearsal_environment(original,bad,'172.18.0.9')

    def test_unpreserved_incumbent_writable_layer_stops_replacement(self):
        class Host(release.NativeHost):
            changes=b'C /run\nA /run/axxium\nA /run/axxium/google-client.json\n'
            def command(self,*args,**kwargs):return self.changes
        h=Host();h.original_layer(plan())
        h.changes+=b'A /app/unpreserved-state.db\n'
        with self.assertRaises(release.Uncertain):h.original_layer(plan())

    def test_nonzero_mutator_is_uncertain_and_observation_failure_is_known(self):
        result=SimpleNamespace(returncode=1,stdout=b'',stderr=b'private diagnostic')
        h=release.NativeHost()
        with mock.patch.object(release.subprocess,'run',return_value=result):
            with self.assertRaises(release.KnownFailure):h.command(['fixture-read'])
            self.assertFalse(h.uncertain)
            with self.assertRaises(release.Uncertain) as error:h.command(['fixture-effect'],effect=True)
            self.assertNotIn('private diagnostic',str(error.exception));self.assertTrue(h.uncertain)

    def test_bound_ingress_inode_observes_in_place_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'Caddyfile';path.write_bytes(b'old');path.chmod(0o600)
            with path.open('rb') as bound:
                class Host(release.NativeHost):
                    def unchanged_dependencies(self,p):pass
                    def recheck_lock(self):pass
                    def validate_caddy(self,data):pass
                    def command(self,args,**kwargs):
                        if args[3]=='cat':bound.seek(0);return bound.read()
                        self.reload_effect=kwargs.get('effect')
                        return b''
                h=Host();h.original=b'old';h.maintenance=b'fenced';h.final=b'new'
                before=path.stat().st_ino
                with mock.patch.object(release,'CADDY',path):h.replace_caddy(plan(),b'new')
                bound.seek(0);self.assertEqual(bound.read(),b'new')
                self.assertEqual(path.stat().st_ino,before);self.assertTrue(h.reload_effect)

    def test_failed_in_place_sync_does_not_reload_or_compensate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'Caddyfile';path.write_bytes(b'old')
            class Host(release.NativeHost):
                def unchanged_dependencies(self,p):pass
                def recheck_lock(self):pass
                def validate_caddy(self,data):pass
                def command(self,*args,**kwargs):raise AssertionError('No command after uncertain write')
            h=Host();h.original=b'old';h.maintenance=b'fenced';h.final=b'new'
            with mock.patch.object(release,'CADDY',path),mock.patch.object(release.os,'fsync',side_effect=OSError('fixture')):
                with self.assertRaises(OSError):h.replace_caddy(plan(),b'new')
            self.assertTrue(h.uncertain)

    def test_cli_refusal_returns_nonzero_without_echoing_private_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'plan';path.write_bytes(b'{"private":"sentinel"}');path.chmod(0o600)
            result=subprocess.run([sys.executable,str(SCRIPTS/'axxium_minimal_release.py'),
                '--plan',str(path),'--plan-sha256',release.file_digest(path),'--check'],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2)
            self.assertNotIn('sentinel',result.stdout+result.stderr)

    @contextmanager
    def native_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);runtime=base/'runtime';runtime.mkdir(mode=0o700)
            state=base/'state';state.mkdir(mode=0o700)
            caddy=base/'Caddyfile';caddy.write_bytes(b'incumbent')
            (runtime/'compose.yaml').write_bytes(b'compose')
            (runtime/'.env').write_bytes(b'private=sentinel');(runtime/'.env').chmod(0o600)
            with mock.patch.multiple(release,RUNTIME=runtime,STATE=state,CADDY=caddy,
                    LOCK=runtime/'lock',PENDING=runtime/'pending'):
                yield base

    def test_pending_hold_precedes_effect_and_only_exact_terminal_clears(self):
        with self.native_storage():
            h=release.NativeHost('a'*64)
            with h.lock():
                h.claim(plan())
                self.assertEqual(release.private_read(release.PENDING),h.pending_bytes)
                with self.assertRaises(OSError):h.finish(plan(),'healthy')
                self.assertTrue(release.PENDING.exists())
                h.private_write(h.operation/'deployment.json',json.dumps(h.receipt('healthy')).encode())
                h.record('transaction','healthy');h.finish(plan(),'healthy')
                self.assertFalse(release.PENDING.exists())
            other=release.NativeHost('a'*64)
            with other.lock():
                with self.assertRaises(release.Refused):other.claim(plan())

    def test_uncertain_marker_completion_recreates_hold(self):
        with self.native_storage():
            h=release.NativeHost('a'*64)
            with h.lock():
                h.claim(plan())
                h.private_write(h.operation/'deployment.json',json.dumps(h.receipt('healthy')).encode())
                h.record('transaction','healthy')
                with mock.patch.object(h,'sync_directory',side_effect=OSError('fixture directory sync')):
                    with self.assertRaises(release.Uncertain):h.finish(plan(),'healthy')
                self.assertTrue(release.PENDING.exists())
                self.assertEqual(release.private_read(release.PENDING),h.pending_bytes)

    def test_crashed_claim_leaves_production_hold_and_no_automatic_reentry(self):
        with self.native_storage() as base:
            program='''import os,sys,json
from pathlib import Path
import axxium_minimal_release as m
b=Path(sys.argv[1]);m.RUNTIME=b/'runtime';m.STATE=b/'state';m.CADDY=b/'Caddyfile';m.LOCK=m.RUNTIME/'lock';m.PENDING=m.RUNTIME/'pending'
h=m.NativeHost('a'*64)
with h.lock():
 h.claim(json.loads(Path(sys.argv[2]).read_text()))
 os._exit(42)
'''
            fixed=base/'publicplan';fixed.write_text(json.dumps(plan()))
            p=subprocess.run([sys.executable,'-c',program,str(base),str(fixed)],
                env={**os.environ,'PYTHONPATH':str(SCRIPTS)},capture_output=True,timeout=5)
            self.assertEqual(p.returncode,42,p.stderr)
            self.assertTrue(release.PENDING.exists())
            with mock.patch.object(release.os,'getuid',return_value=1000):
                with self.assertRaises(release.Refused):release.NativeHost().preflight(plan())


if __name__=='__main__':unittest.main()
