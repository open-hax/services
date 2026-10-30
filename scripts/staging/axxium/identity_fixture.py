"""Create a production-shaped identity solely on the disposable staging VM.

This module is test-only. It writes fresh synthetic credentials, never imports
production material, and must run inside a network namespace without routes.
The fixture runner owns setup and destruction of its VM and containers.
"""
import datetime
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import fixture_netns

IMAGE = 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
ISSUER = 'https://axxium.promethean.rest/oidc'
CALLBACK = 'https://knoxx.promethean.rest/colab/auth/callback'
ACTOR = re.compile(r'actor_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
SIGNUP = r"""const fs=require('node:fs');
(async()=>{const d=JSON.parse(fs.readFileSync(0,'utf8'));
const r=await fetch('http://127.0.0.1:8787/api/auth/signup',{
method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(d)});
if(r.status!==200)process.exit(2);const j=await r.json();
if(typeof j.actor?.id!=='string')process.exit(3);
process.stdout.write(j.actor.id);})().catch(()=>process.exit(4));"""
GENERATE_KEY = r"""const c=require('node:crypto');
const k=c.generateKeyPairSync('rsa',{modulusLength:3072,publicExponent:65537});
process.stdout.write(JSON.stringify(k.privateKey.export({format:'jwk'})));"""


class FixtureRefused(RuntimeError):
    pass


def command(args, *, data=b''):
    try:
        p = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise FixtureRefused('Synthetic identity command unavailable') from None
    if p.returncode:
        raise FixtureRefused('Synthetic identity command failed')
    return p.stdout


def private_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def validate_root(app_id, state_root, controller_root):
    if (os.getuid() != 1000 or os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise FixtureRefused('Only the isolated hosted runner is supported')
    state_root, controller_root = Path(state_root), Path(controller_root)
    if (state_root != Path('/srv/open-hax/state') or state_root.resolve() != state_root or
            controller_root.resolve() != controller_root or
            Path('/srv/open-hax/services/axxium/controllers') not in controller_root.parents):
        raise FixtureRefused('Unexpected fixture paths')
    if not re.fullmatch(r'[0-9a-f]{64}', app_id):
        raise FixtureRefused('Exact fixture application ID required')
    marker = state_root / '.axxium-staging-fixture'
    info = marker.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
            marker.read_bytes() != (app_id + '\n').encode()):
        raise FixtureRefused('Fixture marker differs')
    # The runner must have only its own loopback interface. Docker is reached
    # through its Unix socket; the fixture uses internal Docker bridges.
    try:
        fixture_netns.require_isolated()
    except (OSError, RuntimeError, ValueError):
        raise FixtureRefused('Fixture process has an external network route') from None
    identity = command(['docker', 'inspect', '--format', '{{.Id}} {{.Image}}', app_id]).decode().strip()
    if identity != app_id + ' ' + IMAGE:
        raise FixtureRefused('Fixture application identity differs')
    raw_networks = command(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', app_id])
    try:
        networks = json.loads(raw_networks)
    except ValueError:
        raise FixtureRefused('Fixture network identity is unavailable') from None
    if not isinstance(networks, dict) or set(networks) != {'open-hax', 'axxium_database'}:
        raise FixtureRefused('Fixture application network scope differs')
    for name in networks:
        internal = command(['docker', 'network', 'inspect', '--format', '{{.Internal}}', name]).strip()
        if internal != b'true':
            raise FixtureRefused('Fixture application network is not internal')
    return state_root, controller_root


def create(app_id, state_root, controller_root, actor_id=None):
    """Create a test actor and complete identity; return only the actor ID.

    The caller must create the marker after starting its isolated candidate,
    copy the provisioner into the fixture controller and retain UID 1000.
    The resulting provisioner copy is rebound to the new synthetic actor.
    """
    state_root, controller_root = validate_root(app_id, state_root, controller_root)
    provisioner = controller_root / 'scripts' / 'provision_axxium_colab_identity.py'
    info = provisioner.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_nlink != 1 or provisioner.resolve() != provisioner:
        raise FixtureRefused('Fixture provisioner is not isolated')
    source = provisioner.read_text()
    selections = re.findall(r"(?m)^APPROVED_ACTOR = '(actor_[0-9a-f-]+)'$", source)
    if len(selections) != 1 or not ACTOR.fullmatch(selections[0]):
        raise FixtureRefused('Fixture provisioner actor selector differs')
    original_actor = selections[0]
    if actor_id is None:
        signup = {'email': 'fixture-' + secrets.token_hex(12) + '@example.invalid',
                  'password': secrets.token_urlsafe(32), 'display_name': 'Synthetic staging'}
        actor_id = command(['docker', 'exec', '-i', app_id, 'node', '-e', SIGNUP],
                           data=json.dumps(signup).encode()).decode()
    if not isinstance(actor_id, str) or not ACTOR.fullmatch(actor_id) or actor_id == original_actor:
        raise FixtureRefused('Synthetic actor identity is invalid')

    key_raw = command(['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
                       '--pull', 'never', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--pids-limit', '64', '--memory', '128m', '--entrypoint', 'node',
                       IMAGE, '-e', GENERATE_KEY])
    try:
        key = json.loads(key_raw)
        if set(key) != {'kty', 'n', 'e', 'd', 'p', 'q', 'dp', 'dq', 'qi'} or key['kty'] != 'RSA':
            raise ValueError('Invalid synthetic key shape')
    except (ValueError, TypeError):
        raise FixtureRefused('Synthetic signing key invalid') from None
    key.update(alg='RS256', use='sig', kid='fixture-' + secrets.token_hex(12))
    secret = secrets.token_urlsafe(48)
    binding = {'schema': 'axxium-colab-production-binding/v1', 'issuer': ISSUER,
               'actor_id': actor_id, 'actor_kind': 'human', 'actor_status': 'active',
               'evidence_sha256': '0' * 64,
               'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    authority = {'issuer': ISSUER, 'clients': [{'id': 'knoxx-colab', 'secret': secret,
                 'redirect-uris': [CALLBACK], 'refresh-enabled?': True, 'device-enabled?': False}],
                 'cookie-keys': [secrets.token_urlsafe(48), secrets.token_urlsafe(48)],
                 'jwks': {'keys': [key]}}
    gateway = {'issuer': ISSUER, 'client_id': 'knoxx-colab', 'client_secret': secret,
               'allowed_subjects': [actor_id]}
    encode = lambda value: (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    oidc, colab = state_root / 'axxium' / 'oidc', state_root / 'colab-mcp'
    for path in (oidc, colab):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 1000 or
                stat.S_IMODE(info.st_mode) != 0o700 or path.resolve() != path):
            raise FixtureRefused('Synthetic identity directory is not private')
    binding_data = encode(binding)
    private_file(oidc / 'production-binding.json', binding_data)
    private_file(oidc / 'authority.json', encode(authority))
    private_file(colab / 'axxium-client.json', encode(gateway))
    private_file(controller_root / 'production-binding.json', binding_data)
    old, new = "APPROVED_ACTOR = '" + original_actor + "'", "APPROVED_ACTOR = '" + actor_id + "'"
    if source.count(old) != 1:
        raise FixtureRefused('Fixture provisioner actor selector differs')
    provisioner.write_text(source.replace(old, new, 1))
    provisioner.chmod(0o600)
    return actor_id
