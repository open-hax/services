#!/usr/bin/env python3
"""One fixed transport-stage transaction; production is never selected here.

Only admitted GHCR bytes and the reviewed compose definition reach the host.
Existing Axxium authority/client enrollment is an explicit separate prerequisite.
An uncertain or failed transaction leaves a private journal and cannot be retried
as a new deployment. The existing production host queue encloses this controller.
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from colab_stage_admission import LICENSE, validate_plan

RUNTIME = Path('/srv/open-hax/services/colab-transport-stage')
STATE = Path('/srv/open-hax/state/colab-transport-stage')
PROJECT = 'colab-mcp-transport-staging'
SERVICES = {'bridge-staging', 'compute-staging', 'gateway-staging'}
ACTOR = 'actor_20603b93-2c96-4ff2-a213-0b1e84017993'
ISSUER = 'https://staging.axxium.promethean.rest/oidc'
BASE = 'http://127.0.0.1:18771'
OWNER_UID = 1000


class Refused(RuntimeError):
    pass


def require(condition, message='Transport-stage precondition refused'):
    if not condition:
        raise Refused(message)


def private_read(path, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER_UID and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= maximum)
        with os.fdopen(fd, 'rb', closefd=False) as source:
            return source.read(maximum + 1)
    finally:
        os.close(fd)


def controlled_directory(path, create=False):
    if create and not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER_UID and not info.st_mode & 0o077)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_client(raw):
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {'issuer', 'client_id', 'client_secret',
            'allowed_subjects', 'allowed_client_redirect_uris'} and value['issuer'] == ISSUER and
            value['client_id'] == 'knoxx-colab-transport-staging' and
            value['allowed_subjects'] == [ACTOR] and
            value['allowed_client_redirect_uris'] == ['http://127.0.0.1:18776/callback'] and
            isinstance(value['client_secret'], str) and 32 <= len(value['client_secret']) <= 512)


def run(args, *, data=None, env=None, timeout=180):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, timeout=timeout, check=False)
    require(result.returncode == 0, 'Bounded transport-stage command failed')
    return result.stdout


def verify_manifest(value, plan):
    """Bind Docker CLI's original manifest bytes to both immutable identities."""
    require(isinstance(value, dict) and value.get('Ref') == plan['image_reference'] and
            isinstance(value.get('Raw'), str) and len(value['Raw']) <= 131072)
    raw = base64.b64decode(value['Raw'], validate=True)
    descriptor = value.get('Descriptor', {})
    require(0 < len(raw) <= 65536 and
            'sha256:' + hashlib.sha256(raw).hexdigest() == plan['runtime_image_id'] and
            descriptor.get('digest') == plan['runtime_image_id'] and descriptor.get('size') == len(raw) and
            descriptor.get('mediaType') == 'application/vnd.docker.distribution.manifest.v2+json' and
            descriptor.get('platform') == {'architecture': 'amd64', 'os': 'linux'})
    manifest = json.loads(raw)
    require(manifest == value.get('SchemaV2Manifest') and manifest.get('schemaVersion') == 2 and
            manifest.get('mediaType') == descriptor['mediaType'] and
            manifest.get('config', {}).get('digest') == plan['image_id'] and
            manifest['config'].get('mediaType') == 'application/vnd.docker.container.image.v1+json' and
            type(manifest['config'].get('size')) is int and manifest['config']['size'] > 0 and
            isinstance(manifest.get('layers'), list) and bool(manifest['layers']))
    for layer in manifest['layers']:
        require(isinstance(layer, dict) and layer.get('mediaType') == 'application/vnd.docker.image.rootfs.diff.tar.gzip' and
                isinstance(layer.get('digest'), str) and re.fullmatch(r'sha256:[0-9a-f]{64}', layer['digest']) and
                type(layer.get('size')) is int and layer['size'] > 0)
    return len(raw)


def inspect_image(value, plan, manifest_size):
    require(isinstance(value, list) and len(value) == 1)
    record = value[0]
    config = record.get('Config', {})
    labels = config.get('Labels', {})
    descriptor = record.get('Descriptor', {})
    require(record.get('Id') == plan['runtime_image_id'] and
            descriptor.get('digest') == plan['runtime_image_id'] and descriptor.get('size') == manifest_size and
            descriptor.get('mediaType') == 'application/vnd.docker.distribution.manifest.v2+json' and
            record.get('RepoDigests') == [plan['image_reference']] and config.get('User') == '1000:1000' and
            record.get('Os') == 'linux' and record.get('Architecture') == 'amd64' and
            labels.get('org.opencontainers.image.source') == 'https://github.com/open-hax/cloud-cuckoo' and
            labels.get('org.opencontainers.image.revision') == plan['source_sha'] and
            labels.get('org.opencontainers.image.licenses') == 'GPL-3.0-or-later')


