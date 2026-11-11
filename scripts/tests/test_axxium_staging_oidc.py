"""Staging slot templates preserve the settings of their predecessors."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'digitalocean/environments/axxium.compose.yaml'
STAGING = ROOT / 'digitalocean/environments/axxium-staging-oidc.compose.yaml'
MAILBOX = ROOT / 'digitalocean/environments/axxium-staging-mailbox.compose.yaml'
GOOGLE = ROOT / 'digitalocean/environments/axxium-staging-google.compose.yaml'
OWNER = ROOT / 'digitalocean/environments/axxium-staging-owner.compose.yaml'
CANONICAL = ROOT / 'digitalocean/environments/axxium-staging-canonical.compose.yaml'
ROOT_ENV = {
    'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': 'actor_00000000-0000-4000-8000-000000000001',
    'AXXIUM_IDENTITY_ROOT_DID': 'did:web:staging.axxium.promethean.rest:actors:owner',
}
OWNER_ENV = {
    **ROOT_ENV,
    'AXXIUM_MAILBOX_ACTOR_BINDINGS': (
        '[["did:plc:aaaaaaaaaaaaaaaaaaaaaaaa" "did:web:staging.axxium.promethean.rest:actors:owner:work"]]'
    ),
    'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa',
}

class StagingAuthorityTemplateTest(unittest.TestCase):
    def render(self, path, *, trust=True, settings=None, project_directory=BASE.parent):
        env = {'PATH': os.environ.get('PATH', os.defpath),
               'AXXIUM_IMAGE': 'promethean-axxium:fixture',
               'AXXIUM_PUBLIC_BASE_URL': 'https://staging.axxium.promethean.rest',
               'DB_PASSWORD': 'fixture-only-db', 'JWT_SECRET': 'fixture-only-session'}
        if trust:
            env['AXXIUM_TRUST_PROXY'] = '172.17.0.1'
        if settings is not None:
            env.update(settings)
        return subprocess.run(['docker', 'compose', '--project-name', 'staging-fixture',
                               '--project-directory', str(project_directory), '-f', str(path),
                               'config', '--format', 'json'], env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_only_authority_config_and_read_only_directory_differ(self):
        base_result, staging_result = self.render(BASE), self.render(STAGING)
        self.assertEqual(base_result.returncode, 0, base_result.stderr)
        self.assertEqual(staging_result.returncode, 0, staging_result.stderr)
        base, staging = json.loads(base_result.stdout), json.loads(staging_result.stdout)
        app = staging['services']['axxium']
        self.assertEqual(app['environment'].pop('AXXIUM_OIDC_CONFIG_FILE'), '/run/axxium/oidc/authority.json')
        self.assertEqual(app['environment'].pop('AXXIUM_TRUST_PROXY'), '172.17.0.1')
        mount = [v for v in app['volumes'] if v['target'] == '/run/axxium/oidc']
        self.assertEqual(len(mount), 1)
        self.assertTrue(mount[0]['read_only'])
        # Recent Compose versions omit this field when its value is false.
        source = yaml.safe_load(STAGING.read_text())
        source_mount = next(v for v in source['services']['axxium']['volumes']
                            if isinstance(v, dict) and v['target'] == '/run/axxium/oidc')
        self.assertIs(source_mount['bind']['create_host_path'], False)
        self.assertFalse(mount[0]['bind'].get('create_host_path', False))
        app['volumes'].remove(mount[0])
        self.assertEqual(staging, base)

    def test_mailbox_changes_only_actor_signing_ledger_and_optional_webhook_settings(self):
        staging_result, mailbox_result = self.render(STAGING), self.render(MAILBOX)
        self.assertEqual(staging_result.returncode, 0, staging_result.stderr)
        self.assertEqual(mailbox_result.returncode, 0, mailbox_result.stderr)
        staging, mailbox = json.loads(staging_result.stdout), json.loads(mailbox_result.stdout)
        app = mailbox['services']['axxium']
        expected_env = {
            'AXXIUM_SERVICE': 'promethean',
            'AXXIUM_ENV': 'staging',
            'AXXIUM_ISS': 'https://staging.axxium.promethean.rest',
            'AXXIUM_SIGNING_KEY_FILE': '/run/axxium/actor-signing.pem',
            'AXXIUM_MAILBOX_LEDGER_FILE': '/var/lib/axxium/mailbox/ledger.edn',
            'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': '',
            'AXXIUM_MAILBOX_WEBHOOK_KEY_FILE': '',
        }
        for key, value in expected_env.items():
            self.assertEqual(app['environment'].pop(key), value)

        # Compose can omit false flags in normalized output. Check the explicit
        # source declarations as well, so missing files cannot create host paths.
        source = yaml.safe_load(MAILBOX.read_text())
        source_volumes = source['services']['axxium']['volumes']
        expected_mounts = [
            ('./actor-signing.pem', '/run/axxium/actor-signing.pem', True),
            ('./mailbox/ledger.edn', '/var/lib/axxium/mailbox/ledger.edn', False),
        ]
        for relative, target, read_only in expected_mounts:
            with self.subTest(target=target):
                [mount] = [v for v in app['volumes'] if v['target'] == target]
                self.assertEqual(mount['type'], 'bind')
                self.assertEqual(Path(mount['source']), BASE.parent / relative)
                self.assertIs(mount.get('read_only', False), read_only)
                self.assertFalse(mount['bind'].get('create_host_path', False))
                [declared] = [v for v in source_volumes if isinstance(v, dict) and v['target'] == target]
                self.assertEqual(declared['source'], relative)
                self.assertIs(declared['bind']['create_host_path'], False)
                app['volumes'].remove(mount)

        # Equality also keeps the old image's JWT inputs, OIDC authority,
        # transfer identity, trust mounts, resource limits and database intact.
        self.assertEqual(mailbox, staging)

    def test_mailbox_webhook_uses_existing_read_only_oidc_mount(self):
        settings = {
            'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa',
            'AXXIUM_MAILBOX_WEBHOOK_KEY_FILE': '/run/axxium/oidc/mailbox-webhook.key',
        }
        defaults, configured = self.render(MAILBOX), self.render(MAILBOX, settings=settings)
        self.assertEqual(defaults.returncode, 0, defaults.stderr)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        expected, actual = json.loads(defaults.stdout), json.loads(configured.stdout)
        app = expected['services']['axxium']
        app['environment'].update(settings)
        self.assertEqual(actual, expected)
        [mount] = [v for v in app['volumes'] if v['target'] == '/run/axxium/oidc']
        self.assertEqual(Path(mount['source']), BASE.parent / 'oidc')
        self.assertTrue(mount['read_only'])
        self.assertFalse(mount['bind'].get('create_host_path', False))

    def assert_mount(self, template, app, relative, target, *, read_only):
        [mount] = [volume for volume in app['volumes'] if volume['target'] == target]
        self.assertEqual(mount['type'], 'bind')
        self.assertEqual(Path(mount['source']), BASE.parent / relative)
        self.assertIs(mount.get('read_only', False), read_only)
        self.assertFalse(mount['bind'].get('create_host_path', False))
        source_volumes = yaml.safe_load(template.read_text())['services']['axxium']['volumes']
        [declared] = [volume for volume in source_volumes if volume['target'] == target]
        self.assertIs(declared['bind']['create_host_path'], False)
        app['volumes'].remove(mount)

    def test_google_bootstrap_preserves_existing_mailbox_slot(self):
        base_result, google_result = self.render(MAILBOX), self.render(GOOGLE)
        self.assertEqual(base_result.returncode, 0, base_result.stderr)
        self.assertEqual(google_result.returncode, 0, google_result.stderr)
        base, google = json.loads(base_result.stdout), json.loads(google_result.stdout)
        app = google['services']['axxium']
        google_client = '/run/axxium/owner/google-client.json'
        self.assertEqual(app['environment'].pop('GOOGLE_OAUTH_CLIENT_FILE'), google_client)
        self.assert_mount(GOOGLE, app, 'owner/google-client.json', google_client, read_only=True)
        self.assertEqual(google, base)

    def test_canonical_preserves_existing_db_issuer_and_mailbox(self):
        google_result = self.render(GOOGLE)
        owner_result = self.render(OWNER, settings=ROOT_ENV)
        canonical_result = self.render(CANONICAL, settings=OWNER_ENV)
        self.assertEqual(google_result.returncode, 0, google_result.stderr)
        self.assertEqual(owner_result.returncode, 0, owner_result.stderr)
        self.assertEqual(canonical_result.returncode, 0, canonical_result.stderr)
        google = json.loads(google_result.stdout)
        owner = json.loads(owner_result.stdout)
        canonical = json.loads(canonical_result.stdout)
        environment = canonical['services']['axxium']['environment']
        self.assertEqual(environment.pop('AXXIUM_MAILBOX_ACTOR_BINDINGS'), OWNER_ENV['AXXIUM_MAILBOX_ACTOR_BINDINGS'])
        self.assertEqual(environment.pop('AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS'), '')
        self.assertEqual(environment.pop('AXXIUM_MAILBOX_STREAM_ENABLED'), 'false')
        self.assertEqual(environment.pop('AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED'), 'false')
        self.assertEqual(environment['AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID'], OWNER_ENV['AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID'])
        self.assertEqual(environment['AXXIUM_MAILBOX_WEBHOOK_KEY_FILE'], '/run/axxium/oidc/mailbox-webhook.key')
        owner_env = owner['services']['axxium']['environment']
        for key in ('AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID', 'AXXIUM_MAILBOX_WEBHOOK_KEY_FILE'):
            environment[key] = owner_env[key]
        self.assertEqual(canonical, owner)

        app = owner['services']['axxium']
        identity_key = '/run/axxium/owner/identity.key'
        seed = '/run/axxium/owner/foresight-tree.edn'
        identity_ledger = '/var/lib/axxium/identity/ledger.edn'
        self.assertEqual(app['environment'].pop('AXXIUM_GITHUB_IDENTITY_KEY_FILE'), identity_key)
        self.assertEqual(app['environment'].pop('AXXIUM_IDENTITY_SEED_FILE'), seed)
        self.assertEqual(app['environment'].pop('AXXIUM_GITHUB_IDENTITY_LEDGER_FILE'), identity_ledger)
        for key, value in ROOT_ENV.items():
            self.assertEqual(app['environment'].pop(key), value)
        self.assert_mount(OWNER, app, 'owner/identity.key', identity_key, read_only=True)
        self.assert_mount(OWNER, app, 'owner/foresight-tree.edn', seed, read_only=True)
        self.assert_mount(OWNER, app, 'identity/ledger.edn', identity_ledger, read_only=False)
        self.assertEqual(owner, google)

    def test_canonical_forwards_explicit_stream_and_bootstrap_opt_ins(self):
        settings = {
            'AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS': (
                'did:web:fixture.test:actors:owner:codex,did:web:fixture.test:actors:owner:bot'
            ),
            'AXXIUM_MAILBOX_STREAM_ENABLED': 'true',
        }
        defaults = self.render(CANONICAL, settings=OWNER_ENV)
        configured = self.render(CANONICAL, settings={**OWNER_ENV, **settings})
        owner = self.render(OWNER, settings={**ROOT_ENV, **settings})
        for result in (defaults, configured, owner):
            self.assertEqual(result.returncode, 0, result.stderr)
        expected = json.loads(defaults.stdout)
        expected['services']['axxium']['environment'].update(settings)
        self.assertEqual(json.loads(configured.stdout), expected)
        self.assertTrue(settings.keys().isdisjoint(json.loads(owner.stdout)['services']['axxium']['environment']))

    def test_canonical_refuses_incomplete_owner_or_mailbox_binding(self):
        for required in OWNER_ENV:
            with self.subTest(required=required):
                settings = {**OWNER_ENV, required: ''}
                refused = self.render(CANONICAL, settings=settings)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(required, refused.stderr)

    def test_receiver_entrypoint_resolves_reviewed_profiles_in_the_fixed_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            slot = Path(directory)
            for dependency in (MAILBOX, GOOGLE, OWNER):
                shutil.copyfile(dependency, slot / dependency.name)
            selected = slot / 'compose.yaml'
            shutil.copyfile(CANONICAL, selected)
            result = self.render(selected, settings=OWNER_ENV, project_directory=slot)
            self.assertEqual(result.returncode, 0, result.stderr)
            project = json.loads(result.stdout)
            self.assertEqual(set(project['services']), {'axxium', 'database'})
            mounts = project['services']['axxium']['volumes']
            for mount in mounts:
                self.assertTrue(Path(mount['source']).is_relative_to(slot))
            self.assertEqual(set(project['volumes']), {'postgres-data'})

    def test_missing_proxy_refuses_render(self):
        for path in (STAGING, MAILBOX):
            with self.subTest(template=path.name):
                self.assertNotEqual(self.render(path, trust=False).returncode, 0)

    def test_staging_templates_cover_maximum_startup_shutdown(self):
        for path in (BASE, STAGING, MAILBOX):
            with self.subTest(template=path.name):
                result = self.render(path)
                self.assertEqual(result.returncode, 0, result.stderr)
                services = json.loads(result.stdout)['services']
                self.assertEqual(services['axxium']['stop_grace_period'], '10m10s')
                self.assertNotIn('stop_grace_period', services['database'])

if __name__ == '__main__':
    unittest.main()
