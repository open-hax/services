"""Resolve the app-only mailbox delta against the incumbent Compose layers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import axxium_events_profile as events
import axxium_unified_mailbox_release as unified

BASE = ROOT / 'digitalocean/services/axxium/compose.yaml'
MAILBOX = BASE.with_name('mailbox-production.overlay.yaml')
EVENTS = BASE.with_name('mailbox-events-production.overlay.yaml')
INCUMBENT_IMAGE = 'sha256:' + '1' * 64
CANDIDATE_IMAGE = 'sha256:' + '2' * 64
STATE = Path('/fixture/state/axxium')
IDENTITY = STATE / 'operations/candidate/mailbox-identity'
AT_SIGNING = STATE / 'operations' / (unified.SOURCE + '-at-auth') / 'actor-signing.pem'
OWNER = 'actor_00000000-0000-4000-8000-000000000001'
ROOT_DID = 'did:web:axxium.promethean.rest:actors:owner'
WORK_DID = 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa'
BINDINGS = f'[["{WORK_DID}" "{ROOT_DID}:work"]]'
OIDC_MOUNT = '/run/axxium/oidc'


class MailboxRuntimeHealthTest(unittest.TestCase):
    def test_bare_docker_environment_entry_is_unset_and_explicit_values_are_preserved(self):
        plan = {'at_auth': {'overlay_path': '/fixture/at-auth.compose.json'}, 'image_id': CANDIDATE_IMAGE}
        host = unified.Host(plan, Path('/fixture/plan.json'), 'a' * 64)
        host.candidate_id = 'candidate'
        host.proxy_ip = '172.18.0.9'
        host.old_environment = {'INCUMBENT_EMPTY': '', 'INCUMBENT_VALUE': 'one=two'}
        optional = 'AXXIUM_MAILBOX_BOUND_BOOTSTRAP_BINDINGS'
        selected = {'Id': host.candidate_id, 'Image': CANDIDATE_IMAGE, 'Mounts': [], 'Config': {'Env': []}}
        proxy = {'NetworkSettings': {'Networks': {'open-hax': {'IPAddress': host.proxy_ip}}}}
        projection = {'environment': {optional: None}, 'volumes': []}
        oidc = {'issuer': unified.release.ISSUER, 'code_challenge_methods_supported': ['S256']}
        metadata = {'resource': unified.ORIGIN + unified.RESOURCE_PATH,
                    'authorization_servers': [unified.release.ISSUER],
                    'scopes_supported': ['mailbox:events', 'mailbox:actors', 'mailbox:inbox']}

        def probe(command):
            result = metadata if command[-1].endswith(unified.METADATA_PATH) else oidc
            return json.dumps(result).encode()

        with (mock.patch.object(host, 'wait_app'), mock.patch.object(host, 'unchanged_dependencies'),
              mock.patch.object(host, 'identity_check'), mock.patch.object(host, 'http', return_value=(503, b'')),
              mock.patch.object(host, 'mailbox_projection', return_value=projection),
              mock.patch.object(host, 'inspect', side_effect=lambda name: selected if name == unified.release.APP else proxy),
              mock.patch.object(host, 'command', side_effect=probe)):
            original = ['INCUMBENT_EMPTY=', 'INCUMBENT_VALUE=one=two', optional]
            selected['Config']['Env'] = original
            host.private_health(plan)

            for entries, reason in (
                (['INCUMBENT_EMPTY=', 'INCUMBENT_VALUE=one=two', optional + '='], 'Running mailbox environment differs'),
                (['INCUMBENT_EMPTY', 'INCUMBENT_VALUE=one=two', optional], 'Incumbent application setting was not preserved'),
                (['INCUMBENT_EMPTY=', 'INCUMBENT_VALUE=one', optional], 'Incumbent application setting was not preserved'),
            ):
                with self.subTest(entries=entries):
                    selected['Config']['Env'] = entries
                    with self.assertRaisesRegex(unified.release.Uncertain, reason):
                        host.private_health(plan)


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI unavailable')
class ProductionMailboxOverlayTest(unittest.TestCase):
    def setUp(self):
        self.env = {
            'PATH': os.environ.get('PATH', os.defpath),
            'AXXIUM_IMAGE': INCUMBENT_IMAGE,
            'AXXIUM_DB_PASSWORD': 'fixture-db',
            'AXXIUM_JWT_SECRET': 'fixture-session',
            'AXXIUM_STATE_PATH': str(STATE),
            'AXXIUM_PUBLIC_HOST': 'axxium.promethean.rest',
            'AXXIUM_BOOTSTRAP_ADMIN_EMAIL': 'fixture@example.test',
            'PDS_JWT_SECRET': 'fixture-pds',
            'PDS_ADMIN_PASSWORD': 'fixture-admin',
            'PDS_PLC_ROTATION_KEY_K256_PRIVATE_KEY_HEX': 'fixture-plc',
            'AXXIUM_PRODUCTION_MAILBOX_IMAGE': CANDIDATE_IMAGE,
            'AXXIUM_PRODUCTION_MAILBOX_IDENTITY_PATH': str(IDENTITY),
            'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': OWNER,
            'AXXIUM_IDENTITY_ROOT_DID': ROOT_DID,
            'AXXIUM_MAILBOX_ACTOR_BINDINGS': BINDINGS,
            'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': WORK_DID,
        }
        scenario = tempfile.TemporaryDirectory()
        self.addCleanup(scenario.cleanup)
        self.retained = Path(scenario.name) / 'candidate.compose.json'
        # Shape produced by NativeHost.start_candidate for the incumbent.
        self.retained.write_text(json.dumps({'services': {'axxium': {
            'image': INCUMBENT_IMAGE,
            'stop_grace_period': '610s',
            'environment': {'AXXIUM_TRUST_PROXY': '172.18.0.9',
                            'AXXIUM_OIDC_CONFIG_FILE': f'{OIDC_MOUNT}/authority.json'},
            'volumes': [{'type': 'bind', 'source': str(STATE / 'oidc'), 'target': OIDC_MOUNT,
                         'read_only': True, 'bind': {'create_host_path': False}}],
        }}}))
        self.at_auth = self.retained.with_name('at-auth.compose.json')
        self.at_auth.write_text(json.dumps({'services': {'axxium': {
            'image': CANDIDATE_IMAGE,
            'environment': {'JWT_AUDIENCE': 'promethean+production',
                            'AXXIUM_SIGNING_KEY_FILE': unified.SIGNING_TARGET},
            'volumes': [{'type': 'bind', 'source': str(AT_SIGNING), 'target': unified.SIGNING_TARGET,
                         'read_only': True, 'bind': {'create_host_path': False}}],
        }}}))

    def render(self, *overlays, env=None):
        files = [arg for path in (BASE, self.retained, self.at_auth, *overlays) for arg in ('-f', str(path))]
        return subprocess.run(['docker', 'compose', '--project-name', 'axxium', *files,
                               'config', '--format', 'json'], env=self.env if env is None else env,
                              text=True, capture_output=True, check=False)

    def test_changes_only_the_admitted_image_mailbox_environment_and_mounts(self):
        base_result, result = self.render(), self.render(MAILBOX)
        self.assertEqual(base_result.returncode, 0, base_result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        base, candidate = json.loads(base_result.stdout), json.loads(result.stdout)
        previous = base['services']['axxium']
        app = candidate['services']['axxium']
        self.assertEqual(app['image'], CANDIDATE_IMAGE)
        app['image'] = previous['image']

        expected_env = {
            'AXXIUM_GITHUB_IDENTITY_KEY_FILE': '/run/axxium/owner/identity.key',
            'AXXIUM_GITHUB_IDENTITY_LEDGER_FILE': '/var/lib/axxium/identity/ledger.edn',
            'AXXIUM_IDENTITY_SEED_FILE': '/run/axxium/owner/foresight-tree.edn',
            'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': OWNER,
            'AXXIUM_IDENTITY_ROOT_DID': ROOT_DID,
            'AXXIUM_MAILBOX_LEDGER_FILE': '/var/lib/axxium/mailbox/ledger.edn',
            'AXXIUM_MAILBOX_DIRECTORY': 'true',
            'AXXIUM_MAILBOX_ACTOR_BINDINGS': BINDINGS,
            'AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS': None,
            'AXXIUM_MAILBOX_BOUND_BOOTSTRAP_BINDINGS': None,
            'AXXIUM_MAILBOX_STREAM_ENABLED': 'true',
            'AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED': 'false',
            'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': WORK_DID,
            'AXXIUM_MAILBOX_WEBHOOK_KEY_FILE': f'{OIDC_MOUNT}/mailbox-webhook.key',
        }
        for key, value in expected_env.items():
            self.assertEqual(app['environment'].pop(key, None), value, key)

        expected_mounts = {
            OIDC_MOUNT: (IDENTITY / 'oidc', True),
            '/run/axxium/owner/identity.key': (IDENTITY / 'owner/identity.key', True),
            '/run/axxium/owner/foresight-tree.edn': (IDENTITY / 'owner/foresight-tree.edn', True),
            '/var/lib/axxium/identity/ledger.edn': (STATE / 'identity/ledger.edn', False),
            '/var/lib/axxium/mailbox/ledger.edn': (STATE / 'mailbox/ledger.edn', False),
        }
        declared = yaml.safe_load(MAILBOX.read_text())
        self.assertEqual(set(declared), {'services'})
        self.assertEqual(set(declared['services']), {'axxium'})
        binds = declared['services']['axxium']['volumes']
        self.assertEqual({v['target'] for v in binds}, set(expected_mounts))
        self.assertTrue(all(v['bind']['create_host_path'] is False for v in binds))
        for target, (source, read_only) in expected_mounts.items():
            with self.subTest(target=target):
                [mount] = [v for v in app['volumes'] if v['target'] == target]
                self.assertEqual(mount['type'], 'bind')
                self.assertEqual(Path(mount['source']), source)
                self.assertIs(mount.get('read_only', False), read_only)
                self.assertFalse(mount.get('bind', {}).get('create_host_path', False))
                app['volumes'].remove(mount)
        [old_oidc] = [v for v in previous['volumes'] if v['target'] == OIDC_MOUNT]
        app['volumes'].append(old_oidc)
        for service in (app, previous):
            service['volumes'].sort(key=lambda volume: volume['target'])
        self.assertEqual(candidate, base)

    def test_production_controller_checks_complete_compose_delta(self):
        env = self.env | unified.PREFLIGHT_BINDING
        renders = [self.render(env=env), self.render(MAILBOX, env=env)]
        for result in renders:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('AXXIUM_MAILBOX_BOUND_BOOTSTRAP_BINDINGS', env)
        base, candidate = (json.loads(result.stdout) for result in renders)
        app = candidate['services']['axxium']
        self.assertIsNone(app['environment']['AXXIUM_MAILBOX_BOUND_BOOTSTRAP_BINDINGS'])
        with mock.patch.object(unified.release, 'STATE', STATE):
            selected = unified.validate_projection(json.loads(renders[0].stdout),
                                                   json.loads(renders[1].stdout),
                                                   CANDIDATE_IMAGE, IDENTITY, AT_SIGNING)
            self.assertEqual(app, selected)
            self.assertEqual(next(row['source'] for row in selected['volumes']
                                  if row['target'] == unified.SIGNING_TARGET), str(AT_SIGNING))
            signing = next(row for row in app['volumes'] if row['target'] == unified.SIGNING_TARGET)
            signing['source'] = str(IDENTITY / 'rotated-signing.pem')
            with self.assertRaisesRegex(unified.release.Refused, 'incumbent application settings'):
                unified.validate_projection(json.loads(renders[0].stdout), json.loads(json.dumps(candidate)),
                                            CANDIDATE_IMAGE, IDENTITY, AT_SIGNING)
            signing['source'] = str(AT_SIGNING)
            app['environment']['AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED'] = 'true'
            with self.assertRaisesRegex(unified.release.Refused, 'environment differs'):
                unified.validate_projection(base, candidate, CANDIDATE_IMAGE, IDENTITY, AT_SIGNING)

    def test_same_image_rehearsal_uses_explicit_candidate_and_incumbent_profiles(self):
        with mock.patch.object(unified.release, 'STATE', STATE):
            host = unified.Host({'at_auth': {'overlay_path': str(self.at_auth)}}, Path('/plan'), 'a' * 64)
        plan = {'image_id': CANDIDATE_IMAGE, 'expected': {'app_image_id': CANDIDATE_IMAGE}}
        host.backups = {'before': {'database': 'rehearsal_fixture'}}
        with (mock.patch.object(host, 'probe') as probe, mock.patch.object(host, 'command'),
              mock.patch.object(host, 'private_write')):
            host.operation = IDENTITY.parent
            host.rehearse({**plan, 'source_sha': unified.SOURCE}, 'before', candidate=True)
        self.assertEqual([call.args[2] for call in probe.call_args_list], [CANDIDATE_IMAGE, CANDIDATE_IMAGE])
        self.assertEqual([call.kwargs['candidate'] for call in probe.call_args_list], [True, False])
        values, mounts = host.probe_profile(plan, CANDIDATE_IMAGE, 'fixture', {}, candidate=False)
        self.assertEqual(values, {})
        self.assertIn(f'type=bind,src={AT_SIGNING},dst={unified.SIGNING_TARGET},readonly', mounts)
        self.assertFalse(any(str(IDENTITY) in mount for mount in mounts))

    def test_same_image_events_overlay_preserves_the_active_compose_chain(self):
        active = self.retained.with_name('active-mailbox.compose.json')
        active_image = 'sha256:' + '3' * 64
        active.write_text(json.dumps({'services': {'axxium': {'image': active_image}}}))
        incumbent = self.render(MAILBOX, active)
        candidate = self.render(MAILBOX, active, EVENTS)
        for result in (incumbent, candidate):
            self.assertEqual(result.returncode, 0, result.stderr)
        events.validate_projection(
            json.loads(incumbent.stdout),
            json.loads(candidate.stdout),
            image_id=active_image,
            recipient_did=WORK_DID,
        )

    def test_refuses_missing_required_inputs_and_keeps_provided_bootstrap_bindings(self):
        for key in ('AXXIUM_PRODUCTION_MAILBOX_IMAGE', 'AXXIUM_PRODUCTION_MAILBOX_IDENTITY_PATH',
                    'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID', 'AXXIUM_IDENTITY_ROOT_DID',
                    'AXXIUM_MAILBOX_ACTOR_BINDINGS', 'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID'):
            with self.subTest(key=key):
                env = self.env.copy()
                del env[key]
                result = self.render(MAILBOX, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)

        optional = {
            'AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS': f'{ROOT_DID}:codex',
            'AXXIUM_MAILBOX_BOUND_BOOTSTRAP_BINDINGS':
                f'[["did:plc:bbbbbbbbbbbbbbbbbbbbbbbb" "{ROOT_DID}:codex-bound"]]',
        }
        result = self.render(MAILBOX, env=self.env | optional)
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = json.loads(result.stdout)['services']['axxium']['environment']
        self.assertEqual({key: resolved[key] for key in optional}, optional)


if __name__ == '__main__':
    unittest.main()
