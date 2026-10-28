"""Staging authority template preserves all non-OIDC Compose settings."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'digitalocean/environments/axxium.compose.yaml'
STAGING = ROOT / 'digitalocean/environments/axxium-staging-oidc.compose.yaml'

class StagingAuthorityTemplateTest(unittest.TestCase):
    def render(self, path, *, trust=True):
        env = {'PATH': os.environ.get('PATH', os.defpath),
               'AXXIUM_IMAGE': 'promethean-axxium:fixture',
               'AXXIUM_PUBLIC_BASE_URL': 'https://staging.axxium.promethean.rest',
               'DB_PASSWORD': 'fixture-only-db', 'JWT_SECRET': 'fixture-only-session'}
        if trust:
            env['AXXIUM_TRUST_PROXY'] = '172.17.0.1'
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
        self.assertFalse(mount[0]['bind']['create_host_path'])
        app['volumes'].remove(mount[0])
        self.assertEqual(staging, base)

    def test_missing_proxy_refuses_render(self):
        self.assertNotEqual(self.render(STAGING, trust=False).returncode, 0)

if __name__ == '__main__':
    unittest.main()
