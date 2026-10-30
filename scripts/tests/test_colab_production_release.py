"""Gateway-only transaction and real private-copy laws; no host mutation."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_production_release as m
import colab_production_admission

ROOT = Path(__file__).resolve().parents[2]


class FakeHost:
    def __init__(self, fail=None):
        self.fail, self.calls, self.held = fail, [], False

    def step(self, name):
        self.calls.append(name)
        if self.fail == name: raise RuntimeError('uncertain native effect')

    @contextmanager
    def lock(self):
        self.step('lock')
        yield

    def preflight(self): self.step('preflight')
    def image(self): self.step('image')
    def claim(self):
        self.held = True
        self.step('claim')
    def record(self, name, outcome): self.step(name + ':' + outcome)
    def freeze(self): self.step('freeze')
    def snapshot(self): self.step('snapshot')
    def rehearse(self): self.step('rehearse')
    def unfreeze(self): self.step('unfreeze')
    def cutover(self): self.step('cutover')
    def verify(self):
        self.step('verify')
        return {'verified': True}
    def publish(self, proof):
        self.step('publish')
        self.held = False
        return proof
    def legacy_restart(self): self.step('forbidden-legacy-restart')


class ReleaseTests(unittest.TestCase):
    def test_all_mutation_steps_follow_hold_and_verified_snapshot_precedes_cutover(self):
        host = FakeHost()
        self.assertEqual(m.execute(host), {'verified': True})
        self.assertEqual(host.calls[:4], ['lock', 'preflight', 'image', 'claim'])
        self.assertLess(host.calls.index('snapshot:succeeded'), host.calls.index('cutover:pending'))
        self.assertLess(host.calls.index('rehearse:succeeded'), host.calls.index('unfreeze:pending'))
        self.assertLess(host.calls.index('verify:succeeded'), host.calls.index('publish'))
        self.assertFalse(host.held)

    def test_each_uncertain_outcome_retains_hold_without_retry_rollback_or_receipt(self):
        for step in ('claim', 'freeze', 'snapshot', 'rehearse', 'unfreeze', 'cutover', 'verify',
                     'cutover:pending', 'cutover:succeeded'):
            with self.subTest(step=step):
                host = FakeHost(step)
                with self.assertRaises(RuntimeError): m.execute(host)
                self.assertTrue(host.held)
                self.assertEqual(host.calls.count(step), 1)
                self.assertNotIn('publish', host.calls)
                self.assertNotIn('forbidden-legacy-restart', host.calls)

    def fixture_state(self, root):
        root.mkdir(mode=0o700)
        (root / 'oauth').mkdir(mode=0o700)
        for name in ('bridge-token', 'gateway-token', 'axxium-client.json'):
            (root / name).write_text('synthetic-private-fixture-' + name)
            (root / name).chmod(0o600)
        with sqlite3.connect(root / 'oauth/oauth.sqlite3') as db:
            db.execute('CREATE TABLE original(value TEXT)')
            db.execute('INSERT INTO original VALUES (?)', ('preserve-fixture',))
        (root / 'oauth/oauth.sqlite3').chmod(0o600)

    def test_real_backup_restore_copy_and_sqlite_integrity_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(m, 'OWNER_UID', os.getuid()):
            root = Path(temporary)
            state, transaction = root / 'state', root / 'transaction'
            self.fixture_state(state)
            transaction.mkdir(mode=0o700)
            before = m.state_inventory(state)
            host = object.__new__(m.Host)
            host.transaction, host.protected = transaction, before
            with patch.object(m, 'STATE', state): host.snapshot()
            self.assertEqual(m.state_inventory(state), before)
            self.assertEqual(m.state_inventory(transaction / 'before-state'), before)
            self.assertEqual(m.state_inventory(transaction / 'restore-copy'), before)
            with self.assertRaises(m.Refused): m.copy_state(state, transaction / 'restore-copy', before)
            self.assertEqual(m.state_inventory(state), before)

    def test_symlink_hardlink_exposed_file_or_changed_snapshot_fails_closed(self):
        for bad in ('symlink', 'hardlink', 'exposed', 'changed'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temporary, patch.object(m, 'OWNER_UID', os.getuid()):
                root = Path(temporary)
                state = root / 'state'
                self.fixture_state(state)
                before = m.state_inventory(state)
                if bad == 'symlink': (state / 'escape').symlink_to('/etc/passwd')
                if bad == 'hardlink': os.link(state / 'bridge-token', state / 'duplicate')
                if bad == 'exposed': (state / 'bridge-token').chmod(0o644)
                if bad == 'changed': (state / 'bridge-token').write_text('changed-fixture')
                with self.assertRaises(m.Refused): m.copy_state(state, root / 'backup', before)
                self.assertFalse((root / 'backup').exists())

    def test_corrupt_sqlite_is_not_a_verified_restore(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(m, 'OWNER_UID', os.getuid()):
            root = Path(temporary)
            state, transaction = root / 'state', root / 'transaction'
            self.fixture_state(state)
            (state / 'oauth/oauth.sqlite3').write_bytes(b'corrupt-database')
            transaction.mkdir(mode=0o700)
            host = object.__new__(m.Host)
            host.transaction, host.protected = transaction, m.state_inventory(state)
            with patch.object(m, 'STATE', state), self.assertRaises(sqlite3.DatabaseError): host.snapshot()
            self.assertTrue((transaction / 'before-state').exists())
            self.assertTrue((transaction / 'restore-copy').exists())

    def candidate(self):
        return {'runtime_image_id': 'sha256:' + '1' * 64, 'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:' + '1' * 64,
                'source_sha': 'a' * 40, 'image_id': 'sha256:' + '2' * 64}

    def record(self):
        candidate = self.candidate()
        env = {'COLAB_AUTH_PROFILE': 'production', 'COLAB_BRIDGE_TOKEN_FILE': '/run/colab/bridge-token',
            'COLAB_BRIDGE_URL': 'http://127.0.0.1:18023/mcp', 'COLAB_COMPUTE_URL': 'http://127.0.0.1:18025/mcp',
            'COLAB_OAUTH_BASE_URL': m.BASE, 'COLAB_OAUTH_STATE_DIR': '/var/lib/colab-oauth',
            'COLAB_AXXIUM_CLIENT_FILE': '/run/colab/axxium-client.json', 'PATH': '/usr/bin:/bin'}
        return {'Image': candidate['runtime_image_id'], 'State': {'Running': True, 'Paused': False},
            'Config': {'Image': candidate['image_reference'], 'Cmd': m.COMMAND, 'Entrypoint': None,
                'User': '1000:1000', 'Env': [k + '=' + v for k, v in env.items()], 'Labels': {
                    'com.docker.compose.project': 'colab-mcp', 'com.docker.compose.service': 'gateway',
                    'org.opencontainers.image.source': 'https://github.com/open-hax/cloud-cuckoo',
                    'org.opencontainers.image.revision': candidate['source_sha'],
                    'org.opencontainers.image.licenses': 'GPL-3.0-or-later'}},
            'HostConfig': {'NetworkMode': 'host', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                'SecurityOpt': ['no-new-privileges:true'], 'Memory': 268435456, 'NanoCpus': 500000000,
                'PidsLimit': 128, 'RestartPolicy': {'Name': 'unless-stopped'}, 'Tmpfs': {'/tmp': 'mode=1777,size=32m'}},
            'Mounts': [{'Type': 'bind', 'Source': str(m.STATE), 'Destination': '/run/colab', 'RW': False},
                {'Type': 'bind', 'Source': str(m.STATE / 'oauth'), 'Destination': '/var/lib/colab-oauth', 'RW': True}]}

    def test_actual_candidate_requires_exact_image_auth_profile_mounts_and_isolation(self):
        m.validate_candidate(self.record(), self.candidate())
        mutations = [lambda r: r.update(Image='sha256:' + '0' * 64),
            lambda r: r['Config'].update(User='root'), lambda r: r['Config'].update(Cmd=['sh']),
            lambda r: r['Config']['Env'].append('COLAB_AUTH_PROFILE=transport-staging'),
            lambda r: r['Config']['Env'].append('COLAB_TOKEN=synthetic-legacy-key'),
            lambda r: r['HostConfig'].update(Memory=0), lambda r: r['HostConfig'].update(ReadonlyRootfs=False),
            lambda r: r['Mounts'][0].update(RW=True), lambda r: r['Mounts'].append(r['Mounts'][0])]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(m.Refused):
                record = copy.deepcopy(self.record())
                mutation(record)
                m.validate_candidate(record, self.candidate())

    def test_wrong_and_legacy_key_denial_is_checked_without_a_grant_or_tool_call(self):
        with patch.object(m, 'request', return_value=(401, b'')) as request:
            m.denials(m.ADDRESS, 'synthetic-old-credential')
            self.assertEqual([c.kwargs['token'] for c in request.call_args_list],
                [None, 'services-invalid-credential', 'synthetic-old-credential'])
            self.assertTrue(all(c.args[0] == m.ADDRESS + '/mcp' and c.kwargs['post'] for c in request.call_args_list))
        with patch.object(m, 'request', return_value=(200, b'')), self.assertRaises(m.Refused):
            m.denials(m.ADDRESS, 'synthetic-old-credential')

    def test_healthy_receipt_is_durable_before_hold_is_cleared(self):
        host = object.__new__(m.Host)
        events = []
        host.plan, host.controller, host.qualification_hash = self.candidate(), 'b' * 40, 'c' * 64
        host.transaction, host.checkout, host.compose = Path('/fixture/transaction'), Path('/fixture/controller'), Path('/fixture/compose')
        host.record = lambda name, result: events.append(('record', name, result))
        host.pending = SimpleNamespace(unlink=lambda: events.append(('unlink', 'hold')))
        with patch.object(m, 'file_hash', return_value='d' * 64), \
             patch.object(m, 'write_new', side_effect=lambda path, value: events.append(('write', path.name))), \
             patch.object(m, 'sync_directory', side_effect=lambda path: events.append(('sync', str(path)))):
            result = host.publish({'gateway_container_id': 'e' * 64, 'unrelated_runtime_preserved': True})
        self.assertEqual(events[:3], [('write', 'healthy-receipt.json'), ('record', 'transaction', 'healthy'), ('unlink', 'hold')])
        self.assertFalse(result['human_login_verified'])
        self.assertFalse(result['google_execution_verified'])

    def test_failed_receipt_write_cannot_clear_hold(self):
        host = object.__new__(m.Host)
        host.plan, host.controller, host.qualification_hash = self.candidate(), 'b' * 40, 'c' * 64
        host.transaction, host.checkout, host.compose = Path('/fixture/transaction'), Path('/fixture/controller'), Path('/fixture/compose')
        removed = []
        host.pending = SimpleNamespace(unlink=lambda: removed.append(True))
        with patch.object(m, 'file_hash', return_value='d' * 64), patch.object(m, 'write_new', side_effect=OSError('uncertain write')):
            with self.assertRaises(OSError): host.publish({})
        self.assertEqual(removed, [])

    def test_source_compose_selects_only_gateway_and_has_single_parsed_tmpfs(self):
        value = yaml.safe_load((ROOT / 'digitalocean/services/colab-mcp/compose.yaml').read_text())
        self.assertEqual(set(value['services']), {'gateway'})
        gateway = value['services']['gateway']
        self.assertEqual(gateway['tmpfs'], ['/tmp:size=32m,mode=1777'])
        self.assertEqual(gateway['environment']['COLAB_AUTH_PROFILE'], 'production')
        self.assertEqual((gateway['mem_limit'], gateway['cpus'], gateway['pids_limit']), ('256m', 0.5, 128))
        self.assertEqual(gateway['command'], m.COMMAND)
        self.assertEqual(m.OWNER_UID, 1000)

    def test_image_validation_reuses_accepted_manifest_config_and_license_boundary(self):
        self.assertIs(m.Host.image, m.StageHost.image)

    def test_actual_preflight_compares_pinned_full_id_to_untruncated_native_listing(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(m, 'OWNER_UID', os.getuid()):
            root = Path(temporary)
            runtime, controller = root / 'runtime', root / 'controller'
            runtime.mkdir(mode=0o700)
            controller.mkdir(mode=0o700)
            compose = runtime / 'compose.yaml'
            compose.write_text('synthetic-incumbent-compose')
            compose.chmod(0o664)
            record = self.record()
            expected = {**colab_production_admission.PREDECESSOR,
                'compose_sha256': m.digest(compose.read_bytes()), 'environment_sha256': m.environment_hash(record)}
            record.update(Id=expected['container_id'], Image=expected['image_id'], Name='/colab-mcp-gateway-1')
            record['Config']['Image'] = expected['image_reference']
            host = object.__new__(m.Host)
            host.full_plan, host.plan = {'predecessor': expected}, self.candidate()
            host.pending, host.environment = runtime / '.pending', controller / '.env'
            host.proof = controller / 'proof.json'
            host.proof.write_text(json.dumps({'repository': 'open-hax/cloud-cuckoo', 'source_sha': host.plan['source_sha']}))
            host.proof.chmod(0o600)
            host.inspect = lambda selector: record
            host.identity = lambda: None
            host.inventory = lambda: {}
            def command(args, **kwargs):
                self.assertEqual(args[:3], ['docker', 'ps', '-aq'])
                identifier = expected['container_id'] if '--no-trunc' in args else expected['container_id'][:12]
                return identifier.encode() + b'\n'
            def private(path, **kwargs):
                return host.proof.read_bytes() if path == host.proof else b'synthetic-legacy-credential-long-enough'
            with patch.object(m, 'RUNTIME', runtime), patch.object(m, 'file_hash', return_value=expected['compose_sha256']), \
                 patch.object(m, 'run', side_effect=command) as run, patch.object(m, 'private_read', side_effect=private), \
                 patch.object(m, 'production_admission', return_value={'admitted': True}), \
                 patch.object(m, 'state_inventory', return_value={}), patch.object(m.socket, 'socket'):
                host.preflight()
                self.assertIn('--no-trunc', run.call_args.args[0])


if __name__ == '__main__': unittest.main()
