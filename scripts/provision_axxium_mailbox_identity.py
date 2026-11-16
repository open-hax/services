#!/usr/bin/env python3
"""Prepare the primary production Axxium mailbox identity under the deploy lock."""
import argparse
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

import provision_axxium_colab_identity as incumbent


STATE = incumbent.STATE_ROOT / 'axxium'
ORIGIN = 'https://axxium.promethean.rest'
ISSUER = ORIGIN + '/oidc'
RESOURCE = ORIGIN + '/api/mailbox/mcp'
WORK_CLIENT = 'axxium-mailbox-chatgpt-production'
WORK_CALLBACK = 'https://chatgpt.com/connector_platform_oauth_redirect'
ROOT_ACTOR = 'did:web:axxium.promethean.rest:actors:err'
WORK_ACTOR = ROOT_ACTOR + ':work'
CODEX_ACTOR = ROOT_ACTOR + ':hosts:stealth:codex'
REQUEST_SCHEMA = 'axxium-production-mailbox-identity/v1'
CLAIM_SCHEMA = 'axxium-production-mailbox-identity-claim/v1'
WORK_MAILBOX = re.compile(r'did:plc:[a-z2-7]{24}\Z')
WORK_SECRET = re.compile(r'[A-Za-z0-9_-]{32,512}\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
MAILBOX_CAPACITY = 2 * 1024 * 1024


Refusal = incumbent.Refusal


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def json_object(data):
    value = json.loads(data, object_pairs_hook=incumbent.unique_object)
    require(isinstance(value, dict), 'Configuration must be an object')
    return value


def database_exists(sql):
    result = subprocess.run(
        ['docker', 'exec', '-i', 'axxium-axxium-db-1', 'psql', '-q', '-U', 'axxium', '-d', 'axxium',
         '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1'], input=('BEGIN READ ONLY;\n' + sql + '\nROLLBACK;\n').encode(),
        capture_output=True, timeout=15, check=False)
    return result.returncode == 0 and result.stdout.strip() == b't'


def existing_google_owner(binding):
    actor = binding['actor_id']
    require(incumbent.ACTOR.fullmatch(actor), 'Invalid owner ID')
    return database_exists(
        "SELECT EXISTS(SELECT 1 FROM actors a JOIN entities e ON e.id=a.entity_id "
        "JOIN provider_bindings p ON p.actor_id=a.id WHERE a.id='" + actor + "' "
        "AND a.status='active' AND e.kind='human' AND p.provider='google');")


def existing_atproto_work(binding, mailbox):
    actor = binding['actor_id']
    require(incumbent.ACTOR.fullmatch(actor) and WORK_MAILBOX.fullmatch(mailbox),
            'Invalid production Work account identity')
    return database_exists(
        "SELECT count(*)=1 FROM actors a JOIN provider_bindings p ON p.actor_id=a.id "
        "WHERE a.id<>'" + actor + "' AND a.status='active' AND p.provider='atproto' "
        "AND p.subject='" + mailbox + "';")


def authority_with_work(original, request):
    require(set(original) == {'issuer', 'clients', 'cookie-keys', 'jwks'} and original['issuer'] == ISSUER,
            'Incumbent authority schema or issuer differs')
    require(isinstance(original['clients'], list) and len(original['clients']) == 1,
            'Incumbent authority must contain its one registered Colab client')
    prior = original['clients'][0]
    require(isinstance(prior, dict) and prior.get('id') == incumbent.CLIENT_ID and
            prior.get('redirect-uris') == [incumbent.CALLBACK] and
            prior.get('refresh-enabled?') is True and prior.get('device-enabled?') is False and
            isinstance(prior.get('secret'), str), 'Incumbent Colab registration differs')
    secret = request['work_client_secret']
    require(secret != prior['secret'] and secret not in original['cookie-keys'],
            'The Work client requires its own secret')
    work = {'id': WORK_CLIENT, 'secret': secret, 'redirect-uris': [WORK_CALLBACK],
            'refresh-enabled?': True, 'device-enabled?': False}
    return {**original, 'clients': [prior, work], 'mailbox-client-id': WORK_CLIENT,
            'mailbox-resource': RESOURCE, 'mailbox-actor-messaging?': True,
            'mailbox-owner-consent?': True, 'mailbox-inbox?': True}


def require_live_lock(lock_fd, operation, plan_sha, source):
    require(type(lock_fd) is int and lock_fd >= 0, 'The inherited production lock is required')
    incumbent.no_symlinks(incumbent.LOCK)
    current = incumbent.LOCK.lstat()
    held = os.fstat(lock_fd)
    require(stat.S_ISREG(current.st_mode) and current.st_uid == incumbent.OWNER_UID and
            current.st_nlink == 1 and stat.S_IMODE(current.st_mode) == 0o600 and
            (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino),
            'The production lock identity differs')
    fd = os.open(incumbent.LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise Refusal('The production lock is not held')
    finally:
        os.close(fd)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Refusal('The inherited open file does not own the production lock') from None
    incumbent.private_directory(operation)
    expected = {'schema': 'axxium-production-pending/v1', 'operation': str(operation),
                'source_sha': source, 'plan_sha256': plan_sha}
    require(incumbent.read_json(incumbent.PENDING) == expected, 'The production hold differs')


def openssl(args, data=None):
    result = subprocess.run(['/usr/bin/openssl', *args], input=data, capture_output=True, timeout=10,
                            env={'LC_ALL': 'C'}, check=False)
    require(result.returncode == 0, 'Actor signing key verification failed')
    return result.stdout


def prepared_key(path, generate):
    stage = incumbent.stage_path(path)
    for existing in (stage, path):
        if incumbent.present(existing):
            return incumbent.read_bytes(existing, max_links=2)
    return generate()


def signing_key(path):
    data = prepared_key(path, lambda: openssl(['genpkey', '-algorithm', 'ED25519']))
    require(data.startswith(b'-----BEGIN PRIVATE KEY-----\n') and
            b'ED25519 Public-Key:' in openssl(['pkey', '-text_pub', '-noout'], data),
            'Actor signing key must be an Ed25519 private PEM')
    incumbent.publish_identity_file(path, data)


def symmetric_key(path):
    data = prepared_key(path, lambda: base64.b64encode(secrets.token_bytes(32)) + b'\n')
    try:
        key = base64.b64decode(data.strip(), validate=True)
    except binascii.Error:
        raise Refusal('Invalid prepared symmetric key') from None
    require(len(key) == 32 and data == base64.b64encode(key) + b'\n', 'Invalid prepared symmetric key')
    incumbent.publish_identity_file(path, data)


def check_ledger(path, capacity):
    incumbent.private_directory(path.parent)
    incumbent.no_symlinks(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == incumbent.OWNER_UID and
            info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and
            (capacity is None or info.st_size <= capacity), 'Persistent mailbox ledger protection or capacity differs')


def compose_environment(identity, binding, mailbox):
    settings = {'AXXIUM_PRODUCTION_MAILBOX_IDENTITY_PATH': str(identity),
                'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': binding['actor_id'],
                'AXXIUM_IDENTITY_ROOT_DID': ROOT_ACTOR,
                'AXXIUM_MAILBOX_ACTOR_BINDINGS': json_bytes([[mailbox, WORK_ACTOR]]).decode().strip(),
                'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': mailbox,
                'AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS': CODEX_ACTOR}
    return ''.join(f"{name}='{value}'\n" for name, value in settings.items()).encode()


def provision(plan_path, input_path, seed_path, operation, *, prepare=False, lock_fd=None):
    incumbent.require_owner()
    plan_bytes = incumbent.read_bytes(plan_path, private=False)
    plan = json_object(plan_bytes)
    pin = plan.get('identity', {})
    source = plan.get('source_sha')
    require(isinstance(source, str) and COMMIT.fullmatch(source) and operation == STATE / 'operations' / source,
            'The production operation does not match the admitted source')
    require(isinstance(pin, dict) and set(pin) == {'incumbent_authority_sha256', 'seed_sha256', 'work_client_id'}
            and pin['work_client_id'] == WORK_CLIENT, 'The protected mailbox identity pin differs')
    request_bytes = incumbent.read_bytes(input_path)
    request = json_object(request_bytes)
    require(set(request) == {'schema', 'work_mailbox_did', 'work_client_secret'} and
            request['schema'] == REQUEST_SCHEMA and isinstance(request['work_mailbox_did'], str) and
            WORK_MAILBOX.fullmatch(request['work_mailbox_did']) and isinstance(request['work_client_secret'], str) and
            WORK_SECRET.fullmatch(request['work_client_secret']), 'The protected Work registration input differs')
    seed = incumbent.read_bytes(seed_path, private=False)
    authority = incumbent.read_bytes(incumbent.AUTHORITY)
    require(seed and pin['seed_sha256'] == digest(seed) and pin['incumbent_authority_sha256'] == digest(authority),
            'The incumbent authority or owner seed does not match the plan')
    binding = incumbent.load_binding()
    require(existing_google_owner(binding), 'The existing production Google owner could not be verified')
    require(existing_atproto_work(binding, request['work_mailbox_did']),
            'The exact distinct production Work AT Protocol account could not be verified')
    candidate = json_bytes(authority_with_work(json_object(authority), request))
    plan_sha = digest(plan_bytes)
    if prepare:
        require_live_lock(lock_fd, operation, plan_sha, source)
    identity = operation / 'mailbox-identity'
    claim = identity / 'preparation.json'
    claim_bytes = json_bytes({'schema': CLAIM_SCHEMA, 'plan_sha256': plan_sha,
                             'registration_sha256': digest(request_bytes),
                             'owner_sha256': digest(json_bytes(binding))})
    ledgers = [(STATE / 'identity/ledger.edn', None), (STATE / 'mailbox/ledger.edn', MAILBOX_CAPACITY)]
    claimed = incumbent.present(claim)
    if claimed:
        incumbent.private_directory(identity)
        require(incumbent.read_bytes(claim, max_links=2) == claim_bytes,
                'Existing identity preparation claim differs')
    files = {claim: claim_bytes, identity / 'oidc/authority.json': candidate,
             identity / 'owner/foresight-tree.edn': seed,
             identity / 'mailbox.env': compose_environment(identity, binding, request['work_mailbox_did'])}
    for path, contents in files.items():
        incumbent.verify_publication_state(path, contents)
    for ledger, capacity in ledgers:
        stage = incumbent.stage_path(ledger)
        if incumbent.present(stage):
            require(claimed, 'Existing persistent ledger lacks the admitted identity claim')
            incumbent.private_directory(ledger.parent)
            incumbent.verify_publication_state(ledger, b'')
        elif incumbent.present(ledger):
            require(claimed, 'Existing persistent ledger lacks the admitted identity claim')
            check_ledger(ledger, capacity)
    if not prepare:
        return {'verified': True, 'prepared': False}
    for directory in (identity, identity / 'oidc', identity / 'owner'):
        incumbent.make_private_directory(directory)
    for path, contents in files.items():
        incumbent.publish_identity_file(path, contents)
    signing_key(identity / 'actor-signing.pem')
    symmetric_key(identity / 'owner/identity.key')
    symmetric_key(identity / 'oidc/mailbox-webhook.key')
    for ledger, capacity in ledgers:
        incumbent.make_private_directory(ledger.parent)
        if incumbent.present(incumbent.stage_path(ledger)) or not incumbent.present(ledger):
            incumbent.publish_identity_file(ledger, b'')
        check_ledger(ledger, capacity)
    return {'verified': True, 'prepared': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--preflight', action='store_true')
    action.add_argument('--prepare', action='store_true')
    for name in ('plan', 'input', 'seed', 'operation'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--lock-fd', type=int)
    args = parser.parse_args()
    try:
        result = provision(args.plan, args.input, args.seed, args.operation,
                           prepare=args.prepare, lock_fd=args.lock_fd)
    except Refusal as error:
        print(json.dumps({'verified': False, 'reason': str(error)}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'verified': False, 'reason': 'The protected identity inputs could not be verified'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
