# SPDX-License-Identifier: GPL-3.0-or-later
"""Preservation and refusal laws for the closed transport Stage upgrade."""
from contextlib import contextmanager, closing
import os
import hashlib
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_transport_stage_upgrade as upgrade


class FakeHost:
    def __init__(self, fail=None):
        self.calls, self.fail, self.held = [], fail, False
    def step(self, name):
        self.calls.append(name)
        if name == self.fail: raise RuntimeError('uncertain command')
    @contextmanager
    def lock(self):
        self.step('lock')
        yield
    def preflight(self): self.step('preflight')
    def image(self): self.step('image')
    def claim(self):
        self.held = True
        self.step('claim')
    def record(self, name, value): self.step(name + ':' + value)
    def stop(self): self.step('stop')
    def snapshot(self): self.step('snapshot')
    def select(self): self.step('select')
    def start(self): self.step('start')
    def verify(self):
        self.step('verify')
        return {'assertions': 165}
    def publish(self, proof):
        self.step('publish')
        self.held = False
        return proof


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        (self.state / 'oauth').mkdir(mode=0o700)
        for name, raw in [('fixture-token', b'synthetic-bridge-secret\n'),
                          ('axxium-client.json', b'{"synthetic":"client"}\n'),
                          ('oauth/state', b'encrypted-fixture-bytes\x00\xff')]:
            target = self.state / name
            target.write_bytes(raw)
            target.chmod(0o600)
        selected = patch.object(upgrade, 'OWNER_UID', os.getuid())
        selected.start()
        self.addCleanup(selected.stop)

    def test_exact_private_snapshot_and_independent_restore_copy_preserve_all_bytes(self):
        expected = upgrade.tree_manifest(self.state)
        observed = upgrade.snapshot_state(self.state, self.root / 'backup')
        self.assertEqual(observed['manifest'], expected)
        self.assertEqual(upgrade.tree_manifest(self.root / 'backup/state'), expected)
        self.assertEqual(upgrade.tree_manifest(self.root / 'backup/restored'), expected)
        self.assertEqual(upgrade.tree_manifest(self.state), expected)
        self.assertEqual((self.root / 'backup').stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'backup/manifest.json').stat().st_mode & 0o777, 0o600)

    def test_existing_backup_refuses_without_touching_its_recovery_bytes(self):
        backup = self.root / 'backup'
        upgrade.snapshot_state(self.state, backup)
        original = (backup / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            upgrade.snapshot_state(self.state, backup)
        self.assertEqual((backup / 'manifest.json').read_bytes(), original)

    def test_symlinks_hardlinks_and_writable_inputs_refuse(self):
        target = self.state / 'fixture-token'
        for kind in ('symlink', 'hardlink', 'writable', 'exposed'):
            with self.subTest(kind=kind):
                linked = self.state / 'redirect'
                if kind == 'symlink':
                    linked.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(target, linked)
                elif kind == 'writable':
                    target.chmod(0o666)
                else:
                    target.chmod(0o644)
                with self.assertRaises(upgrade.Refused):
                    upgrade.tree_manifest(self.state)
                if kind in ('symlink', 'hardlink'):
                    linked.unlink()
                target.chmod(0o600)

    def test_size_and_ownership_drift_refuse(self):
        with patch.object(upgrade, 'MAX_STATE_BYTES', 1), self.assertRaises(upgrade.Refused):
            upgrade.tree_manifest(self.state)
        with patch.object(upgrade, 'OWNER_UID', os.getuid() + 1), self.assertRaises(upgrade.Refused):
            upgrade.tree_manifest(self.state)

    def test_source_drift_or_corrupt_restore_never_becomes_a_verified_backup(self):
        original = upgrade.copy_tree
        for target_name in ('state', 'restored'):
            def corrupt(source, destination):
                original(source, destination)
                if destination.name == target_name:
                    (destination / 'fixture-token').write_bytes(b'changed')
            with self.subTest(target=target_name), patch.object(upgrade, 'copy_tree', side_effect=corrupt):
                with self.assertRaises(upgrade.Refused):
                    upgrade.snapshot_state(self.state, self.root / ('backup-' + target_name))
        self.assertEqual((self.state / 'fixture-token').read_bytes(), b'synthetic-bridge-secret\n')

    def candidate(self):
        return {'source_sha': 'a' * 40, 'image_id': 'sha256:' + '1' * 64,
            'runtime_image_id': 'sha256:' + '2' * 64,
            'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:' + '2' * 64}

    def old_receipt(self):
        value = {key: value for key, value in upgrade.OLD.items() if key != 'receipt_sha256'}
        value.update(schema='colab-transport-stage-receipt/v1', phase='staging', health='success',
            source_repository='open-hax/cloud-cuckoo', compose_project=upgrade.stage.PROJECT,
            assertions=165, completed_at='2026-10-29T22:09:46Z', synthetic_actor=True,
            synthetic_upstreams=True, human_login_verified=False, google_execution_verified=False,
            production_qualified=False, oauth_flow_verified=True, gateway_forwarding_verified=True,
            session_revoked=True, unrelated_runtime_preserved=True, manifest_config_binding_verified=True)
        return json.dumps(value, sort_keys=True, indent=2).encode()

    def test_old_receipt_uses_the_exact_authenticated_producer_serialization(self):
        raw = self.old_receipt()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), upgrade.OLD['receipt_sha256'])
        self.assertEqual(upgrade.verify_old_receipt(raw)['assertions'], 165)
        for bad in (raw + b'\n', raw.replace(b'165', b'166'), raw.replace(b'19e1', b'0000')):
            with self.assertRaises(upgrade.Refused): upgrade.verify_old_receipt(bad)

    def test_native_image_precedes_hold_and_snapshot_precedes_selector_and_start(self):
        host = FakeHost()
        self.assertEqual(upgrade.execute(host), {'assertions': 165})
        self.assertEqual(host.calls[:4], ['lock', 'preflight', 'image', 'claim'])
        self.assertLess(host.calls.index('stop:succeeded'), host.calls.index('snapshot:pending'))
        self.assertLess(host.calls.index('snapshot:succeeded'), host.calls.index('select:pending'))
        self.assertLess(host.calls.index('verify:succeeded'), host.calls.index('publish:pending'))
        self.assertFalse(host.held)

    def test_unknown_native_or_journal_outcome_never_retries_restores_or_publishes(self):
        for point in ('claim', 'stop', 'snapshot', 'select', 'start', 'verify', 'select:pending', 'verify:succeeded'):
            with self.subTest(point=point):
                host = FakeHost(point)
                with self.assertRaises(RuntimeError): upgrade.execute(host)
                self.assertTrue(host.held)
                self.assertEqual(host.calls.count(point), 1)
                self.assertNotIn('publish', host.calls)

    def test_stop_uses_only_three_captured_stage_ids_without_other_service_effects(self):
        host = object.__new__(upgrade.Host)
        host.ids = ['a' * 64, 'b' * 64, 'c' * 64]
        host.old_records = {key: {'image': upgrade.OLD['runtime_image_id'], 'command': ['fixture']} for key in host.ids}
        records = [{'Id': key, 'Image': upgrade.OLD['runtime_image_id'], 'State': {'Running': False},
                    'Config': {'Cmd': ['fixture']}} for key in host.ids]
        with patch.object(upgrade.stage, 'run', side_effect=[b'', json.dumps(records).encode()]) as run:
            host.stop()
            self.assertEqual(run.call_args_list[0].args[0], ['docker', 'stop', '--time', '10', *host.ids])
            self.assertEqual(run.call_args_list[1].args[0], ['docker', 'inspect', *host.ids])

    def test_only_image_selector_changes_and_state_credentials_keep_exact_bytes(self):
        host = object.__new__(upgrade.Host)
        host.plan = self.candidate()
        host.environment = self.root / 'environment'
        host.environment.write_bytes(upgrade.OLD_SELECTOR)
        host.environment.chmod(0o600)
        host.old_selector = upgrade.OLD_SELECTOR
        host.transaction = self.root / 'transaction'
        host.transaction.mkdir(mode=0o700)
        host.state_before = upgrade.tree_manifest(self.state)
        with patch.object(upgrade.stage, 'STATE', self.state), patch.object(upgrade.stage, 'RUNTIME', self.root):
            host.select()
        self.assertEqual(host.environment.read_bytes(), ('COLAB_MCP_IMAGE=' + host.plan['image_reference'] + '\n').encode())
        self.assertEqual(upgrade.tree_manifest(self.state), host.state_before)
        self.assertEqual(host.environment.stat().st_mode & 0o777, 0o600)

    def test_graceful_sqlite_wal_checkpoint_is_backed_up_after_stop_without_rotating_credentials(self):
        path = self.state / 'oauth/cache.db'
        database = sqlite3.connect(path)
        path.chmod(0o600)
        database.execute('PRAGMA journal_mode=WAL')
        database.execute('CREATE TABLE original(value TEXT)')
        database.execute('INSERT INTO original VALUES (?)', ('preserve-committed-value',))
        database.commit()
        host = object.__new__(upgrade.Host)
        host.state_before = upgrade.tree_manifest(self.state)
        self.assertIn('oauth/cache.db-wal', host.state_before)
        database.close()
        self.assertNotEqual(upgrade.tree_manifest(self.state), host.state_before)
        host.transaction = self.root / 'transaction'
        host.transaction.mkdir(mode=0o700)
        with patch.object(upgrade.stage, 'STATE', self.state): host.snapshot()
        restored = host.transaction / 'sqlite-verification/oauth/cache.db'
        with closing(sqlite3.connect(restored.absolute().as_uri() + '?mode=ro', uri=True)) as database:
            self.assertEqual(database.execute('SELECT value FROM original').fetchall(), [('preserve-committed-value',)])
        self.assertEqual(upgrade.tree_manifest(host.transaction / 'backup/state'),
                         upgrade.tree_manifest(host.transaction / 'backup/restored'))

    def test_stop_time_credential_change_is_not_treated_as_sqlite_checkpoint(self):
        host = object.__new__(upgrade.Host)
        host.state_before = upgrade.tree_manifest(self.state)
        host.transaction = self.root / 'transaction'
        host.transaction.mkdir(mode=0o700)
        (self.state / 'fixture-token').write_bytes(b'changed-credential')
        with patch.object(upgrade.stage, 'STATE', self.state), self.assertRaises(upgrade.Refused): host.snapshot()
        self.assertFalse((host.transaction / 'backup').exists())

    def test_corrupt_quiesced_database_cannot_supply_a_verified_restore(self):
        (self.state / 'oauth/cache.db').write_bytes(b'corrupt-synthetic-database')
        (self.state / 'oauth/cache.db').chmod(0o600)
        host = object.__new__(upgrade.Host)
        host.state_before = upgrade.tree_manifest(self.state)
        host.transaction = self.root / 'transaction'
        host.transaction.mkdir(mode=0o700)
        with patch.object(upgrade.stage, 'STATE', self.state), self.assertRaises(sqlite3.DatabaseError): host.snapshot()
        self.assertTrue((host.transaction / 'backup/state/oauth/cache.db').exists())
        self.assertTrue((host.transaction / 'backup/restored/oauth/cache.db').exists())

    def records(self, plan=None):
        plan = plan or self.candidate()
        result = []
        commands = [('bridge-staging', 'transport-stage-bridge', '18773'),
                    ('compute-staging', 'transport-stage-compute', '18775'), ('gateway-staging', 'gateway', '18771')]
        for index, (role, mode, port) in enumerate(commands):
            mounts = [{'Type': 'bind', 'Source': str(upgrade.stage.STATE), 'Destination': '/run/colab-transport-stage', 'RW': False}]
            if role == 'gateway-staging':
                mounts.append({'Type': 'bind', 'Source': str(upgrade.stage.STATE / 'oauth'), 'Destination': '/var/lib/colab-transport-stage', 'RW': True})
            result.append({'Id': 'abc'[index] * 64, 'Image': plan['runtime_image_id'], 'State': {'Running': True, 'Paused': False}, 'Mounts': mounts,
                'Config': {'Image': plan['image_reference'], 'User': '1000:1000', 'Entrypoint': None,
                    'Cmd': ['knoxx-colab-mcp', mode, '--host', '127.0.0.1', '--port', port],
                    'Env': ['COLAB_AUTH_PROFILE=transport-staging'], 'Labels': {'com.docker.compose.project': upgrade.stage.PROJECT,
                        'com.docker.compose.service': role, 'org.opencontainers.image.revision': plan['source_sha']}},
                'HostConfig': {'NetworkMode': 'host', 'ReadonlyRootfs': True, 'Memory': 268435456,
                    'NanoCpus': 500000000, 'PidsLimit': 128, 'CapDrop': ['ALL'],
                    'SecurityOpt': ['no-new-privileges:true'], 'Tmpfs': {'/tmp': 'size=32m,mode=1777'}}})
        return result

    def test_actual_preflight_requests_untruncated_ids_before_matching_inspect_identity(self):
        import enroll_colab_transport_stage
        runtime = self.root / 'runtime'
        runtime.mkdir(mode=0o700)
        (runtime / 'stage-receipt.json').write_bytes(self.old_receipt())
        (runtime / 'stage-receipt.json').chmod(0o600)
        (runtime / 'environment').write_bytes(upgrade.OLD_SELECTOR)
        (runtime / 'environment').chmod(0o600)
        repository = Path(__file__).resolve().parents[2]
        old = runtime / 'controllers' / upgrade.OLD['services_sha']
        old_service = old / 'digitalocean/services/colab-mcp'
        old_service.mkdir(parents=True)
        for name in ('compose.transport-stage.yaml', 'transport-stage-plan.json'):
            target = old_service / name
            target.write_bytes((repository / 'digitalocean/services/colab-mcp' / name).read_bytes())
            target.chmod(0o600)
        (self.state / 'fixture-token').write_bytes(b'synthetic-private-fixture-token-long-enough\n')
        (self.state / 'axxium-client.json').write_text(json.dumps({'issuer': upgrade.stage.ISSUER,
            'client_id': 'knoxx-colab-transport-staging', 'client_secret': 's' * 48,
            'allowed_subjects': [upgrade.stage.ACTOR], 'allowed_client_redirect_uris': ['http://127.0.0.1:18776/callback']}))
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[:3] == ['docker', 'ps', '-aq']:
                identifiers = ['a' * 64, 'b' * 64, 'c' * 64]
                if '--no-trunc' not in args: identifiers = [value[:12] for value in identifiers]
                return ('\n'.join(identifiers) + '\n').encode()
            if args[:2] == ['docker', 'inspect']: return json.dumps(self.records(upgrade.OLD)).encode()
            self.fail('unexpected native command')
        with patch.object(upgrade.stage, 'RUNTIME', runtime), patch.object(upgrade.stage, 'STATE', self.state), \
             patch.object(upgrade.stage, 'run', side_effect=command), patch.object(upgrade.stage, 'inventory', return_value={}), \
             patch.object(enroll_colab_transport_stage, 'complete_pair') as pair, \
             patch.object(enroll_colab_transport_stage, 'actor_verified'), patch.object(enroll_colab_transport_stage, 'issuer_ready'):
            host = upgrade.Host({'candidate': self.candidate()}, 'd' * 40, old)
            host.preflight()
            self.assertEqual(set(host.ids), {'a' * 64, 'b' * 64, 'c' * 64})
            self.assertIn('--no-trunc', calls[0])
            pair.assert_called_once()

    def smoke(self):
        return {'passed': True, 'synthetic_actor': True, 'synthetic_upstreams': True, 'oauth_flow_verified': True,
            'gateway_forwarding_verified': True, 'execution_denied': True, 'session_revoked': True,
            'google_execution_verified': False, 'human_login_verified': False, 'assertions': 165}

    def test_verification_reuses_actual_gateway_container_and_oauth_smoke_contract(self):
        import colab_stage_oauth_smoke
        host = object.__new__(upgrade.Host)
        host.plan, host.before = self.candidate(), {'unrelated': {'image': 'fixed', 'running': True}}
        host.static_before = {key: upgrade.tree_manifest(self.state)[key] for key in ('fixture-token', 'axxium-client.json')}
        host.command = lambda *args: b'one two three'
        with patch.object(upgrade.stage, 'STATE', self.state), \
             patch.object(upgrade.stage, 'run', return_value=json.dumps(self.records()).encode()), \
             patch.object(upgrade.stage, 'inventory', return_value=host.before), \
             patch.object(upgrade.stage, 'protocol_checks') as protocol, \
             patch.object(colab_stage_oauth_smoke, 'smoke', return_value=self.smoke()) as smoke:
            self.assertEqual(host.verify()['assertions'], 165)
            protocol.assert_called_once()
            smoke.assert_called_once()

    def test_failed_revocation_or_google_profile_claim_cannot_supply_new_stage_receipt(self):
        import colab_stage_oauth_smoke
        host = object.__new__(upgrade.Host)
        host.plan, host.before = self.candidate(), {}
        host.command = lambda *args: b'one two three'
        for key, value in [('session_revoked', False), ('synthetic_actor', False), ('google_execution_verified', True)]:
            with self.subTest(key=key), patch.object(upgrade.stage, 'run', return_value=json.dumps(self.records()).encode()), \
                 patch.object(upgrade.stage, 'protocol_checks'), patch.object(upgrade.stage, 'inventory', return_value={}), \
                 patch.object(colab_stage_oauth_smoke, 'smoke', return_value={**self.smoke(), key: value}):
                with self.assertRaises(upgrade.stage.Refused): host.verify()

    def test_atomic_new_receipt_keeps_old_bytes_and_clears_hold_only_after_durable_publish(self):
        host = object.__new__(upgrade.Host)
        host.plan, host.controller = self.candidate(), 'd' * 40
        host.checkout, host.transaction = self.root / 'controller', self.root / 'transaction'
        host.transaction.mkdir(mode=0o700)
        plan = host.checkout / upgrade.PLAN_PATH
        plan.parent.mkdir(parents=True)
        plan.write_text('synthetic-plan')
        plan.chmod(0o600)
        host.compose = self.root / 'compose'
        host.compose.write_text('synthetic-compose')
        host.compose.chmod(0o600)
        host.old_receipt = self.old_receipt()
        (self.root / 'stage-receipt.json').write_bytes(host.old_receipt)
        (self.root / 'stage-receipt.json').chmod(0o600)
        (host.transaction / 'previous-receipt.json').write_bytes(host.old_receipt)
        host.pending = self.root / '.pending'
        host.pending.write_text('synthetic-hold')
        events = []
        host.record = lambda *args: events.append((host.pending.exists(), args))
        with patch.object(upgrade.stage, 'RUNTIME', self.root): result = host.publish(self.smoke())
        self.assertEqual(events, [(True, ('publish', 'healthy'))])
        self.assertFalse(host.pending.exists())
        self.assertEqual((host.transaction / 'previous-receipt.json').read_bytes(), host.old_receipt)
        self.assertEqual((self.root / 'stage-receipt.json').read_bytes(), (host.transaction / 'healthy-receipt.json').read_bytes())
        self.assertEqual(result['source_sha'], self.candidate()['source_sha'])
        self.assertFalse(result['production_qualified'])


if __name__ == '__main__':
    unittest.main()
