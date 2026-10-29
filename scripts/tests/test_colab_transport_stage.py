"""The fixed Stage can neither select production nor hide a failed verification."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_transport_stage as m

ROOT = Path(__file__).resolve().parents[2]


class FakeHost:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def step(self, name):
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError('uncertain effect')

    @contextmanager
    def lock(self):
        self.step('lock')
        yield

    def preflight(self): self.step('preflight')
    def image(self): self.step('image')
    def claim(self): self.step('claim')
    def start(self): self.step('start')
    def verify(self):
        self.step('verify')
        return {'passed': True}
    def publish(self, proof):
        self.step('publish')
        return proof


class StageTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:' + '2' * 64,
                     'image_id': 'sha256:' + '3' * 64, 'source_sha': '4' * 40}

    def client(self):
        return {'issuer': m.ISSUER, 'client_id': 'knoxx-colab-transport-staging', 'client_secret': 's' * 48,
                'allowed_subjects': [m.ACTOR],
                'allowed_client_redirect_uris': ['http://127.0.0.1:18776/callback']}

    def test_fixed_client_needs_exact_existing_stage_actor_and_callback(self):
        m.validate_client(json.dumps(self.client()))
        for key, value in [('issuer', 'https://axxium.promethean.rest/oidc'),
                           ('client_id', 'knoxx-colab'), ('allowed_subjects', ['actor-new']),
                           ('allowed_client_redirect_uris', ['https://chatgpt.com/callback']),
                           ('client_secret', 'short'), ('override', True)]:
            with self.subTest(key=key), self.assertRaises(m.Refused):
                m.validate_client(json.dumps({**self.client(), key: value}))

    def test_private_input_rejects_symlink_or_exposed_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'client'
            path.write_text('private-fixture')
            path.chmod(0o600)
            with patch.object(m.os, 'getuid', return_value=1000):
                if path.stat().st_uid == 1000:
                    self.assertEqual(m.private_read(path), b'private-fixture')
                path.chmod(0o644)
                with self.assertRaises(m.Refused):
                    m.private_read(path)
                link = Path(root) / 'link'
                link.symlink_to(path)
                with self.assertRaises(OSError):
                    m.private_read(link)

    def test_actual_order_admits_image_before_effects_and_proof_before_receipt(self):
        host = FakeHost()
        self.assertEqual(m.execute(host), {'passed': True})
        self.assertEqual(host.calls, ['lock', 'preflight', 'image', 'claim', 'start', 'verify', 'publish'])

    def test_uncertainty_or_failed_verify_never_publishes_or_retries(self):
        for stage in ('preflight', 'image', 'claim', 'start', 'verify'):
            with self.subTest(stage=stage):
                host = FakeHost(stage)
                with self.assertRaises(RuntimeError):
                    m.execute(host)
                self.assertNotIn('publish', host.calls)
                self.assertEqual(host.calls.count(stage), 1)

    def records(self):
        records = []
        for role, mode, port in [('bridge-staging', 'transport-stage-bridge', '18773'),
                                 ('compute-staging', 'transport-stage-compute', '18775'),
                                 ('gateway-staging', 'gateway', '18771')]:
            mounts = [{'Type': 'bind', 'Source': str(m.STATE), 'Destination': '/run/colab-transport-stage', 'RW': False}]
            if role == 'gateway-staging':
                mounts.append({'Type': 'bind', 'Source': str(m.STATE / 'oauth'),
                               'Destination': '/var/lib/colab-transport-stage', 'RW': True})
            records.append({'Image': self.plan['image_id'], 'State': {'Running': True}, 'Mounts': mounts,
                'Config': {'Image': self.plan['image_reference'], 'User': '1000:1000',
                    'Cmd': ['knoxx-colab-mcp', mode, '--host', '127.0.0.1', '--port', port],
                    'Env': ['COLAB_AUTH_PROFILE=transport-staging'], 'Labels': {
                        'com.docker.compose.project': m.PROJECT, 'com.docker.compose.service': role,
                        'org.opencontainers.image.revision': self.plan['source_sha']}},
                'HostConfig': {'NetworkMode': 'host', 'ReadonlyRootfs': True, 'Memory': 268435456,
                               'NanoCpus': 500000000, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true']}})
        return records

    def test_all_three_actual_containers_must_match_source_and_isolation(self):
        m.validate_containers(self.records(), self.plan)
        for mutate in [lambda r: r.pop(), lambda r: r[0].update(Image='sha256:' + 'a' * 64),
                       lambda r: r[1]['Config'].update(User='root'),
                       lambda r: r[2]['Config'].update(Cmd=['knoxx-colab-mcp', 'gateway']),
                       lambda r: r[0]['HostConfig'].update(ReadonlyRootfs=False),
                       lambda r: r[1]['HostConfig'].update(Memory=0),
                       lambda r: r[0]['Mounts'][0].update(Source='/srv/open-hax/state/colab-mcp'),
                       lambda r: r[2]['Mounts'][0].update(RW=True)]:
            with self.subTest(mutate=mutate):
                records = self.records()
                mutate(records)
                with self.assertRaises(m.Refused):
                    m.validate_containers(records, self.plan)

    def test_selected_image_id_and_license_source_are_required(self):
        image = [{'Id': self.plan['image_id'], 'Os': 'linux', 'Architecture': 'amd64',
                  'Config': {'User': '1000:1000', 'Labels': {
                    'org.opencontainers.image.source': 'https://github.com/open-hax/cloud-cuckoo',
                    'org.opencontainers.image.revision': self.plan['source_sha'],
                    'org.opencontainers.image.licenses': 'GPL-3.0-or-later'}}}]
        m.inspect_image(image, self.plan)
        for key in ('org.opencontainers.image.source', 'org.opencontainers.image.revision', 'org.opencontainers.image.licenses'):
            with self.subTest(key=key):
                altered = copy.deepcopy(image)
                altered[0]['Config']['Labels'][key] = 'wrong'
                with self.assertRaises(m.Refused):
                    m.inspect_image(altered, self.plan)

    def test_unlabelled_unrelated_runtime_is_preserved_in_inventory(self):
        with patch.object(m, 'run', side_effect=[b'a\nb\n', json.dumps([
            {'Id': 'a', 'Image': 'image-a', 'State': {'Running': True}, 'Config': {'Labels': None}},
            {'Id': 'b', 'Image': 'image-b', 'State': {'Running': True},
             'Config': {'Labels': {'com.docker.compose.project': m.PROJECT}}}]).encode()]):
            self.assertEqual(m.inventory(), {'a': {'image': 'image-a', 'running': True}})

    def responses(self):
        return {m.BASE + '/health': (200, {'service': 'knoxx-colab-mcp', 'ready': True, 'identity_available': True}),
                m.BASE + '/.well-known/oauth-authorization-server': (200, {
                    'issuer': m.BASE, 'scopes_supported': ['openid', 'profile'],
                    'code_challenge_methods_supported': ['S256'],
                    **{k: m.BASE + v for k, v in {'authorization_endpoint': '/authorize', 'token_endpoint': '/token',
                       'registration_endpoint': '/register', 'revocation_endpoint': '/revoke'}.items()}}),
                m.BASE + '/.well-known/oauth-protected-resource/mcp': (200, {
                    'resource': m.BASE + '/mcp', 'authorization_servers': [m.BASE], 'scopes_supported': ['openid', 'profile']}),
                m.ISSUER + '/.well-known/openid-configuration': (200, {
                    'issuer': m.ISSUER, 'code_challenge_methods_supported': ['S256'],
                    'token_endpoint_auth_methods_supported': ['client_secret_post'],
                    **{k: m.ISSUER + '/' + k for k in ('authorization_endpoint', 'token_endpoint',
                        'introspection_endpoint', 'revocation_endpoint', 'jwks_uri')}}),
                **{f'http://127.0.0.1:{port}/mcp': (401, {}) for port in (18771, 18773, 18775)}}

    def protocol(self, responses):
        def request(url, **kwargs):
            status, body = responses[url]
            return status, json.dumps(body).encode()
        with patch.object(m, 'request', side_effect=request):
            m.protocol_checks()

    def test_pending_identity_discovery_drift_or_anonymous_success_fails(self):
        self.protocol(self.responses())
        for url, field, value in [(m.BASE + '/health', 'identity_available', False),
                                 (m.BASE + '/.well-known/oauth-authorization-server', 'scopes_supported', ['colab']),
                                 (m.ISSUER + '/.well-known/openid-configuration', 'token_endpoint', 'http://elsewhere/token')]:
            with self.subTest(url=url):
                responses = self.responses()
                responses[url][1][field] = value
                with self.assertRaises(m.Refused):
                    self.protocol(responses)
        responses = self.responses()
        responses[m.BASE + '/mcp'] = (200, {})
        with self.assertRaises(m.Refused):
            self.protocol(responses)

    def test_phase_has_one_locked_caller_and_no_production_capability(self):
        phase = yaml.load((ROOT / '.github/workflows/deploy-colab-transport-stage.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(phase['on']), {'workflow_call'})
        self.assertNotIn('concurrency', phase)
        self.assertEqual(phase['jobs']['stage']['needs'], 'admit')
        text = (ROOT / '.github/workflows/deploy-colab-transport-stage.yml').read_text()
        self.assertIn('open-hax/services/.github/workflows/deploy-stack.yml@refs/heads/main', text)
        self.assertIn('scripts/colab_stage_admission.py', text)
        self.assertNotIn('COLAB_TRANSPORT_STAGE_CLIENT_JSON', text)
        compose = yaml.safe_load((ROOT / 'digitalocean/services/colab-mcp/compose.transport-stage.yaml').read_text())
        self.assertEqual(set(compose['services']), m.SERVICES)
        for value in compose['services'].values():
            self.assertEqual(value['environment']['COLAB_AUTH_PROFILE'], 'transport-staging')
            self.assertNotIn('--auth-profile', value['command'])
            self.assertIs(value['read_only'], True)
            self.assertEqual(value['network_mode'], 'host')


if __name__ == '__main__':
    unittest.main()
