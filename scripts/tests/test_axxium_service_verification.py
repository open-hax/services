"""Credential-free command doubles exercise the real required shell gate."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / 'digitalocean/services/axxium'
ORIGIN = 'https://axxium.promethean.rest'
ISSUER = ORIGIN + '/oidc'
APP, DB, PDS, PROXY = 'axxium-axxium-1', 'axxium-axxium-db-1', 'axxium-pds-1', 'caddy-caddy-1'

MOCK = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
d=json.loads(Path(os.environ['VERIFY_FIXTURE']).read_text())
args=sys.argv[1:]
with Path(os.environ['VERIFY_CALLS']).open('a') as f:f.write(json.dumps([Path(sys.argv[0]).name]+args)+'\n')
if d.get('command_failure'):
    print('private-value-must-never-be-reported',file=sys.stderr);sys.exit(1)
if Path(sys.argv[0]).name=='docker':
    if args[:2]==['inspect','--format']:
        assert '.Config' not in args[2] and '.Env' not in args[2]
        value=d['containers'][args[3]]
        if d.get('runtime_drift') and args[3]=='axxium-axxium-1':
            n=Path(os.environ['VERIFY_COUNTER']);count=int(n.read_text()) if n.exists() else 0;n.write_text(str(count+1))
            if count:value={**value,'id':'b'*64}
        print(json.dumps(value))
    else:
        assert args[:9]==['exec','caddy-caddy-1','wget','-q','-O','-','-T','10','--header']
        assert args[9:] == ['X-Forwarded-Proto: https','--header','Host: axxium.promethean.rest','http://axxium:8787/oidc/.well-known/openid-configuration']
        print(json.dumps(d['private_discovery']))
else:
    assert '--location' not in args and '--insecure' not in args
    url=args[-1];value=d['http'][url]
    if '--request' in args:
        assert url==d['public_discovery']['introspection_endpoint']
        assert args[args.index('--request')+1]=='POST'
        from urllib.parse import parse_qs
        assert parse_qs(args[args.index('--data')+1])=={
          'client_id':['knoxx-colab'],'client_secret':['axxium-deployment-verification-invalid-secret'],
          'token':['axxium-deployment-verification-invalid-token']}
    print(json.dumps(value['body']) if isinstance(value['body'],dict) else value['body'])
    sys.stdout.write(str(value['status']))
'''


def fixture():
    plan = json.loads((SERVICE / 'minimal-production-plan.json').read_text())
    containers = {APP: {'id': 'a' * 64, 'image': plan['image_id'], 'running': True,
                        'health': 'healthy', 'proxy_ip': '172.18.0.5'}}
    for name, prefix in ((DB, 'db'), (PDS, 'pds'), (PROXY, 'caddy')):
        containers[name] = {'id': plan['expected'][prefix + '_container_id'],
                            'image': plan['expected'][prefix + '_image_id'], 'running': True,
                            'health': None if name == PROXY else 'healthy',
                            'proxy_ip': '172.18.0.9' if name == PROXY else None}
    discovery = {'issuer': ISSUER, 'code_challenge_methods_supported': ['S256'],
                 'response_types_supported': ['code'], 'grant_types_supported': ['authorization_code'],
                 'token_endpoint_auth_methods_supported': ['client_secret_post'],
                 'authorization_endpoint': ISSUER + '/auth', 'token_endpoint': ISSUER + '/token',
                 'jwks_uri': ISSUER + '/jwks', 'introspection_endpoint': ISSUER + '/token/introspection'}
    values = {}
    for origin in ('http://127.0.0.1:8787', ORIGIN):
        values[origin + '/health'] = {'status': 'ok'}
        values[origin + '/api/auth/atproto/client-metadata.json'] = {
            'client_id': ORIGIN + '/api/auth/atproto/client-metadata.json',
            'redirect_uris': [ORIGIN + '/api/auth/atproto/callback']}
    for origin in ('http://127.0.0.1:3000', ORIGIN):
        values[origin + '/xrpc/_health'] = {'version': '0.4.0'}
        values[origin + '/xrpc/com.atproto.server.describeServer'] = {'did': 'did:web:axxium.promethean.rest'}
    values[ISSUER + '/.well-known/openid-configuration'] = discovery
    values[ISSUER + '/jwks'] = {'keys': [{'kty': 'RSA', 'alg': 'RS256', 'kid': 'fixture', 'n': 'AQAB', 'e': 'AQAB'}]}
    http = {url: {'status': 200, 'body': body} for url, body in values.items()}
    http[discovery['introspection_endpoint']] = {'status': 401, 'body': 'Shared login request could not be completed'}
    return {'containers': containers, 'private_discovery': copy.deepcopy(discovery),
            'public_discovery': discovery, 'http': http}


class RequiredServiceVerificationTests(unittest.TestCase):
    def gate(self, data=None, mutate_plan=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('verify.sh', 'minimal-production-plan.json'):
                shutil.copyfile(SERVICE / name, root / name)
            if mutate_plan:
                p = json.loads((root / 'minimal-production-plan.json').read_text());mutate_plan(p)
                (root / 'minimal-production-plan.json').write_text(json.dumps(p))
            for name in ('docker', 'curl'):
                (root / name).write_text(MOCK);(root / name).chmod(0o755)
            (root / 'fixture.json').write_text(json.dumps(data or fixture()))
            calls = root / 'calls.jsonl'
            env = {'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'VERIFY_FIXTURE': str(root / 'fixture.json'), 'VERIFY_CALLS': str(calls),
                   'VERIFY_COUNTER': str(root / 'counter')}
            result = subprocess.run(['bash', str(root / 'verify.sh')], env=env, capture_output=True, text=True, timeout=20)
            observed = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result, observed

    def test_real_gate_uses_bound_proxy_and_no_private_configuration(self):
        result, calls = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('introspection denial verified', result.stdout)
        self.assertEqual(sum(c[:2] == ['docker', 'inspect'] for c in calls), 8)
        self.assertEqual(sum(c[:2] == ['docker', 'exec'] for c in calls), 1)
        self.assertNotIn('Config.Env', json.dumps(calls))
        self.assertFalse(any(c[1] in ('compose', 'run', 'stop', 'restart', 'cp') for c in calls if c[0] == 'docker'))

    def test_fixed_plan_refuses_a_different_image_before_commands(self):
        result, calls = self.gate(mutate_plan=lambda p: p.update(image_id='sha256:' + '0' * 64))
        self.assertNotEqual(result.returncode, 0);self.assertEqual(calls, [])

    def test_admitted_image_and_every_preserved_identity_are_required(self):
        for name, key, value in ((APP, 'image', 'sha256:' + '0' * 64), (PDS, 'id', '0' * 64),
                                 (DB, 'image', 'sha256:' + '0' * 64), (PROXY, 'id', '0' * 64),
                                 (PROXY, 'proxy_ip', '172.18.0.10'), (PDS, 'health', 'unhealthy')):
            with self.subTest(name=name, key=key):
                d = fixture();d['containers'][name][key] = value
                self.assertNotEqual(self.gate(d)[0].returncode, 0)

    def test_public_pds_failure_and_authority_drift_refuse(self):
        for url, body in ((ORIGIN + '/xrpc/_health', {'version': ''}),
                          (ORIGIN + '/xrpc/com.atproto.server.describeServer', {'did': 'did:web:other.invalid'}),
                          (ORIGIN + '/api/auth/atproto/client-metadata.json', {'client_id': 'other'})):
            with self.subTest(url=url):
                d = fixture();d['http'][url]['body'] = body
                self.assertNotEqual(self.gate(d)[0].returncode, 0)

    def test_oidc_issuer_pkce_and_endpoint_escape_refuse(self):
        for key, value in (('issuer', 'https://staging.axxium.promethean.rest/oidc'),
                           ('code_challenge_methods_supported', ['plain']),
                           ('introspection_endpoint', 'https://other.invalid/oidc/token/introspection')):
            with self.subTest(key=key):
                d = fixture();d['public_discovery'][key] = value;d['private_discovery'][key] = value
                self.assertNotEqual(self.gate(d)[0].returncode, 0)

    def test_private_and_public_discovery_must_match(self):
        d = fixture();d['private_discovery']['extra'] = 'drift'
        self.assertNotEqual(self.gate(d)[0].returncode, 0)

    def test_jwks_private_material_and_anonymous_introspection_success_refuse(self):
        d = fixture();d['http'][ISSUER + '/jwks']['body']['keys'][0]['d'] = 'private-test-marker'
        result, _ = self.gate(d);self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('private-test-marker', result.stdout + result.stderr)
        d = fixture();d['http'][ISSUER + '/token/introspection'] = {'status': 200, 'body': {'active': True}}
        self.assertNotEqual(self.gate(d)[0].returncode, 0)

    def test_runtime_drift_during_http_probes_refuses(self):
        d = fixture();d['runtime_drift'] = True
        self.assertNotEqual(self.gate(d)[0].returncode, 0)

    def test_failed_command_diagnostics_are_not_exposed(self):
        d = fixture();d['command_failure'] = True
        result, _ = self.gate(d)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('private-value-must-never-be-reported', result.stdout + result.stderr)

    def test_descriptor_and_base_preserve_actual_authorities(self):
        descriptor = yaml.safe_load((SERVICE / 'service.yaml').read_text())
        self.assertTrue(descriptor['verify']['required'])
        self.assertFalse(descriptor['deployment']['genericRenderer'])
        self.assertEqual(descriptor['deployment']['qualification'], 'environment_policy.production_admission')
        compose = yaml.safe_load((SERVICE / 'compose.yaml').read_text())
        self.assertEqual(set(compose['services']), {'axxium', 'axxium-db', 'pds'})
        self.assertEqual(compose['services']['pds']['ports'], ['127.0.0.1:3000:3000'])
        self.assertEqual(compose['services']['axxium']['ports'], ['127.0.0.1:8787:8787'])
        self.assertEqual(compose['services']['pds']['networks']['default']['aliases'], ['axxium-pds'])
        self.assertEqual(compose['networks']['database'], {'internal': True})
        self.assertEqual(compose['networks']['default'], {'name': 'open-hax', 'external': True})
        self.assertEqual(compose['services']['axxium-db']['volumes'], ['${AXXIUM_STATE_PATH}/postgres:/var/lib/postgresql/data'])
        self.assertEqual(compose['services']['pds']['volumes'], ['${AXXIUM_STATE_PATH}/pds:/pds'])
        self.assertNotIn('env_file', compose['services']['axxium'])


if __name__ == '__main__':
    unittest.main()
