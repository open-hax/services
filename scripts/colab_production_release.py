#!/usr/bin/env python3
"""Fixed gateway-only cutover; any uncertain outcome retains its durable hold.

No ingress, browser bridge, compute service, identity provisioning or source
build is performed here. The canonical caller admits native image/Stage/full
qualification before SSH. This controller rechecks the immutable qualification
and host identity, preserves the exact quiesced private state and a restore copy,
and never restarts a legacy image as an authentication fallback.
"""
import argparse
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import time
import urllib.error
import urllib.request

from colab_production_admission import validate_plan
from colab_transport_stage import Host as StageHost, private_read, run, sync_directory, validate_tmpfs
from environment_policy import production_admission

OWNER_UID = 1000
RUNTIME = Path('/srv/open-hax/services/colab-mcp')
STATE = Path('/srv/open-hax/state/colab-mcp')
PROJECT = 'colab-mcp'
BASE = 'https://knoxx.promethean.rest/colab'
ISSUER = 'https://axxium.promethean.rest/oidc'
ADDRESS = 'http://172.31.255.1:18024'
REHEARSAL = 'http://127.0.0.1:18824'
COMMAND = ['knoxx-colab-mcp', 'gateway', '--host', '172.31.255.1', '--port', '18024']


class Refused(RuntimeError):
    pass


def require(condition, message='Fixed Colab production precondition refused'):
    if not condition:
        raise Refused(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def environment_hash(record):
    return digest(json.dumps(sorted(record['Config'].get('Env') or []), separators=(',', ':')).encode())


def directory(path, *, private=True):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER_UID
            and (stat.S_IMODE(info.st_mode) == 0o700 if private else not info.st_mode & 0o022))


def file_hash(path, *, private=True):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER_UID and info.st_nlink == 1
            and (stat.S_IMODE(info.st_mode) == 0o600 if private else not info.st_mode & 0o002)
            and info.st_size <= 64 * 1024 * 1024)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def state_inventory(root):
    """Private hash/permission inventory; raw credentials never enter receipts."""
    directory(root)
    result, size = {}, 0
    for path in sorted(root.rglob('*')):
        require(len(result) < 512)
        info = path.lstat()
        name = str(path.relative_to(root))
        if stat.S_ISDIR(info.st_mode):
            directory(path)
            result[name] = {'type': 'directory', 'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid}
        else:
            value = file_hash(path)
            size += info.st_size
            require(size <= 64 * 1024 * 1024, 'Private state exceeds the bounded snapshot size')
            result[name] = {'type': 'file', 'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid,
                            'size': info.st_size, 'sha256': value}
    require('bridge-token' in result and 'gateway-token' in result and 'axxium-client.json' in result
            and result.get('oauth', {}).get('type') == 'directory'
            and result.get('oauth/oauth.sqlite3', {}).get('type') == 'file', 'Expected private state is incomplete')
    return result


def copy_state(source, destination, expected):
    require(not destination.exists() and not destination.is_symlink())
    require(state_inventory(source) == expected, 'State changed outside the quiesced gateway')
    shutil.copytree(source, destination, symlinks=False)
    require(state_inventory(destination) == expected and state_inventory(source) == expected,
            'Snapshot or restore-copy bytes differ')
    for path in destination.rglob('*'):
        if path.is_file():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)
    for path in sorted((p for p in destination.rglob('*') if p.is_dir()), reverse=True):
        sync_directory(path)
    sync_directory(destination)
    sync_directory(destination.parent)


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(value)
        target.flush()
        os.fsync(target.fileno())
    sync_directory(path.parent)


def request(url, *, token=None, post=False):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-03-26', 'capabilities': {},
        'clientInfo': {'name': 'services-colab-production-denial', 'version': '1'}}}).encode() if post else None
    headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    if token is not None: headers['Authorization'] = 'Bearer ' + token
    try:
        with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url, data=body, headers=headers), timeout=10) as response:
            raw = response.read(65537)
            require(len(raw) <= 65536)
            return response.status, raw
    except urllib.error.HTTPError as error:
        return error.code, b''


def health(base):
    status, raw = request(base + '/health')
    require(status == 200)
    value = json.loads(raw)
    require(value.get('service') == 'knoxx-colab-mcp' and value.get('ready') is True
            and value.get('identity_available') is True, 'Gateway identity is unavailable')


def denials(base, old_key):
    for token in (None, 'services-invalid-credential', old_key):
        status, _ = request(base + '/mcp', token=token, post=True)
        require(status == 401, 'Gateway accepted an anonymous, wrong or legacy credential')


