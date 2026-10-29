#!/usr/bin/env python3
"""Provision the fixed Colab client for an existing, privately bound human.

Run as the existing trusted deploy UID. --check reads only. --provision creates
only an entirely absent authority/client pair and never repairs or overwrites a
partial pair. The binding is an operator attestation, not a new human account;
the existing active human is independently checked in Axxium's database.
The caller must admit the controller, source and image before provisioning;
this helper grants no deployment or human-session authority.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess


OWNER_UID = 1000
STATE_ROOT = Path('/srv/open-hax/state')
AUTHORITY = Path('/srv/open-hax/state/axxium/oidc/authority.json')
BINDING = Path('/srv/open-hax/state/axxium/oidc/production-binding.json')
GATEWAY_CLIENT = Path('/srv/open-hax/state/colab-mcp/axxium-client.json')
PLAN = Path(__file__).resolve().parents[1] / 'digitalocean/services/axxium/minimal-production-plan.json'
CONTROLLER_BINDING_SOURCE = Path(__file__).resolve().parents[1] / 'production-binding.json'
ISSUER = 'https://axxium.promethean.rest/oidc'
CLIENT_ID = 'knoxx-colab'
CALLBACK = 'https://knoxx.promethean.rest/colab/auth/callback'
ACTOR = re.compile(r'actor_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')

# Use the admitted application image's native crypto; no host-side RSA parser,
# package install, network access, environment credential or host volume mount.
CRYPTO = r"""
const fs = require('node:fs');
const crypto = require('node:crypto');
if (process.argv[1] === 'generate') {
  const {privateKey} = crypto.generateKeyPairSync('rsa', {modulusLength:3072,publicExponent:65537});
  process.stdout.write(JSON.stringify(privateKey.export({format:'jwk'})));
} else if (process.argv[1] === 'check') {
  const jwk = JSON.parse(fs.readFileSync(0,'utf8'));
  const key = crypto.createPrivateKey({key:jwk,format:'jwk'});
  if (key.asymmetricKeyType !== 'rsa' || key.asymmetricKeyDetails.modulusLength < 2048) process.exit(2);
  const normalized = key.export({format:'jwk'});
  for (const field of ['kty','n','e','d','p','q','dp','dq','qi']) {
    if (normalized[field] !== jwk[field]) process.exit(2);
  }
  const challenge = Buffer.from('open-hax/axxium-colab-provisioning/v1');
  const signature = crypto.sign('RSA-SHA256',challenge,key);
  if (!crypto.verify('RSA-SHA256',challenge,crypto.createPublicKey(key),signature)) process.exit(2);
  process.stdout.write('true');
} else process.exit(2);
"""


class Refusal(Exception):
    """A safe failure with no configuration values attached."""


def require_owner():
    if os.geteuid() != OWNER_UID:
        raise Refusal('The existing trusted deploy UID is required')


def no_symlinks(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise Refusal('Symbolic configuration paths are forbidden')


def private_directory(path):
    no_symlinks(path)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != OWNER_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refusal('A fixed private configuration directory has unexpected ownership or mode')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal('Duplicate configuration fields are forbidden')
        result[key] = value
    return result


def read_bytes(path, *, private=True):
    no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != OWNER_UID or info.st_size > 65536 or
                (mode != 0o600 if private else bool(mode & 0o022))):
            raise Refusal('A fixed configuration file has unexpected ownership, mode or size')
        data = os.read(fd, 65537)
        if len(data) > 65536:
            raise Refusal('Configuration exceeds its fixed size bound')
        return data
    finally:
        os.close(fd)


def read_json(path, *, private=True):
    value = json.loads(read_bytes(path, private=private), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise Refusal('Configuration must be an object')
    return value


def validate_binding(binding):
    expected = {'schema', 'issuer', 'actor_id', 'actor_kind', 'actor_status', 'evidence_sha256', 'observed_at'}
    if (not isinstance(binding, dict) or set(binding) != expected or binding['schema'] != 'axxium-colab-production-binding/v1' or
            binding['issuer'] != ISSUER or binding['actor_kind'] != 'human' or
            binding['actor_status'] != 'active' or not isinstance(binding['actor_id'], str) or
            not ACTOR.fullmatch(binding['actor_id']) or not isinstance(binding['evidence_sha256'], str) or
            not SHA256.fullmatch(binding['evidence_sha256'])):
        raise Refusal('The preexisting verified human binding is invalid')
    try:
        observed = datetime.fromisoformat(binding['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise Refusal('The human binding observation time is invalid') from None
    return binding


def load_binding():
    private_directory(BINDING.parent)
    return validate_binding(read_json(BINDING))


def existing_human_verified(binding, runner=subprocess.run):
    """Read the existing record only. Validated stable IDs cannot alter SQL."""
    actor_id = binding.get('actor_id')
    if not isinstance(actor_id, str) or not ACTOR.fullmatch(actor_id):
        return False
    query = ("SELECT EXISTS(SELECT 1 FROM actors a JOIN entities e ON e.id=a.entity_id "
             f"WHERE a.id='{actor_id}' AND e.kind='human' AND a.status='active');\n")
    try:
        result = runner(['docker', 'exec', '-i', 'axxium-axxium-db-1', 'psql',
                         '-U', 'axxium', '-d', 'axxium', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1'],
                        input=query.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=15, check=False)
        return result.returncode == 0 and result.stdout.strip() == b't'
    except (OSError, subprocess.TimeoutExpired):
        return False


def image_id():
    plan = read_json(PLAN, private=False)
    value = plan.get('image_id', '')
    if (plan.get('schema') != 'axxium-minimal-production/v1' or
            not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value)):
        raise Refusal('The admitted image plan is invalid')
    return value


def native_crypto(mode, value=None, runner=subprocess.run):
    try:
        result = runner(['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--pull', 'never',
                         '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                         '--pids-limit', '64', '--memory', '128m', '--entrypoint', 'node',
                         image_id(), '-e', CRYPTO, mode],
                        input=json.dumps(value).encode() if value is not None else b'',
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False)
        if result.returncode != 0:
            raise Refusal('Native private signing-key validation failed')
        if mode == 'generate':
            return json.loads(result.stdout, object_pairs_hook=unique_object)
        if result.stdout.strip() != b'true':
            raise Refusal('Native private signing-key validation failed')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise Refusal('Native private signing-key operation failed') from None


def validate_pair(authority, gateway, binding, crypto=None):
    # This is a deliberately narrow instance of axxium.law.oidc's closed
    # kebab-key config, not the oidc-provider's different camel/snake schema.
    if set(authority) != {'issuer', 'clients', 'cookie-keys', 'jwks'} or authority['issuer'] != ISSUER:
        raise Refusal('The authority does not match the fixed source schema')
    clients = authority['clients']
    client_fields = {'id', 'secret', 'redirect-uris', 'refresh-enabled?', 'device-enabled?'}
    if not isinstance(clients, list) or len(clients) != 1 or not isinstance(clients[0], dict):
        raise Refusal('The fixed authority requires exactly its Colab client')
    client = clients[0]
    if (set(client) != client_fields or client['id'] != CLIENT_ID or
            client['redirect-uris'] != [CALLBACK] or client['refresh-enabled?'] is not True or
            client['device-enabled?'] is not False or not isinstance(client['secret'], str) or
            not 32 <= len(client['secret']) <= 512):
        raise Refusal('The registered client does not match the fixed source schema')
    cookies = authority['cookie-keys']
    if (not isinstance(cookies, list) or not 1 <= len(cookies) <= 3 or
            any(not isinstance(key, str) or not 32 <= len(key) <= 512 for key in cookies) or
            len(set(cookies)) != len(cookies) or client['secret'] in cookies):
        raise Refusal('The private cookie-key set is invalid')
    jwks = authority['jwks']
    if not isinstance(jwks, dict) or set(jwks) != {'keys'} or not isinstance(jwks['keys'], list) or len(jwks['keys']) != 1:
        raise Refusal('The private signing-key set is invalid')
    key = jwks['keys'][0]
    fields = {'kty', 'n', 'e', 'd', 'p', 'q', 'dp', 'dq', 'qi', 'alg', 'kid', 'use'}
    if (not isinstance(key, dict) or set(key) != fields or key['kty'] != 'RSA' or
            key['alg'] != 'RS256' or key['use'] != 'sig' or
            any(not isinstance(value, str) or not value for value in key.values())):
        raise Refusal('A persistent private RS256 key is required')
    expected_gateway_fields = {'issuer', 'client_id', 'client_secret', 'allowed_subjects'}
    if (set(gateway) != expected_gateway_fields or gateway['issuer'] != ISSUER or
            gateway['client_id'] != CLIENT_ID or gateway['client_secret'] != client['secret'] or
            gateway['allowed_subjects'] != [binding['actor_id']]):
        raise Refusal('The gateway client does not match the authority and selected human')
    (crypto or native_crypto)('check', key)


def check():
    require_owner()
    binding = load_binding()
    if not existing_human_verified(binding):
        raise Refusal('The selected existing human could not be verified')
    private_directory(AUTHORITY.parent)
    private_directory(GATEWAY_CLIENT.parent)
    validate_pair(read_json(AUTHORITY), read_json(GATEWAY_CLIENT), binding)
    return {'verified': True, 'status': 'complete'}


def write_new_bytes(path, value):
    # O_EXCL/O_NOFOLLOW refuses overwrites. A crash or second-file failure
    # leaves a partial pair, which subsequent runs refuse instead of repairing.
    no_symlinks(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def write_new(path, value):
    write_new_bytes(path, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())


def make_private_directory(path):
    no_symlinks(path)
    if path != STATE_ROOT and STATE_ROOT not in path.parents:
        raise Refusal('The private directory escaped its fixed state root')
    root_info = STATE_ROOT.stat()
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != OWNER_UID:
        raise Refusal('The fixed state root has unexpected ownership')
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    private_directory(path)


def install_binding(binding_input, material_present):
    if Path(binding_input).absolute() != CONTROLLER_BINDING_SOURCE:
        raise Refusal('Only the admitted controller binding input is supported')
    incoming_bytes = read_bytes(CONTROLLER_BINDING_SOURCE)
    incoming = validate_binding(json.loads(incoming_bytes, object_pairs_hook=unique_object))
    if BINDING.exists() or BINDING.is_symlink():
        load_binding()
        if incoming_bytes != read_bytes(BINDING):
            raise Refusal('Existing human binding differs; never overwrite it')
        return
    if material_present:
        raise Refusal('Identity material exists without its binding; recovery is required')
    if not existing_human_verified(incoming):
        raise Refusal('The selected existing human could not be verified')
    make_private_directory(BINDING.parent)
    write_new_bytes(BINDING, incoming_bytes)


def provision(binding_input=None):
    require_owner()
    present = [path.exists() or path.is_symlink() for path in (AUTHORITY, GATEWAY_CLIENT)]
    if any(present) and not all(present):
        raise Refusal('Partial identity material exists; recovery is required, never overwrite it')
    if binding_input is not None:
        install_binding(binding_input, any(present))
    binding = load_binding()
    if not existing_human_verified(binding):
        raise Refusal('The selected existing human could not be verified')
    if any(present):
        result = check()
        return {**result, 'status': 'unchanged'}
    private_directory(AUTHORITY.parent)
    make_private_directory(GATEWAY_CLIENT.parent)
    client_secret = secrets.token_urlsafe(48)
    signing_key = native_crypto('generate')
    if not isinstance(signing_key, dict):
        raise Refusal('Native signing-key generation returned an invalid shape')
    signing_key.update({'alg': 'RS256', 'use': 'sig', 'kid': 'axxium-colab-' + secrets.token_hex(12)})
    authority = {'issuer': ISSUER, 'clients': [{'id': CLIENT_ID, 'secret': client_secret,
                 'redirect-uris': [CALLBACK], 'refresh-enabled?': True, 'device-enabled?': False}],
                 'cookie-keys': [secrets.token_urlsafe(48), secrets.token_urlsafe(48)],
                 'jwks': {'keys': [signing_key]}}
    gateway = {'issuer': ISSUER, 'client_id': CLIENT_ID, 'client_secret': client_secret,
               'allowed_subjects': [binding['actor_id']]}
    validate_pair(authority, gateway, binding)
    write_new(AUTHORITY, authority)
    write_new(GATEWAY_CLIENT, gateway)
    result = check()
    return {**result, 'status': 'created'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--provision', action='store_true')
    parser.add_argument('--binding-input', help='the fixed admitted controller production-binding.json')
    args = parser.parse_args()
    try:
        if args.check and args.binding_input:
            raise Refusal('Binding installation is not allowed during read-only verification')
        result = check() if args.check else provision(args.binding_input)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refusal, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'verified': False, 'status': 'refused'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
