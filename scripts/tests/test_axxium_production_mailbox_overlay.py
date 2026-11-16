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
import axxium_unified_mailbox_release as unified

BASE = ROOT / 'digitalocean/services/axxium/compose.yaml'
MAILBOX = BASE.with_name('mailbox-production.overlay.yaml')
INCUMBENT_IMAGE = 'sha256:' + '1' * 64
CANDIDATE_IMAGE = 'sha256:' + '2' * 64
STATE = Path('/fixture/state/axxium')
IDENTITY = STATE / 'operations/candidate/mailbox-identity'
OWNER = 'actor_00000000-0000-4000-8000-000000000001'
ROOT_DID = 'did:web:axxium.promethean.rest:actors:owner'
WORK_DID = 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa'
BINDINGS = f'[["{WORK_DID}" "{ROOT_DID}:work"]]'
OIDC_MOUNT = '/run/axxium/oidc'


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

    def render(self, *overlays, env=None):
        files = [arg for path in (BASE, self.retained, *overlays) for arg in ('-f', str(path))]
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
            'JWT_AUDIENCE': 'promethean+production',
            'AXXIUM_SIGNING_KEY_FILE': '/run/axxium/actor-signing.pem',
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
        app['environment']['JWT_AUDIENCE'] = previous['environment']['JWT_AUDIENCE']

        expected_mounts = {
            OIDC_MOUNT: (IDENTITY / 'oidc', True),
            '/run/axxium/actor-signing.pem': (IDENTITY / 'actor-signing.pem', True),
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
                                                   CANDIDATE_IMAGE, IDENTITY)
            self.assertEqual(app, selected)
            app['environment']['AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED'] = 'true'
            with self.assertRaisesRegex(unified.release.Refused, 'environment differs'):
                unified.validate_projection(base, candidate, CANDIDATE_IMAGE, IDENTITY)

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
