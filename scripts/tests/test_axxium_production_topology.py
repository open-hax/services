"""Production Axxium topology must preserve the existing PDS and database."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / 'digitalocean/services/axxium'
EXISTING = ROOT / 'scripts/tests/fixtures/axxium-production-existing-20261028.compose.yaml'

class AxxiumProductionTopologyTest(unittest.TestCase):
    def render(self, files):
        values = {
            'PATH': os.environ['PATH'], 'AXXIUM_IMAGE': 'fixture-axxium:1',
            'AXXIUM_STATE_PATH': '/private/fixture', 'AXXIUM_PUBLIC_HOST': 'axxium.promethean.rest',
            'AXXIUM_DB_PASSWORD': 'fixture-db', 'AXXIUM_JWT_SECRET': 'fixture-jwt',
            'AXXIUM_BOOTSTRAP_ADMIN_EMAIL': 'operator@example.test',
            'PDS_JWT_SECRET': 'fixture-pds', 'PDS_ADMIN_PASSWORD': 'fixture-admin',
            'PDS_PLC_ROTATION_KEY_K256_PRIVATE_KEY_HEX': 'fixture-key',
        }
        cmd = ['docker', 'compose', '--project-name', 'axxium-fixture', '--project-directory', str(SERVICE)]
        for file in files:
            cmd.extend(['-f', str(file)])
        result = subprocess.run(cmd + ['config', '--format', 'json'], env=values, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_reused_topology_preserves_live_database_pds_and_networks(self):
        old = self.render([EXISTING])
        new = self.render([SERVICE / 'compose.yaml'])
        for key in ('axxium-db', 'pds'):
            self.assertEqual(old['services'][key], new['services'][key])
        self.assertEqual(old['networks'], new['networks'])
        app = new['services']['axxium']
        self.assertEqual(app.pop('user'), '1000:1000')
        self.assertEqual(app['environment'].pop('AXXIUM_TRUST_PROXY'), '')
        self.assertEqual(app['environment'].pop('AXXIUM_OIDC_CONFIG_FILE'), '')
        self.assertEqual(old['services']['axxium'], app)

    def test_oidc_overlay_is_read_only_and_preserves_other_services(self):
        # Some Compose versions omit false-valued bind fields from JSON. Check
        # the authored safety contract directly as well as the resolved mount;
        # an omitted JSON field alone is not proof of host enforcement.
        source = yaml.safe_load((SERVICE / 'axxium-oidc.compose.yaml').read_text())
        source_mounts = [v for v in source['services']['axxium']['volumes']
                         if v['target'] == '/run/axxium/oidc']
        self.assertEqual(len(source_mounts), 1)
        self.assertIs(source_mounts[0]['bind']['create_host_path'], False)
        base = self.render([SERVICE / 'compose.yaml'])
        overlay = self.render([SERVICE / 'compose.yaml', SERVICE / 'axxium-oidc.compose.yaml'])
        self.assertEqual(base['services']['axxium-db'], overlay['services']['axxium-db'])
        self.assertEqual(base['services']['pds'], overlay['services']['pds'])
        app = overlay['services']['axxium']
        self.assertEqual(app['environment']['AXXIUM_OIDC_CONFIG_FILE'], '/run/axxium/oidc/authority.json')
        mounts = [v for v in app['volumes'] if v['target'] == '/run/axxium/oidc']
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0]['read_only'])
        self.assertIs(mounts[0]['bind'].get('create_host_path', False), False)

    def test_oidc_verification_requires_issuer_and_s256(self):
        with tempfile.TemporaryDirectory() as directory:
            curl = Path(directory) / 'curl'
            curl.write_text('''#!/usr/bin/env python3
import json, os, sys
url = sys.argv[-1]
if url.endswith('/health'): out={'status':'ok'}
elif url.endswith('/api/auth/atproto/client-metadata.json'): out={'client_id':'https://axxium.promethean.rest/api/auth/atproto/client-metadata.json','redirect_uris':['https://axxium.promethean.rest/api/auth/atproto/callback']}
elif url.endswith('/xrpc/_health'): out={'version':'test'}
elif url.endswith('/xrpc/com.atproto.server.describeServer'): out={'did':'did:web:axxium.promethean.rest'}
elif url.endswith('/oidc/.well-known/openid-configuration'): out={'issuer':os.environ['TEST_ISSUER'],'code_challenge_methods_supported':os.environ['TEST_PKCE'].split(',')}
else: sys.exit(2)
print(json.dumps(out))
''')
            curl.chmod(0o755)
            env = {**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH'],
                   'AXXIUM_PUBLIC_HOST': 'axxium.promethean.rest',
                   'AXXIUM_OIDC_CONFIG_FILE': '/run/axxium/oidc/authority.json',
                   'TEST_ISSUER': 'https://axxium.promethean.rest/oidc', 'TEST_PKCE': 'S256'}
            run = lambda: subprocess.run(['bash', str(SERVICE / 'verify.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            env['TEST_ISSUER'] = 'https://staging.axxium.promethean.rest/oidc'
            self.assertNotEqual(run().returncode, 0)
            env['TEST_ISSUER'] = 'https://axxium.promethean.rest/oidc'
            env['TEST_PKCE'] = 'plain'
            self.assertNotEqual(run().returncode, 0)

if __name__ == '__main__':
    unittest.main()
