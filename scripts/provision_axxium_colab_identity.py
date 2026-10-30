#!/usr/bin/env python3
"""Provision the fixed Colab client for an existing, privately bound human.

Run as the existing trusted deploy UID. --check and --preflight read only.
--provision resumes only its own durable transaction and never overwrites an
existing or unowned partial configuration. The binding is an operator attestation, not a new human account;
the existing active human is independently checked in Axxium's database.
The caller must admit the controller, source and image before provisioning;
this helper grants no deployment or human-session authority.
"""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
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
APPROVED_ACTOR = 'actor_ed43270c-5c06-43e6-9667-4c585fa63430'
SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
RUNTIME = Path('/srv/open-hax/services/axxium')
LOCK = RUNTIME / '.axxium-production.lock'
PENDING = RUNTIME / '.axxium-production-pending'
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


def read_bytes(path, *, private=True, max_links=1):
    no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != OWNER_UID or info.st_size > 65536 or
                not 1 <= info.st_nlink <= max_links or
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
            not ACTOR.fullmatch(binding['actor_id']) or binding['actor_id'] != APPROVED_ACTOR or
            not isinstance(binding['evidence_sha256'], str) or
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
        sync_directory(directory.parent)
    private_directory(path)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def require_transaction_context(lock_fd, operation_path):
    """Refuse direct provisioning without the controller's live lock and backup."""
    if type(lock_fd) is not int or lock_fd < 0:
        raise Refusal('The live controller lock is required')
    no_symlinks(LOCK)
    current = LOCK.lstat()
    held = os.fstat(lock_fd)
    if (not stat.S_ISREG(current.st_mode) or current.st_uid != OWNER_UID or
            current.st_nlink != 1 or stat.S_IMODE(current.st_mode) != 0o600 or
            (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
        raise Refusal('The controller lock identity is invalid')
    probe = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise Refusal('The controller lock is not held')
    finally:
        os.close(probe)
    try:
        # A separate opener being blocked proves some lock exists. Reacquiring
        # through the inherited open-file description proves it is that lock.
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Refusal('The inherited controller lock is not held') from None
    expected = STATE_ROOT / 'axxium' / 'operations' / SOURCE
    if operation_path is None or Path(operation_path).absolute() != expected:
        raise Refusal('The fixed production operation is required')
    private_directory(expected)
    marker = read_json(PENDING)
    plan_digest = hashlib.sha256(read_bytes(PLAN, private=False)).hexdigest()
    if marker != {'schema': 'axxium-production-pending/v1', 'operation': str(expected),
                  'source_sha': SOURCE, 'plan_sha256': plan_digest}:
        raise Refusal('The production hold does not match this operation')
    phase_sequences = []
    for phase, status in [('verified_before_backup', 'succeeded'), ('provision_identity', 'pending')]:
        files = list(expected.glob('*-' + phase + '-' + status + '.json'))
        if len(files) != 1:
            raise Refusal('The required controller phase is absent')
        record = read_json(files[0])
        match = re.fullmatch(r'([0-9]+)-' + phase + '-' + status + r'\.json', files[0].name)
        if (not match or type(record.get('sequence')) is not int or
                record['sequence'] != int(match.group(1)) or
                record.get('phase') != phase or record.get('status') != status):
            raise Refusal('The controller phase is invalid')
        phase_sequences.append(record['sequence'])
    records = [int(match.group(1)) for path in expected.iterdir()
               if (match := re.fullmatch(r'([0-9]+)-[a-z][a-z0-9_]*-(?:pending|succeeded|known_failure|healthy|rolled_back)\.json', path.name))]
    if (not records or len(records) != len(set(records)) or
            not phase_sequences[0] < phase_sequences[1] or phase_sequences[1] != max(records)):
        raise Refusal('The controller phase order is invalid')
    backup = read_json(expected / 'before-backup.json')
    dump = expected / 'before.pgdump'
    if (backup.get('path') != str(dump) or not isinstance(backup.get('sha256'), str)
            or not SHA256.fullmatch(backup['sha256']) or not isinstance(backup.get('database'), str)
            or not re.fullmatch(r'axxium_verify_[0-9a-f]{24}', backup['database'])):
        raise Refusal('The verified backup record is invalid')
    no_symlinks(dump)
    fd = os.open(dump, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != OWNER_UID or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise Refusal('The verified backup has unexpected protection')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    finally:
        os.close(fd)
    if actual != backup['sha256']:
        raise Refusal('The verified backup bytes changed')


def present(path):
    return path.exists() or path.is_symlink()


def stage_path(path):
    return path.parent / ('.' + path.name + '.provision-stage')


def transaction_path():
    return BINDING.parent / '.axxium-colab-provision-transaction.json'


def encoded_json(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def verify_publication_state(path, data):
    """Read-only proof that existing target/stage are exact owned transaction bytes."""
    stage = stage_path(path)
    target_info = path.lstat() if present(path) else None
    stage_info = stage.lstat() if present(stage) else None
    same = (target_info is not None and stage_info is not None and
            (target_info.st_dev, target_info.st_ino) == (stage_info.st_dev, stage_info.st_ino))
    if target_info is not None:
        if read_bytes(path, max_links=2) != data or (target_info.st_nlink != 1 and not same):
            raise Refusal('Existing identity material differs; never overwrite it')
    if stage_info is not None:
        if (read_bytes(stage, max_links=2) != data or
                (stage_info.st_nlink != 1 and not same) or
                (target_info is not None and not same)):
            raise Refusal('Incomplete or conflicting staging requires manual recovery')


def requested_binding(binding_input):
    if binding_input is None:
        data = read_bytes(BINDING)
    else:
        if Path(binding_input).absolute() != CONTROLLER_BINDING_SOURCE:
            raise Refusal('Only the admitted controller binding input is supported')
        data = read_bytes(CONTROLLER_BINDING_SOURCE)
    binding = validate_binding(json.loads(data, object_pairs_hook=unique_object))
    if not existing_human_verified(binding):
        raise Refusal('The selected existing human could not be verified')
    return data, binding


def transaction_contents(path, binding_bytes, binding):
    journal = json.loads(read_bytes(path, max_links=2), object_pairs_hook=unique_object)
    if (not isinstance(journal, dict) or set(journal) != {'schema', 'binding', 'authority', 'gateway'}
            or journal['schema'] != 'axxium-colab-provision-transaction/v1'):
        raise Refusal('The provisioning transaction is invalid')
    try:
        values = [base64.b64decode(journal[key], validate=True)
                  for key in ('binding', 'authority', 'gateway')]
        if values[0] != binding_bytes:
            raise Refusal('The transaction binding differs from the approved input')
        authority = json.loads(values[1], object_pairs_hook=unique_object)
        gateway = json.loads(values[2], object_pairs_hook=unique_object)
        validate_pair(authority, gateway, binding)
    except (ValueError, TypeError, KeyError):
        raise Refusal('The provisioning transaction is invalid') from None
    return dict(zip((BINDING, AUTHORITY, GATEWAY_CLIENT), values))


def publish_identity_file(path, data):
    """Publish a complete fsynced file without replacing any existing inode."""
    stage = stage_path(path)
    no_symlinks(stage)
    if present(path) and present(stage):
        current, staged = path.lstat(), stage.lstat()
        if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
            if read_bytes(stage, max_links=2) != data:
                raise Refusal('Published transaction bytes changed')
            stage.unlink()
            sync_directory(stage.parent)
        else:
            raise Refusal('Conflicting staging material requires manual recovery')
    if present(path):
        if read_bytes(path) != data:
            raise Refusal('Existing identity material differs; never overwrite it')
        return
    if present(stage):
        # A validated durable transaction makes this dedicated private staging
        # file recoverable even if its previous write was interrupted.
        info = stage.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != OWNER_UID or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise Refusal('Unowned staging material requires manual recovery')
        if read_bytes(stage) != data:
            raise Refusal('Incomplete or conflicting staging requires manual recovery')
    if not present(stage):
        write_new_bytes(stage, data)
    try:
        os.link(stage, path, follow_symlinks=False)
    except FileExistsError:
        if read_bytes(path) != data:
            raise Refusal('Existing identity material differs; never overwrite it') from None
    sync_directory(path.parent)
    if read_bytes(stage, max_links=2) != data:
        raise Refusal('Staged transaction bytes changed')
    stage.unlink()
    sync_directory(stage.parent)


def preflight(binding_input):
    """Validate the protected binding and current state without writing."""
    require_owner()
    binding_bytes, binding = requested_binding(binding_input)
    journal, unfinished = transaction_path(), stage_path(transaction_path())
    if present(journal) or present(unfinished):
        path = journal if present(journal) else unfinished
        values = transaction_contents(path, binding_bytes, binding)
        verify_publication_state(journal, read_bytes(path, max_links=2))
        for target, expected in values.items():
            verify_publication_state(target, expected)
        return {'verified': True, 'status': 'recoverable'}
    if any(present(stage_path(target)) for target in (BINDING, AUTHORITY, GATEWAY_CLIENT)):
        raise Refusal('Unowned staging material requires manual recovery')
    materials = [present(AUTHORITY), present(GATEWAY_CLIENT)]
    if any(materials) and (not all(materials) or not present(BINDING)):
        raise Refusal('Partial identity material exists; recovery is required, never overwrite it')
    if present(BINDING) and read_bytes(BINDING) != binding_bytes:
        raise Refusal('Existing human binding differs; never overwrite it')
    if all(materials):
        private_directory(AUTHORITY.parent)
        private_directory(GATEWAY_CLIENT.parent)
        validate_pair(read_json(AUTHORITY), read_json(GATEWAY_CLIENT), binding)
    return {'verified': True, 'status': 'complete' if all(materials) else 'ready'}


def provision(binding_input=None, *, lock_fd=None, operation_path=None, require_existing=False):
    require_owner()
    require_transaction_context(lock_fd, operation_path)
    state = preflight(binding_input)
    if require_existing:
        # Interrupted rollout handoffs are authorized to reuse the complete
        # identity only. Never create or repair credential material here.
        if state != {'verified': True, 'status': 'complete'}:
            raise Refusal('A complete existing identity is required')
        return {**check(), 'status': 'unchanged'}
    binding_bytes, binding = requested_binding(binding_input)
    journal, unfinished = transaction_path(), stage_path(transaction_path())
    if state['status'] == 'complete' and not present(journal) and not present(unfinished):
        return {**check(), 'status': 'unchanged'}
    make_private_directory(BINDING.parent)
    make_private_directory(GATEWAY_CLIENT.parent)
    if present(journal) or present(unfinished):
        selected = journal if present(journal) else unfinished
        values = transaction_contents(selected, binding_bytes, binding)
        publish_identity_file(journal, read_bytes(selected, max_links=2))
    else:
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
        values = {BINDING: binding_bytes, AUTHORITY: encoded_json(authority), GATEWAY_CLIENT: encoded_json(gateway)}
        record = {'schema': 'axxium-colab-provision-transaction/v1', **{
            key: base64.b64encode(values[path]).decode('ascii') for key, path in
            [('binding', BINDING), ('authority', AUTHORITY), ('gateway', GATEWAY_CLIENT)]}}
        publish_identity_file(journal, encoded_json(record))
    for target, data in values.items():
        publish_identity_file(target, data)
    result = check()
    journal.unlink()
    sync_directory(journal.parent)
    return {**result, 'status': 'created'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--preflight', action='store_true')
    mode.add_argument('--provision', action='store_true')
    parser.add_argument('--binding-input', help='the fixed admitted controller production-binding.json')
    parser.add_argument('--lock-fd', type=int, help='inherited held native controller lock')
    parser.add_argument('--operation-path', help='fixed native operation path with verified backup')
    parser.add_argument('--require-existing', action='store_true', help='reuse only a complete existing identity')
    args = parser.parse_args()
    try:
        if args.check and (args.binding_input or args.lock_fd is not None or args.operation_path or args.require_existing):
            raise Refusal('Binding installation is not allowed during read-only verification')
        if args.preflight and (not args.binding_input or args.lock_fd is not None or args.operation_path or args.require_existing):
            raise Refusal('Preflight requires only the fixed binding input')
        result = (check() if args.check else preflight(args.binding_input) if args.preflight else
                  provision(args.binding_input, lock_fd=args.lock_fd, operation_path=args.operation_path,
                            require_existing=args.require_existing))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refusal, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'verified': False, 'status': 'refused'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