def protocol(base, old_key):
    health(base)
    status, raw = request(base + '/.well-known/oauth-authorization-server/colab')
    require(status == 200)
    value = json.loads(raw)
    require(value.get('issuer') == BASE and sorted(value.get('scopes_supported', [])) == ['openid', 'profile']
            and value.get('code_challenge_methods_supported') == ['S256']
            and value.get('response_types_supported') == ['code']
            and sorted(value.get('grant_types_supported', [])) == ['authorization_code', 'refresh_token']
            and all(value.get(key) == BASE + suffix for key, suffix in {
                'authorization_endpoint': '/authorize', 'token_endpoint': '/token',
                'registration_endpoint': '/register', 'revocation_endpoint': '/revoke'}.items()),
            'Production OAuth discovery differs from its fixed public contract')
    status, raw = request(base + '/.well-known/oauth-protected-resource/colab/mcp')
    require(status == 200)
    value = json.loads(raw)
    require(value.get('resource') == BASE + '/mcp' and value.get('authorization_servers') == [BASE]
            and sorted(value.get('scopes_supported', [])) == ['openid', 'profile'])
    denials(base, old_key)


def validate_incumbent(record, expected):
    config, host = record.get('Config', {}), record.get('HostConfig', {})
    labels = config.get('Labels', {})
    require(record.get('Id') == expected['container_id'] and record.get('Image') == expected['image_id']
            and config.get('Image') == expected['image_reference'] and environment_hash(record) == expected['environment_sha256']
            and config.get('User') == '1000:1000' and config.get('Cmd') == COMMAND
            and config.get('Entrypoint') is None and record.get('Name') == '/colab-mcp-gateway-1'
            and labels.get('com.docker.compose.project') == PROJECT and labels.get('com.docker.compose.service') == 'gateway'
            and record.get('State', {}).get('Running') is True and record['State'].get('Paused') is False
            and host.get('NetworkMode') == 'host' and host.get('ReadonlyRootfs') is True
            and host.get('CapDrop') == ['ALL'] and 'no-new-privileges:true' in host.get('SecurityOpt', []))
    require({(r.get('Type'), r.get('Source'), r.get('Destination'), r.get('RW')) for r in record.get('Mounts', [])}
            == {('bind', str(STATE), '/run/colab', False), ('bind', str(STATE / 'oauth'), '/var/lib/colab-oauth', True)})
    validate_tmpfs(host)


def validate_candidate(record, candidate):
    config, host = record.get('Config', {}), record.get('HostConfig', {})
    labels = config.get('Labels', {})
    expected_env = {'COLAB_AUTH_PROFILE': 'production', 'COLAB_BRIDGE_TOKEN_FILE': '/run/colab/bridge-token',
        'COLAB_BRIDGE_URL': 'http://127.0.0.1:18023/mcp', 'COLAB_COMPUTE_URL': 'http://127.0.0.1:18025/mcp',
        'COLAB_OAUTH_BASE_URL': BASE, 'COLAB_OAUTH_STATE_DIR': '/var/lib/colab-oauth',
        'COLAB_AXXIUM_CLIENT_FILE': '/run/colab/axxium-client.json'}
    values = config.get('Env', [])
    env = dict(value.split('=', 1) for value in values)
    require(len(env) == len(values) and {k: v for k, v in env.items() if k.startswith('COLAB_')} == expected_env)
    require(record.get('Image') == candidate['runtime_image_id'] and config.get('Image') == candidate['image_reference']
        and config.get('Cmd') == COMMAND and config.get('Entrypoint') is None and config.get('User') == '1000:1000'
        and record.get('State', {}).get('Running') is True and record['State'].get('Paused') is False
        and labels.get('com.docker.compose.project') == PROJECT and labels.get('com.docker.compose.service') == 'gateway'
        and labels.get('org.opencontainers.image.source') == 'https://github.com/open-hax/cloud-cuckoo'
        and labels.get('org.opencontainers.image.revision') == candidate['source_sha']
        and labels.get('org.opencontainers.image.licenses') == 'GPL-3.0-or-later'
        and host.get('NetworkMode') == 'host' and host.get('ReadonlyRootfs') is True
        and host.get('CapDrop') == ['ALL'] and 'no-new-privileges:true' in host.get('SecurityOpt', [])
        and host.get('Memory') == 268435456 and host.get('NanoCpus') == 500000000 and host.get('PidsLimit') == 128
        and host.get('RestartPolicy', {}).get('Name') == 'unless-stopped')
    validate_tmpfs(host)
    mounts = record.get('Mounts', [])
    require(len(mounts) == 2 and {(r.get('Type'), r.get('Source'), r.get('Destination'), r.get('RW')) for r in mounts}
        == {('bind', str(STATE), '/run/colab', False), ('bind', str(STATE / 'oauth'), '/var/lib/colab-oauth', True)})


