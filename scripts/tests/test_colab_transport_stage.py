"""The fixed Stage can neither select production nor hide a failed verification."""
from contextlib import contextmanager
import copy
import base64
import hashlib
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
                     'image_id': 'sha256:' + '3' * 64, 'runtime_image_id': 'sha256:' + '2' * 64,
                     'source_sha': '4' * 40}

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
            # The private fixture belongs to the runner (normally1001), not
            # the host's deployment account. All actual file checks still run.
            with patch.object(m, 'OWNER_UID', os.getuid()):
                self.assertEqual(m.private_read(path), b'private-fixture')
                path.chmod(0o644)
                with self.assertRaises(m.Refused):
                    m.private_read(path)
                link = Path(root) / 'link'
                link.symlink_to(path)
                with self.assertRaises(OSError):
                    m.private_read(link)

    def test_production_deploy_owner_is_closed_at_uid1000(self):
        self.assertEqual(m.OWNER_UID, 1000)

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
            records.append({'Image': self.plan['runtime_image_id'], 'State': {'Running': True}, 'Mounts': mounts,
                'Config': {'Image': self.plan['image_reference'], 'User': '1000:1000',
                    'Cmd': ['knoxx-colab-mcp', mode, '--host', '127.0.0.1', '--port', port],
                    'Env': ['COLAB_AUTH_PROFILE=transport-staging'], 'Labels': {
                        'com.docker.compose.project': m.PROJECT, 'com.docker.compose.service': role,
                        'org.opencontainers.image.revision': self.plan['source_sha']}},
                'HostConfig': {'NetworkMode': 'host', 'ReadonlyRootfs': True, 'Memory': 268435456,
                               'NanoCpus': 500000000, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'],
                               'Tmpfs': {'/tmp': 'mode=1777,size=32m'}}})
        return records

    def test_all_three_actual_containers_must_match_source_and_isolation(self):
        m.validate_containers(self.records(), self.plan)
        for mutate in [lambda r: r.pop(), lambda r: r[0].update(Image='sha256:' + 'a' * 64),
                       lambda r: r[1]['Config'].update(User='root'),
                       lambda r: r[2]['Config'].update(Cmd=['knoxx-colab-mcp', 'gateway']),
                       lambda r: r[0]['HostConfig'].update(ReadonlyRootfs=False),
                       lambda r: r[1]['HostConfig'].update(Memory=0),
                       lambda r: r[1]['HostConfig'].update(Tmpfs={'/tmp': 'size=32m', 'mode=1777': ''}),
                       lambda r: r[0]['Mounts'][0].update(Source='/srv/open-hax/state/colab-mcp'),
                       lambda r: r[2]['Mounts'][0].update(RW=True)]:
            with self.subTest(mutate=mutate):
                records = self.records()
                mutate(records)
                with self.assertRaises(m.Refused):
                    m.validate_containers(records, self.plan)

    def test_selected_image_id_and_license_source_are_required(self):
        image = [{'Id': self.plan['runtime_image_id'], 'Os': 'linux', 'Architecture': 'amd64',
                  'RepoDigests': [self.plan['image_reference']], 'Descriptor': {
                    'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
                    'digest': self.plan['runtime_image_id'], 'size': 2835},
                  'Config': {'User': '1000:1000', 'Labels': {
                    'org.opencontainers.image.source': 'https://github.com/open-hax/cloud-cuckoo',
                    'org.opencontainers.image.revision': self.plan['source_sha'],
                    'org.opencontainers.image.licenses': 'GPL-3.0-or-later'}}}]
        m.inspect_image(image, self.plan, 2835)
        for key in ('org.opencontainers.image.source', 'org.opencontainers.image.revision', 'org.opencontainers.image.licenses'):
            with self.subTest(key=key):
                altered = copy.deepcopy(image)
                altered[0]['Config']['Labels'][key] = 'wrong'
                with self.assertRaises(m.Refused):
                    m.inspect_image(altered, self.plan, 2835)

    def manifest(self):
        value = {'schemaVersion': 2, 'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
                 'config': {'mediaType': 'application/vnd.docker.container.image.v1+json',
                            'size': 9872, 'digest': self.plan['image_id']},
                 'layers': [{'mediaType': 'application/vnd.docker.image.rootfs.diff.tar.gzip',
                             'size': 200, 'digest': 'sha256:' + '5' * 64}]}
        raw = json.dumps(value, indent=2).encode()
        identity = 'sha256:' + hashlib.sha256(raw).hexdigest()
        self.plan.update(runtime_image_id=identity, image_reference='ghcr.io/open-hax/cloud-cuckoo@' + identity)
        return {'Ref': self.plan['image_reference'], 'Raw': base64.b64encode(raw).decode(),
                'SchemaV2Manifest': value, 'Descriptor': {'digest': identity, 'size': len(raw),
                    'mediaType': value['mediaType'], 'platform': {'architecture': 'amd64', 'os': 'linux'}}}

    def test_containerd_manifest_rawbytes_bind_publisher_config_id(self):
        manifest = self.manifest()
        self.assertGreater(m.verify_manifest(manifest, self.plan), 0)
        for mutate in [lambda x: x.update(Raw=base64.b64encode(b'changed').decode()),
                       lambda x: x['Descriptor'].update(digest=self.plan['image_id']),
                       lambda x: x['Descriptor'].update(platform={'architecture': 'arm64', 'os': 'linux'}),
                       lambda x: x['SchemaV2Manifest']['config'].update(digest='sha256:' + 'a' * 64),
                       lambda x: x.update(Ref='ghcr.io/elsewhere/cloud-cuckoo:latest')]:
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(manifest)
                mutate(value)
                with self.assertRaises(Exception):
                    m.verify_manifest(value, self.plan)
        with self.assertRaises(Exception):
            m.verify_manifest(manifest, {**self.plan, 'image_id': 'sha256:' + 'b' * 64})

    def test_runtime_container_cannot_substitute_classic_publisher_config_id(self):
        records = self.records()
        for record in records:
            record['Image'] = self.plan['image_id']
        with self.assertRaises(m.Refused):
            m.validate_containers(records, self.plan)

    def test_host_license_probe_uses_exact_containerd_runtime_id_without_pull(self):
        manifest = self.manifest()
        host = m.Host(self.plan, 'a' * 40, ROOT)
        calls = []
        def command(args, **kwargs):
            calls.append((args, kwargs))
            if args[:3] == ['docker', 'manifest', 'inspect']:
                return json.dumps(manifest).encode()
            return b'[]'
        with patch.object(m, 'run', side_effect=command), patch.object(m, 'inspect_image'):
            host.image()
        probe = next(args for args, _ in calls if args[:2] == ['docker', 'run'])
        self.assertIn(self.plan['runtime_image_id'], probe)
        self.assertNotIn(self.plan['image_id'], probe)
        self.assertEqual(probe[probe.index('--pull') + 1], 'never')
        for _, kwargs in calls:
            self.assertEqual(set(kwargs['env']), {'PATH', 'HOME', 'DOCKER_CONFIG'})

    def test_unlabelled_unrelated_runtime_is_preserved_in_inventory(self):
        with patch.object(m, 'run', side_effect=[b'a\nb\n', json.dumps([
            {'Id': 'a', 'Image': 'image-a', 'State': {'Running': True}, 'Config': {'Labels': None}},
            {'Id': 'b', 'Image': 'image-b', 'State': {'Running': True},
             'Config': {'Labels': {'com.docker.compose.project': m.PROJECT}}}]).encode()]):
            self.assertEqual(m.inventory(), {'a': {'image': 'image-a', 'running': True}})

    def responses(self):
        return {m.BASE + '/health': (200, {'service': 'knoxx-colab-mcp', 'ready': True, 'identity_available': True}),
                m.BASE + '/.well-known/oauth-authorization-server': (200, {
                    # Observed from the accepted19e FastMCP OAuthProxy's real
                    # HTTP response: root issuer has one canonical slash.
                    'issuer': m.BASE + '/', 'scopes_supported': ['openid', 'profile'],
                    'code_challenge_methods_supported': ['S256'],
                    **{k: m.BASE + v for k, v in {'authorization_endpoint': '/authorize', 'token_endpoint': '/token',
                       'registration_endpoint': '/register', 'revocation_endpoint': '/revoke'}.items()}}),
                m.BASE + '/.well-known/oauth-protected-resource/mcp': (200, {
                    'resource': m.BASE + '/mcp', 'authorization_servers': [m.BASE + '/'], 'scopes_supported': ['openid', 'profile']}),
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

    def test_observed_canonical_root_issuer_passes_but_port_or_path_drift_refuses(self):
        self.protocol(self.responses())
        for issuer in (m.BASE, m.BASE + '//', m.BASE + '/other/', 'http://127.0.0.1:18761/'):
            with self.subTest(issuer=issuer):
                responses = self.responses()
                responses[m.BASE + '/.well-known/oauth-authorization-server'][1]['issuer'] = issuer
                with self.assertRaises(m.Refused):
                    self.protocol(responses)
        responses = self.responses()
        responses[m.BASE + '/.well-known/oauth-protected-resource/mcp'][1]['authorization_servers'] = [m.BASE]
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
            # A comma in an unquoted flow-list item becomes a second mount.
            # This is the actual parsed shape Docker Compose must consume.
            self.assertEqual(value['tmpfs'], ['/tmp:size=32m,mode=1777'])

    def test_tmpfs_options_are_exact_but_order_independent(self):
        for value in ('size=32m,mode=1777', 'mode=1777,size=32m'):
            m.validate_tmpfs({'Tmpfs': {'/tmp': value}})
        for value in ({'/tmp': 'size=32m'}, {'/tmp': 'size=32m,mode=777'},
                      {'/tmp': 'size=32m,mode=1777,mode=1777'}, {'mode=1777': 'size=32m'}):
            with self.subTest(value=value), self.assertRaises(m.Refused):
                m.validate_tmpfs({'Tmpfs': value})


if __name__ == '__main__':
    unittest.main()
