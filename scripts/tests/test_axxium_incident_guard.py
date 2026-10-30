"""Synthetic fixed-incident validation; no host or service operations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'axxium_incident_guard.py'
spec = importlib.util.spec_from_file_location('incident_guard', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class IncidentGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.operation = self.root / 'operation'
        self.operation.mkdir()
        self.pending = self.root / 'pending'
        self.caddy = self.root / 'Caddyfile'
        self.original = b'incumbent route'
        self.caddy.write_bytes(self.original)
        self.plan = {'source_sha': guard.SOURCE, 'image_id': 'candidate', 'expected': {
            'caddyfile_sha256': hashlib.sha256(self.original).hexdigest(),
            'app_container_id': guard.PROTECTED_IDS[0], 'app_image_id': 'incumbent',
            'app_environment_sha256': 'envhash', 'caddy_proxy_ip': '10.0.0.2'}}
        self.raw = json.dumps(self.plan).encode()
        self.hash = hashlib.sha256(self.raw).hexdigest()
        self.pending.write_text(json.dumps({'schema': 'axxium-production-pending/v1',
            'operation': str(self.operation), 'source_sha': guard.SOURCE, 'plan_sha256': self.hash}, sort_keys=True))
        (self.operation / 'plan.json').write_bytes(self.raw)
        dump = b'backup fixture'
        (self.operation / 'before.pgdump').write_bytes(dump)
        (self.operation / 'before-backup.json').write_text(json.dumps({
            'path': str(self.operation / 'before.pgdump'), 'database': guard.CLONE,
            'sha256': hashlib.sha256(dump).hexdigest()}))
        (self.operation / 'incumbent.Caddyfile').write_bytes(self.original)
        for i, phase in enumerate(guard.EXPECTED_PHASES):
            name, status = phase.rsplit('-', 1)
            (self.operation / f'{i:03d}-{phase}.json').write_text(json.dumps(
                {'sequence': i, 'phase': name, 'status': status}))
        self.app = {'Id': guard.PROTECTED_IDS[0], 'Image': 'incumbent',
            'State': {'Status': 'running', 'Health': {'Status': 'healthy'}},
            'Config': {'Env': ['DB_HOST=axxium-db']},
            'NetworkSettings': {'Networks': {'open-hax': {'Aliases': ['axxium']}}}}
        self.proxy = {'NetworkSettings': {'Networks': {'open-hax': {'IPAddress': '10.0.0.2'}}}}
        self.rehearsal = {'Id': guard.REHEARSAL_ID, 'Name': '/' + guard.REHEARSAL_NAME,
            'Image': 'candidate', 'State': {'Status': 'running'}, 'ExecIDs': None,
            'Config': {'Env': ['DB_HOST=axxium-db', 'DB_NAME=' + guard.CLONE],
                       'Labels': {'open-hax.axxium-operation': guard.SOURCE}},
            'NetworkSettings': {'Networks': {'axxium_database': {}}}}
        self.identity_status = 'complete'
        owner = self
        class Host:
            lock_fd = 3
            plan_digest = owner.hash
            def recheck_lock(self): pass
            def unchanged_dependencies(self, plan): pass
            def environment_hash(self, app): return 'envhash'
            def original_layer(self, plan): pass
            def validate_caddy(self, data): pass
            def http(self, path): return (200, b'')
            def sql(self, database, query): return b'10\n' if database == 'axxium' else b'idle\n'
            def inspect(self, name):
                return {release.APP: owner.app, release.PROXY: owner.proxy,
                        guard.REHEARSAL_ID: owner.rehearsal, guard.PROTECTED_IDS[0]: owner.app}[name]
            def command(self, args):
                if args[0] == 'docker' and args[1] == 'exec':
                    info = owner.caddy.stat()
                    return f'{info.st_dev}:{info.st_ino}'.encode()
                if args[0] == 'docker' and args[1:3] == ['network', 'inspect']:
                    if args[3] == 'axxium_database': return b'[{"Internal": true}]'
                    return json.dumps([{'Containers': {guard.PROTECTED_IDS[0]: {}}}]).encode()
                if args[0] == 'docker' and args[1] == 'ps': return (guard.REHEARSAL_ID+'\n').encode()
                if '--preflight' in args: return json.dumps({'verified': True, 'status': owner.identity_status}).encode()
                raise AssertionError(args)
        release = self.release = SimpleNamespace(
            __file__=str(self.root / 'scripts' / 'axxium_minimal_release.py'),
            PENDING=self.pending, CADDY=self.caddy, STATE=self.root, APP='app', PROXY='proxy',
            validate_plan=lambda plan: plan, private_read=lambda path: Path(path).read_bytes(),
            maintenance=lambda data, digest: b'fence', prepare=lambda data, digest: b'final')
        self.host = Host()
        self.stack = [patch.object(guard, 'OPERATION', self.operation), patch.object(guard, 'PLAN_HASH', self.hash),
                      patch.object(guard, 'COLAB', self.root / 'colab-mcp'),
                      patch.object(guard, 'private_file', side_effect=lambda path: path),
                      patch.object(guard.os, 'getuid', return_value=1000),
                      patch.object(guard, 'observed_clients'), patch.object(guard.time, 'sleep')]
        for item in self.stack:
            item.start()
            self.addCleanup(item.stop)

    def validate(self):
        return guard.validate(self.host, self.release, self.plan, self.raw)

    def test_valid_incident_returns_exact_hold_without_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.validate(), self.pending.read_bytes())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.host.original, self.original)

    def test_exact_journal_and_backup_are_required(self):
        for file in ('008-candidate_rollback_rehearsal-pending.json', 'before.pgdump'):
            with self.subTest(file=file):
                path = self.operation / file
                old = path.read_bytes()
                path.write_bytes(b'tampered')
                with self.assertRaises((guard.Blocked, ValueError, KeyError)):
                    self.validate()
                path.write_bytes(old)

    def test_journal_sequence_and_backup_path_drift_refused_without_writes(self):
        cases = [
            ('008-candidate_rollback_rehearsal-pending.json', {'sequence': 7, 'phase': 'candidate_rollback_rehearsal', 'status': 'pending'}),
            ('008-candidate_rollback_rehearsal-pending.json', {'sequence': 8, 'phase': 'other', 'status': 'pending'}),
            ('before-backup.json', {'path': '/wrong', 'database': guard.CLONE, 'sha256': hashlib.sha256(b'backup fixture').hexdigest()}),
            ('before-backup.json', {'path': str(self.operation / 'before.pgdump'), 'database': guard.CLONE, 'sha256': '0'*64}),
        ]
        for filename, contents in cases:
            with self.subTest(contents=contents):
                path = self.operation / filename
                original = path.read_bytes()
                path.write_text(json.dumps(contents))
                before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                with self.assertRaises(guard.Blocked): self.validate()
                self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
                path.write_bytes(original)

    def test_extra_four_digit_journal_phase_is_rejected(self):
        (self.operation / '1000-transaction-healthy.json').write_text('{}')
        with self.assertRaises(guard.Blocked): self.validate()

    def test_colab_hold_and_aliased_service_path_are_rejected(self):
        colab = self.root / 'colab-mcp'
        colab.mkdir()
        marker = colab / '.colab-production-pending'
        marker.write_text('held')
        with self.assertRaises(guard.Blocked): self.validate()
        marker.unlink()
        colab.rmdir()
        colab.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(guard.Blocked): self.validate()

    def test_route_container_or_orphan_drift_refuse(self):
        for mutate, restore in ((lambda: self.caddy.write_bytes(b'other'), lambda: self.caddy.write_bytes(self.original)),
                                (lambda: self.app['State'].update(Status='exited'), lambda: self.app['State'].update(Status='running')),
                                (lambda: self.rehearsal.update(ExecIDs=['active']), lambda: self.rehearsal.update(ExecIDs=None))):
            mutate()
            with self.assertRaises(guard.Blocked): self.validate()
            restore()

    def test_ready_or_recoverable_identity_is_refused(self):
        for status in ('ready', 'recoverable'):
            self.identity_status = status
            with self.subTest(status=status), self.assertRaises(guard.Blocked): self.validate()

    def test_identity_preflight_refusal_never_repairs(self):
        command = self.host.command
        def refused(args):
            if '--preflight' in args: raise RuntimeError('synthetic wrong actor or staged identity')
            return command(args)
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(self.host, 'command', side_effect=refused):
            with self.assertRaises(RuntimeError): self.validate()
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_changed_incumbent_layer_or_caddy_bind_refuses(self):
        with patch.object(self.host, 'original_layer', side_effect=guard.Blocked('layer')):
            with self.assertRaises(guard.Blocked): self.validate()
        command = self.host.command
        def changed_inode(args):
            if args[:2] == ['docker', 'exec']: return b'0:0'
            return command(args)
        with patch.object(self.host, 'command', side_effect=changed_inode):
            with self.assertRaises(guard.Blocked): self.validate()

    def test_orphan_clone_or_network_drift_refuses(self):
        for field, bad in (
            ('env', ['DB_HOST=axxium-db', 'DB_NAME=production']),
            ('networks', {'axxium_database': {}, 'open-hax': {}}),
        ):
            with self.subTest(field=field):
                owner, key = ((self.rehearsal['Config'], 'Env') if field == 'env'
                              else (self.rehearsal['NetworkSettings'], 'Networks'))
                previous = owner[key]
                owner[key] = bad
                try:
                    with self.assertRaises(guard.Blocked): self.validate()
                finally:
                    owner[key] = previous

    def test_hold_or_plan_mismatch_refused(self):
        with patch.object(self.host, 'plan_digest', 'x'*64):
            with self.assertRaises(guard.Blocked): self.validate()
        self.pending.write_text('{}')
        with self.assertRaises(guard.Blocked): self.validate()

    def test_clone_client_states_are_restricted(self):
        for states in ([], ['idle']): self.assertTrue(guard.safe_clone_states(states))
        for states in (['active'], ['idle', 'idle'], ['idle in transaction']): self.assertFalse(guard.safe_clone_states(states))

    def test_verify_loads_admitted_plan_and_sets_original_hold(self):
        admitted = self.root / 'digitalocean/services/axxium/minimal-production-plan.json'
        admitted.parent.mkdir(parents=True)
        admitted.write_bytes(self.raw)
        module_name = self.host.__class__.__module__
        with patch.dict(sys.modules, {module_name: self.release}), \
             patch.object(guard, 'validate', return_value=b'exact hold') as validate:
            guard.verify(self.host, self.plan)
        validate.assert_called_once_with(self.host, self.release, self.plan, self.raw)
        self.assertEqual(self.host.operation, self.operation)
        self.assertEqual(self.host.pending_bytes, b'exact hold')

    def test_unrelated_exec_exception_cannot_hide_transaction_marker(self):
        proc = self.root / 'proc' / '1234'
        proc.mkdir(parents=True)
        argv = ['/usr/bin/docker', 'exec', '-i', '--user', '1000', guard.KNOXX_NAME,
                'echo', guard.REHEARSAL_NAME]
        (proc / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
        real_scandir = guard.os.scandir
        # setUp's mocked scanner is bypassed by reloading the source function
        spec2 = importlib.util.spec_from_file_location('guard_scan_test', SCRIPT)
        scan = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(scan)
        with patch.object(scan.os, 'scandir', side_effect=lambda _: real_scandir(proc.parent)), \
             patch.object(scan.os, 'getpid', return_value=99999), \
             patch.object(scan, 'inspect_process_targets') as inspect:
            with self.assertRaises(scan.Blocked): scan.observed_clients()
            inspect.assert_not_called()


if __name__ == '__main__': unittest.main()