def execute(host):
    """There is no catch/retry/legacy restart after an uncertain native command."""
    with host.lock():
        host.preflight()
        host.image()
        host.claim()
        for name in ('freeze', 'snapshot', 'rehearse', 'unfreeze', 'cutover', 'verify'):
            host.record(name, 'pending')
            value = getattr(host, name)()
            host.record(name, 'succeeded')
        return host.publish(value)


class Host:
    # Reuse accepted image/manifest/config/platform/full-license validation and
    # anonymous immutable pull. This method touches no Stage runtime or state.
    image = StageHost.image

    def __init__(self, plan, controller, checkout):
        self.full_plan, self.plan, self.controller, self.checkout = plan, plan['candidate'], controller, checkout
        self.pending = RUNTIME / '.colab-production-pending'
        self.compose = checkout / 'digitalocean/services/colab-mcp/compose.yaml'
        self.environment = self.compose.parent / '.env'
        self.proof = checkout / f'digitalocean/qualifications/colab-mcp/{self.plan["source_sha"]}.json'

    @contextmanager
    def lock(self):
        require(os.getuid() == OWNER_UID)
        directory(RUNTIME, private=False)
        fd = os.open(RUNTIME / '.colab-production.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER_UID and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600)
        with os.fdopen(fd, 'w') as target:
            fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def inspect(self, selector):
        records = json.loads(run(['docker', 'inspect', selector], timeout=20))
        require(isinstance(records, list) and len(records) == 1)
        return records[0]

    def inventory(self):
        ids = run(['docker', 'ps', '-aq'], timeout=20).decode().split()
        records = json.loads(run(['docker', 'inspect', *ids], timeout=30)) if ids else []
        return {r['Id']: {'image': r['Image'], 'running': r['State']['Running'], 'paused': r['State']['Paused']}
            for r in records if ((r.get('Config') or {}).get('Labels') or {}).get('com.docker.compose.project') != PROJECT}

    def identity(self):
        from provision_axxium_colab_identity import check
        require(check() == {'verified': True, 'status': 'complete'}, 'Production identity binding is unavailable')
        status, raw = request(ISSUER + '/.well-known/openid-configuration')
        require(status == 200, 'Production Axxium issuer is unavailable')
        value = json.loads(raw)
        require(value.get('issuer') == ISSUER and 'S256' in value.get('code_challenge_methods_supported', [])
            and 'client_secret_post' in value.get('token_endpoint_auth_methods_supported', [])
            and all(isinstance(value.get(key), str) and value[key].startswith(ISSUER + '/')
                and not re.search(r'[?#%]|/\.\./', value[key]) for key in ('authorization_endpoint',
                    'token_endpoint', 'introspection_endpoint', 'revocation_endpoint', 'jwks_uri')),
            'Production issuer metadata escaped its pinned contract')

    def preflight(self):
        require(not self.pending.exists() and not self.pending.is_symlink(), 'Unresolved production hold exists')
        require(not self.environment.exists() and not self.environment.is_symlink(), 'Controller selector already exists')
        expected = self.full_plan['predecessor']
        require(not (RUNTIME / '.env').exists() and not (RUNTIME / '.env').is_symlink(),
                'Legacy runtime unexpectedly acquired an environment file')
        require(file_hash(RUNTIME / 'compose.yaml', private=False) == expected['compose_sha256'])
        self.old = self.inspect(expected['container_id'])
        validate_incumbent(self.old, expected)
        ids = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT], timeout=20).decode().split()
        require(ids == [expected['container_id']], 'Colab project membership changed')
        self.identity()
        proof = json.loads(private_read(self.proof, maximum=2 * 1024 * 1024))
        require(proof.get('repository') == 'open-hax/cloud-cuckoo' and proof.get('source_sha') == self.plan['source_sha'])
        decision = production_admission(self.plan['source_sha'], proof.get('integration'), proof.get('e2e'), proof.get('mutation_batches'))
        require(decision.get('admitted') is True, 'Host production qualification refused')
        self.qualification_hash = digest(self.proof.read_bytes())
        self.before = self.inventory()
        self.protected = state_inventory(STATE)
        self.old_key = private_read(STATE / 'gateway-token').decode().strip()
        require(len(self.old_key) >= 32 and '\n' not in self.old_key and '\r' not in self.old_key)
        with socket.socket() as probe: probe.bind(('127.0.0.1', 18824))
        require(os.statvfs(RUNTIME).f_bavail * os.statvfs(RUNTIME).f_frsize >= 2 * 1024 ** 3)

    def claim(self):
        self.transaction = RUNTIME / ('transaction-' + self.controller + '-' + secrets.token_hex(12))
        self.transaction.mkdir(mode=0o700)
        self.journal = self.transaction / 'journal.jsonl'
        write_new(self.journal, b'')
        write_new(self.pending, json.dumps({'schema': 'colab-production-hold/v1', 'controller_sha': self.controller,
            'source_sha': self.plan['source_sha'], 'image_reference': self.plan['image_reference'],
            'transaction': self.transaction.name}, sort_keys=True).encode())
        write_new(self.transaction / 'incumbent.json', json.dumps(self.old, sort_keys=True).encode())
        write_new(self.transaction / 'protected-state.json', json.dumps(self.protected, sort_keys=True).encode())
        write_new(self.transaction / 'incumbent-compose.yaml', (RUNTIME / 'compose.yaml').read_bytes())
        require(file_hash(self.transaction / 'incumbent-compose.yaml') == self.full_plan['predecessor']['compose_sha256'])

    def record(self, step, outcome):
        with self.journal.open('ab') as target:
            target.write(json.dumps({'step': step, 'outcome': outcome}, sort_keys=True).encode() + b'\n')
            target.flush()
            os.fsync(target.fileno())

    def freeze(self):
        run(['docker', 'pause', self.old['Id']], timeout=20)
        require(self.inspect(self.old['Id'])['State']['Paused'] is True)

    def snapshot(self):
        require(state_inventory(STATE) == self.protected)
        backup, restore = self.transaction / 'before-state', self.transaction / 'restore-copy'
        copy_state(STATE, backup, self.protected)
        copy_state(backup, restore, self.protected)
        verify = self.transaction / 'sqlite-verification'
        copy_state(restore, verify, self.protected)
        # SQLite may update its private SHM bookkeeping; only a disposable
        # verification copy is opened, leaving both exact copies untouched.
        with closing(sqlite3.connect('file:' + str(verify / 'oauth/oauth.sqlite3') + '?mode=ro', uri=True)) as database:
            require(database.execute('PRAGMA integrity_check').fetchall() == [('ok',)])
        require(state_inventory(backup) == state_inventory(restore) == self.protected)

    def rehearse(self):
        candidate = self.transaction / 'candidate-rehearsal'
        copy_state(self.transaction / 'restore-copy', candidate, self.protected)
        name = 'colab-production-rehearsal-' + self.transaction.name[-24:]
        args = ['docker', 'run', '-d', '--pull', 'never', '--name', name, '--network', 'host',
            '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', '256m', '--cpus', '0.5', '--pids-limit', '128', '--tmpfs', '/tmp:size=32m,mode=1777',
            '--label', 'open-hax.colab-production-rehearsal=' + self.controller,
            '--mount', f'type=bind,src={candidate},dst=/run/colab,readonly',
            '--mount', f'type=bind,src={candidate / "oauth"},dst=/var/lib/colab-oauth']
        for value in ('COLAB_AUTH_PROFILE=production', 'COLAB_BRIDGE_TOKEN_FILE=/run/colab/bridge-token',
            'COLAB_BRIDGE_URL=http://127.0.0.1:18023/mcp', 'COLAB_COMPUTE_URL=http://127.0.0.1:18025/mcp',
            'COLAB_OAUTH_BASE_URL=' + BASE, 'COLAB_OAUTH_STATE_DIR=/var/lib/colab-oauth',
            'COLAB_AXXIUM_CLIENT_FILE=/run/colab/axxium-client.json', 'FASTMCP_CHECK_FOR_UPDATES=off'):
            args.extend(['--env', value])
        identifier = run([*args, self.plan['runtime_image_id'], 'knoxx-colab-mcp', 'gateway',
            '--host', '127.0.0.1', '--port', '18824'], timeout=30).decode().strip()
        require(re.fullmatch(r'[0-9a-f]{64}', identifier))
        record = self.inspect(identifier)
        require(record['Config'].get('Labels', {}).get('open-hax.colab-production-rehearsal') == self.controller
                and record.get('Image') == self.plan['runtime_image_id'] and record['State']['Running'] is True)
        self.readiness(REHEARSAL)
        run(['docker', 'stop', '--time', '10', identifier], timeout=20)
        require(self.inspect(identifier)['State']['Running'] is False)
        run(['docker', 'rm', identifier], timeout=20)
        require(state_inventory(STATE) == self.protected and self.inventory() == self.before)

    def readiness(self, base):
        deadline = time.monotonic() + 60
        while True:
            try:
                protocol(base, self.old_key)
                return
            except (Refused, OSError, ValueError):
                require(time.monotonic() < deadline, 'Production gateway readiness failed; hold retained')
                time.sleep(2)

    def unfreeze(self):
        require(state_inventory(STATE) == self.protected)
        run(['docker', 'unpause', self.old['Id']], timeout=20)
        validate_incumbent(self.inspect(self.old['Id']), self.full_plan['predecessor'])

    def command(self, *args):
        return run(['docker', 'compose', '--project-name', PROJECT, '--file', str(self.compose),
            '--env-file', str(self.environment), *args], timeout=120)

    def cutover(self):
        write_new(self.environment, ('COLAB_MCP_IMAGE=' + self.plan['image_reference'] + '\n'
            + 'COLAB_MCP_SOURCE_REVISION=' + self.plan['source_sha'] + '\n'
            + 'COLAB_MCP_STATE_DIR=' + str(STATE) + '\n').encode())
        self.command('up', '-d', '--pull', 'never', '--no-build')

    def verify(self):
        ids = self.command('ps', '-q', 'gateway').decode().split()
        require(len(ids) == 1)
        record = self.inspect(ids[0])
        validate_candidate(record, self.plan)
        self.readiness(ADDRESS)
        self.identity()
        # This is the required source-owned service gate, using its new private
        # controller selector. The legacy runtime/.env remains absent.
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(self.transaction),
            'COLAB_MCP_IMAGE': self.plan['image_reference'], 'COLAB_MCP_SOURCE_REVISION': self.plan['source_sha'],
            'COLAB_MCP_STATE_DIR': str(STATE)}
        result = subprocess.run(['bash', 'verify.sh'], cwd=self.compose.parent, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False)
        require(result.returncode == 0, 'Required production service gate failed')
        denials(BASE, self.old_key)
        require(self.inventory() == self.before, 'Unrelated runtime changed within the gateway transaction')
        require(file_hash(RUNTIME / 'compose.yaml', private=False) == self.full_plan['predecessor']['compose_sha256']
            and not (RUNTIME / '.env').exists())
        after = state_inventory(STATE)
        for name, before in self.protected.items():
            if not name.startswith('oauth/'):
                require(after.get(name) == before, 'Protected credential bytes or metadata changed')
        return {'gateway_container_id': ids[0], 'unrelated_runtime_preserved': True}

    def publish(self, proof):
        receipt = {'schema': 'colab-qualified-production-receipt/v1', 'phase': 'production', 'health': 'success',
            'source_repository': 'open-hax/cloud-cuckoo', 'source_sha': self.plan['source_sha'],
            'image_reference': self.plan['image_reference'], 'image_id': self.plan['image_id'],
            'runtime_image_id': self.plan['runtime_image_id'], 'services_sha': self.controller,
            'compose_sha256': file_hash(self.compose),
            'plan_sha256': file_hash(self.checkout / 'digitalocean/services/colab-mcp/production-plan.json'),
            'qualification_sha256': self.qualification_hash, 'restore_copy_verified': True,
            'manifest_config_binding_verified': True, 'anonymous_and_legacy_key_denied': True,
            'human_login_verified': False, 'google_execution_verified': False,
            'completed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **proof}
        write_new(self.transaction / 'healthy-receipt.json', json.dumps(receipt, sort_keys=True).encode())
        self.record('transaction', 'healthy')
        self.pending.unlink()
        sync_directory(RUNTIME)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--controller-sha', required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--apply', action='store_true', required=True)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r'[0-9a-f]{40}', args.controller_sha or '')
            and re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256 or ''))
        checkout = Path(__file__).resolve().parents[1]
        require(checkout == RUNTIME / 'controllers' / args.controller_sha)
        directory(checkout)
        plan_path = checkout / 'digitalocean/services/colab-mcp/production-plan.json'
        require(Path(args.plan).resolve() == plan_path)
        raw = private_read(plan_path)
        require(digest(raw) == args.plan_sha256)
        plan = validate_plan(json.loads(raw))
        print(json.dumps(execute(Host(plan, args.controller_sha, checkout)), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'health': 'failed', 'reason': 'fixed Colab production refused or operational hold retained'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
