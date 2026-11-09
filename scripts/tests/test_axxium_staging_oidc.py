"""Staging slot templates preserve the settings of their predecessors."""
import json
import os
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'digitalocean/environments/axxium.compose.yaml'
STAGING = ROOT / 'digitalocean/environments/axxium-staging-oidc.compose.yaml'
MAILBOX = ROOT / 'digitalocean/environments/axxium-staging-mailbox.compose.yaml'

class StagingAuthorityTemplateTest(unittest.TestCase):
    def render(self, path, *, trust=True, webhook=None):
        env = {'PATH': os.environ.get('PATH', os.defpath),
               'AXXIUM_IMAGE': 'promethean-axxium:fixture',
               'AXXIUM_PUBLIC_BASE_URL': 'https://staging.axxium.promethean.rest',
               'DB_PASSWORD': 'fixture-only-db', 'JWT_SECRET': 'fixture-only-session'}
        if trust:
            env['AXXIUM_TRUST_PROXY'] = '172.17.0.1'
        if webhook is not None:
            env.update(webhook)
        return subprocess.run(['docker', 'compose', '--project-name', 'staging-fixture',
                               '--project-directory', str(BASE.parent), '-f', str(path),
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
        defaults, configured = self.render(MAILBOX), self.render(MAILBOX, webhook=settings)
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