def inventory():
    ids = run(['docker', 'ps', '-aq'], timeout=20).decode().split()
    if not ids:
        return {}
    # Project only immutable/runtime fields; container environments never leave
    # this private process or enter a public receipt.
    values = json.loads(run(['docker', 'inspect', *ids], timeout=30))
    return {r['Id']: {'image': r['Image'], 'running': r['State']['Running']}
            for r in values if ((r.get('Config') or {}).get('Labels') or {}).get('com.docker.compose.project') != PROJECT}


def request(url, *, post=False):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-03-26', 'capabilities': {},
        'clientInfo': {'name': 'services-colab-stage-denial', 'version': '1'}}}).encode() if post else None
    req = urllib.request.Request(url, data=body, headers={
        'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as response:
            raw = response.read(65537)
            require(len(raw) <= 65536)
            return response.status, raw
    except urllib.error.HTTPError as error:
        return error.code, b''


def protocol_checks():
    status, raw = request(BASE + '/health')
    require(status == 200)
    health = json.loads(raw)
    require(health.get('service') == 'knoxx-colab-mcp' and health.get('ready') is True and
            health.get('identity_available') is True, 'Stage identity is pending')
    status, raw = request(BASE + '/.well-known/oauth-authorization-server')
    require(status == 200)
    discovery = json.loads(raw)
    # Pydantic's root AnyHttpUrl in the actual FastMCP OAuthProxy metadata is
    # serialized with its canonical trailing slash. Endpoints/resource retain
    # their exact compiled paths; do not normalize arbitrary URLs here.
    require(discovery.get('issuer') == BASE + '/' and discovery.get('scopes_supported') == ['openid', 'profile'] and
            discovery.get('code_challenge_methods_supported') == ['S256'] and
            all(discovery.get(k) == BASE + suffix for k, suffix in {
                'authorization_endpoint': '/authorize', 'token_endpoint': '/token',
                'registration_endpoint': '/register', 'revocation_endpoint': '/revoke'}.items()))
    status, raw = request(BASE + '/.well-known/oauth-protected-resource/mcp')
    require(status == 200)
    resource = json.loads(raw)
    require(resource.get('resource') == BASE + '/mcp' and resource.get('authorization_servers') == [BASE + '/'] and
            sorted(resource.get('scopes_supported', [])) == ['openid', 'profile'])
    status, raw = request(ISSUER + '/.well-known/openid-configuration')
    require(status == 200)
    issuer = json.loads(raw)
    require(issuer.get('issuer') == ISSUER and 'S256' in issuer.get('code_challenge_methods_supported', []) and
            'client_secret_post' in issuer.get('token_endpoint_auth_methods_supported', []) and
            all(isinstance(issuer.get(k), str) and issuer[k].startswith(ISSUER + '/') and
                not re.search(r'[?#%]|/\.\./', issuer[k]) for k in ('authorization_endpoint', 'token_endpoint',
                    'introspection_endpoint', 'revocation_endpoint', 'jwks_uri')))
    for port in (18771, 18773, 18775):
        status, _ = request(f'http://127.0.0.1:{port}/mcp', post=True)
        require(status == 401, 'Stage anonymous MCP denial failed')


def validate_containers(records, plan):
    require(isinstance(records, list) and len(records) == 3)
    seen = set()
    expected_commands = {'bridge-staging': ['knoxx-colab-mcp', 'transport-stage-bridge', '--host', '127.0.0.1', '--port', '18773'],
                         'compute-staging': ['knoxx-colab-mcp', 'transport-stage-compute', '--host', '127.0.0.1', '--port', '18775'],
                         'gateway-staging': ['knoxx-colab-mcp', 'gateway', '--host', '127.0.0.1', '--port', '18771']}
    for record in records:
        config, host = record.get('Config', {}), record.get('HostConfig', {})
        labels = config.get('Labels', {})
        role = labels.get('com.docker.compose.service')
        require(role in SERVICES and role not in seen)
        seen.add(role)
        require(labels.get('com.docker.compose.project') == PROJECT and record.get('State', {}).get('Running') is True and
                record.get('Image') == plan['runtime_image_id'] and config.get('Image') == plan['image_reference'] and
                config.get('User') == '1000:1000' and config.get('Cmd') == expected_commands[role] and
                'COLAB_AUTH_PROFILE=transport-staging' in config.get('Env', []) and
                labels.get('org.opencontainers.image.revision') == plan['source_sha'] and
                host.get('NetworkMode') == 'host' and host.get('ReadonlyRootfs') is True and
                host.get('Memory') == 256 * 1024 * 1024 and host.get('NanoCpus') == 500000000 and
                host.get('CapDrop') == ['ALL'] and 'no-new-privileges:true' in host.get('SecurityOpt', []))
        require(all(v.get('Type') == 'bind' for v in record.get('Mounts', [])))
        mounts = {(v.get('Source'), v.get('Destination'), v.get('RW')) for v in record.get('Mounts', [])}
        expected = {(str(STATE), '/run/colab-transport-stage', False)}
        if role == 'gateway-staging':
            expected.add((str(STATE / 'oauth'), '/var/lib/colab-transport-stage', True))
        require(mounts == expected, 'Stage mount isolation changed')


class Host:
    def __init__(self, plan, controller, checkout):
        self.plan, self.controller, self.checkout = plan, controller, checkout
        self.compose = checkout / 'digitalocean/services/colab-mcp/compose.transport-stage.yaml'
        self.environment = RUNTIME / 'environment'
        self.pending = RUNTIME / '.pending'

    @contextmanager
    def lock(self):
        require(os.getuid() == OWNER_UID, 'Trusted deploy UID required')
        controlled_directory(RUNTIME, create=True)
        fd = os.open(RUNTIME / '.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER_UID and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600)
        with os.fdopen(fd, 'w') as target:
            fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def preflight(self):
        require(not self.pending.exists() and not self.pending.is_symlink(), 'Unresolved transport-stage transaction exists')
        controlled_directory(STATE)
        validate_client(private_read(STATE / 'axxium-client.json'))
        # Enforce the reviewed app/controller input; it is never a host checkout.
        require(self.compose.is_file() and not self.compose.is_symlink())
        private_read(self.compose)
        ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT], timeout=20)
        require(not ids.strip(), 'Fixed first Stage adoption already has containers')
        for port in (18771, 18773, 18775, 18776):
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
        require(os.statvfs(RUNTIME).f_bavail * os.statvfs(RUNTIME).f_frsize >= 2 * 1024 ** 3,
                'Stage disk admission failed')
        memory = Path('/proc/meminfo').read_text()
        require(int(re.search(r'^MemAvailable:\s+(\d+) kB', memory, re.M)[1]) >= 1024 ** 2,
                'Stage memory admission failed')
        self.before = inventory()

    def image(self):
        with tempfile.TemporaryDirectory(prefix='colab-anonymous-') as auth:
            env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': auth, 'DOCKER_CONFIG': auth}
            run(['docker', 'pull', self.plan['image_reference']], env=env, timeout=180)
            size = verify_manifest(json.loads(run(['docker', 'manifest', 'inspect', '--verbose',
                                                   self.plan['image_reference']], env=env)), self.plan)
            inspect_image(json.loads(run(['docker', 'image', 'inspect', self.plan['image_reference']], env=env)),
                          self.plan, size)
            check = ("from pathlib import Path; import hashlib; "
                     "assert hashlib.sha256(Path('/usr/share/licenses/cloud-cuckoo/LICENSE').read_bytes()).hexdigest()==" + repr(LICENSE))
            run(['docker', 'run', '--rm', '-i', '--pull', 'never', '--network', 'none', '--read-only',
                 '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                 '--pids-limit', '64', '--memory', '128m', self.plan['runtime_image_id'], 'python', '-'],
                data=check.encode(), env=env)

    def claim(self):
        fd = os.open(self.pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as target:
            json.dump({'schema': 'colab-stage-pending/v1', 'controller_sha': self.controller,
                       'source_sha': self.plan['source_sha'], 'image_reference': self.plan['image_reference']}, target)
            target.flush()
            os.fsync(target.fileno())
        sync_directory(RUNTIME)

    def start(self):
        token = STATE / 'fixture-token'
        if token.exists() or token.is_symlink():
            require(len(private_read(token).strip()) >= 32)
        else:
            fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as target:
                target.write(secrets.token_urlsafe(48) + '\n')
        controlled_directory(STATE / 'oauth', create=True)
        # A fixed first adoption never overwrites a prior deployment selector.
        with self.environment.open('x') as target:
            os.chmod(self.environment, 0o600)
            target.write('COLAB_MCP_IMAGE=' + self.plan['image_reference'] + '\n')
            target.flush()
            os.fsync(target.fileno())
        self.command('up', '-d', '--pull', 'never', '--no-build')

    def command(self, *args):
        return run(['docker', 'compose', '--project-name', PROJECT, '--file', str(self.compose),
                    '--env-file', str(self.environment), *args], timeout=120)

    def verify(self):
        ids = self.command('ps', '-q').decode().split()
        require(len(ids) == 3)
        validate_containers(json.loads(run(['docker', 'inspect', *ids], timeout=30)), self.plan)
        # Startup readiness retries are read-only, bounded, and give no grant.
        deadline = time.monotonic() + 60
        while True:
            try:
                protocol_checks()
                break
            except (Refused, OSError, ValueError):
                require(time.monotonic() < deadline, 'Stage protocol readiness failed')
                time.sleep(2)
        from colab_stage_oauth_smoke import smoke
        proof = smoke()
        # The owning helper mints/revokes only an existing synthetic Stage
        # session. It must prove the actual authenticated HTTP gateway path.
        require(isinstance(proof, dict) and proof.get('passed') is True and
                proof.get('synthetic_actor') is True and proof.get('synthetic_upstreams') is True and
                proof.get('oauth_flow_verified') is True and proof.get('gateway_forwarding_verified') is True and
                proof.get('execution_denied') is True and proof.get('session_revoked') is True and
                proof.get('google_execution_verified') is False and
                type(proof.get('assertions')) is int and proof['assertions'] > 0,
                'Authenticated Stage forwarding proof unavailable')
        require(inventory() == self.before, 'Unrelated runtime identity or state changed')
        return proof

    def publish(self, proof):
        receipt = {'schema': 'colab-transport-stage-receipt/v1', 'phase': 'staging', 'health': 'success',
                   'source_repository': 'open-hax/cloud-cuckoo', 'source_sha': self.plan['source_sha'],
                   'image_reference': self.plan['image_reference'], 'image_id': self.plan['image_id'],
                   'runtime_image_id': self.plan['runtime_image_id'], 'manifest_config_binding_verified': True,
                   'services_sha': self.controller, 'compose_project': PROJECT,
                   'compose_sha256': hashlib.sha256(self.compose.read_bytes()).hexdigest(),
                   'plan_sha256': hashlib.sha256((self.checkout / 'digitalocean/services/colab-mcp/transport-stage-plan.json').read_bytes()).hexdigest(),
                   'synthetic_actor': True, 'synthetic_upstreams': True,
                   'human_login_verified': False,
                   'oauth_flow_verified': True, 'gateway_forwarding_verified': True,
                   'google_execution_verified': False, 'production_qualified': False,
                   'assertions': proof['assertions'], 'session_revoked': True,
                   'unrelated_runtime_preserved': True, 'completed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        destination = RUNTIME / 'stage-receipt.json'
        with destination.open('x') as target:
            os.chmod(destination, 0o600)
            json.dump(receipt, target, sort_keys=True, indent=2)
            target.flush()
            os.fsync(target.fileno())
        sync_directory(RUNTIME)
        self.pending.unlink()
        sync_directory(RUNTIME)
        return receipt


def execute(host):
    with host.lock():
        host.preflight()
        host.image()
        host.claim()
        host.start()
        proof = host.verify()
        return host.publish(proof)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--controller-sha', required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--apply', action='store_true', required=True)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r'[0-9a-f]{40}', args.controller_sha or '') and
                re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256 or ''))
        checkout = Path(__file__).resolve().parents[1]
        require(checkout == RUNTIME / 'controllers' / args.controller_sha)
        controlled_directory(checkout)
        require(Path(args.plan).resolve() == checkout / 'digitalocean/services/colab-mcp/transport-stage-plan.json')
        raw = Path(args.plan).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == args.plan_sha256)
        plan = validate_plan(json.loads(raw))
        receipt = execute(Host(plan, args.controller_sha, checkout))
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'schema': 'colab-transport-stage-result/v1', 'health': 'failed',
                          'reason': 'transport stage failed or requires explicit recovery; no production proof'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
