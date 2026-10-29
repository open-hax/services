#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit first enrollment of one synthetic Stage client, after source adoption.

Run through the executing Codex account's audited sudo. This privileged helper
does not run in CI. It holds the existing environment receiver lock, preserves
its owner and intake barrier, and never opens a production authority/runtime.
Any partial pair or uncertain effect is retained for explicit recovery.
"""
import argparse
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from colab_transport_stage import ACTOR, ISSUER, STATE, private_read, require, request, run, sync_directory, validate_client

ENVIRONMENTS = Path('/srv/open-hax/environments')
SLOT = ENVIRONMENTS / 'staging/axxium'
AUTHORITY = SLOT / 'oidc/authority.json'
BACKUP = SLOT / 'oidc/authority.before-colab-transport-stage.json'
MARKER = SLOT / 'oidc/colab-transport-stage-enrollment.json'
GATEWAY = STATE / 'axxium-client.json'
LEGACY = ENVIRONMENTS / 'staging/cloud-cuckoo/config/client.json'
CONTAINER = 'axxium-staging-axxium-1'
CLIENT = 'knoxx-colab-transport-staging'
CALLBACK = 'http://127.0.0.1:18771/auth/callback'
REDIRECT = 'http://127.0.0.1:18776/callback'
OWNER_UID = 1000


def present(path):
    return path.exists() or path.is_symlink()


def no_symlinks(path):
    require(not any(item.is_symlink() for item in (path, *path.parents)))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def bytes_json(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def authority_shape(authority):
    # The incumbent has already loaded this authority through Axxium's actual
    # law/crypto adapters. Preserve all existing values, including optional node
    # grants, rather than reconstructing oidc-provider configuration here.
    required = {'issuer', 'clients', 'cookie-keys', 'jwks'}
    optional = {'allow-loopback?', 'device-flow-enabled?', 'node-grants'}
    require(isinstance(authority, dict) and required <= set(authority) <= required | optional and
            authority['issuer'] == ISSUER and authority.get('allow-loopback?') is True and
            isinstance(authority['clients'], list) and 1 <= len(authority['clients']) <= 64)
    for client in authority['clients']:
        require(isinstance(client, dict) and {'id', 'secret', 'redirect-uris'} <= set(client) <=
                {'id', 'secret', 'redirect-uris', 'refresh-enabled?', 'device-enabled?'} and
                isinstance(client['id'], str) and 1 <= len(client['id']) <= 128 and
                isinstance(client['secret'], str) and 32 <= len(client['secret']) <= 512 and
                isinstance(client['redirect-uris'], list) and 1 <= len(client['redirect-uris']) <= 16)
    require(len({c['id'] for c in authority['clients']}) == len(authority['clients']) and
            len({c['secret'] for c in authority['clients']}) == len(authority['clients']))


def plan_pair(authority, gateway, secret=None):
    authority_shape(authority)
    existing = [c for c in authority['clients'] if c['id'] == CLIENT]
    if existing or gateway is not None:
        require(len(existing) == 1 and gateway is not None, 'Partial Stage client pair requires recovery')
        client = existing[0]
        validate_client(bytes_json(gateway))
        require(client == {'id': CLIENT, 'secret': gateway['client_secret'], 'redirect-uris': [CALLBACK],
                           'refresh-enabled?': True, 'device-enabled?': False})
        return authority, gateway, False
    require(len(authority['clients']) < 64 and isinstance(secret, str) and 32 <= len(secret) <= 512 and
            secret not in {c['secret'] for c in authority['clients']} and secret not in authority['cookie-keys'])
    candidate = copy.deepcopy(authority)
    candidate['clients'].append({'id': CLIENT, 'secret': secret, 'redirect-uris': [CALLBACK],
                                'refresh-enabled?': True, 'device-enabled?': False})
    gateway = {'issuer': ISSUER, 'client_id': CLIENT, 'client_secret': secret,
               'allowed_subjects': [ACTOR], 'allowed_client_redirect_uris': [REDIRECT]}
    validate_client(bytes_json(gateway))
    require({k: v for k, v in candidate.items() if k != 'clients'} ==
            {k: v for k, v in authority.items() if k != 'clients'} and
            candidate['clients'][:-1] == authority['clients'])
    return candidate, gateway, True


def write_new(path, raw):
    no_symlinks(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, OWNER_UID, 1000)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    finally:
        os.close(fd)
    sync_directory(path.parent)


def atomic_private(path, raw):
    staged = path.with_name(path.name + '.next')
    write_new(staged, raw)
    os.replace(staged, path)
    sync_directory(path.parent)


@contextmanager
def receiver_lock():
    no_symlinks(ENVIRONMENTS / '.receiver.lock')
    fd = os.open(ENVIRONMENTS / '.receiver.lock', os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b') as lock:
        info = os.fstat(lock.fileno())
        # Existing restricted receiver identity owns this exact lock. Never
        # create a second lock or change its mode/owner to gain traversal.
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 1001 and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not present(ENVIRONMENTS / 'receiver-intake.json'), 'Existing receiver intake barrier is active')
        yield


def installed_stage():
    release = (SLOT / 'release.env').read_text()
    require(re.fullmatch(r'AXXIUM_IMAGE=sha256:[0-9a-f]{64}\n', release))
    image = release.strip().split('=', 1)[1]
    receipt = decode((SLOT / 'deployment.json').read_bytes())
    require(receipt.get('status') == 'healthy' and receipt.get('environment') == 'staging' and
            receipt.get('service') == 'axxium' and receipt.get('images') == {'AXXIUM_IMAGE': image} and
            re.fullmatch(r'[0-9a-f]{40}', receipt.get('source_sha', '')))
    records = json.loads(run(['docker', 'inspect', CONTAINER], timeout=20))
    require(isinstance(records, list) and len(records) == 1)
    value = records[0]
    config = value.get('Config', {})
    labels = config.get('Labels', {})
    env = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
    require(value.get('Name') == '/' + CONTAINER and value.get('Image') == image and
            value.get('State', {}).get('Running') is True and
            labels.get('com.docker.compose.project') == 'axxium-staging' and
            labels.get('com.docker.compose.service') == 'axxium' and
            env.get('AXXIUM_PUBLIC_BASE_URL', '').rstrip('/') == 'https://staging.axxium.promethean.rest' and
            env.get('AXXIUM_OIDC_CONFIG_FILE') == '/run/axxium/oidc/authority.json' and
            any(m.get('Type') == 'bind' and m.get('Source') == str(AUTHORITY.parent) and
                m.get('Destination') == '/run/axxium/oidc' and m.get('RW') is False for m in value.get('Mounts', [])))
    require(re.fullmatch(r'[0-9a-f]{64}', value.get('Id', '')))
    return {'container_id': value['Id'], 'image_id': image, 'source_sha': receipt['source_sha']}


def actor_verified():
    legacy = decode(private_read(LEGACY))
    require(legacy.get('issuer') == ISSUER and legacy.get('client_id') == 'knoxx-colab-staging' and
            legacy.get('allowed_subjects') == [ACTOR])
    sql = ("SELECT EXISTS(SELECT 1 FROM actors a JOIN entities e ON e.id=a.entity_id "
           f"WHERE a.id='{ACTOR}' AND e.kind='human' AND a.status='active');\n").encode()
    result = run(['docker', 'exec', '-i', 'axxium-staging-database-1', 'psql', '-U', 'axxium',
                  '-d', 'axxium', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1'], data=sql, timeout=20)
    require(result.strip() == b't', 'Existing synthetic Stage actor is unavailable')


def issuer_ready():
    status, raw = request(ISSUER + '/.well-known/openid-configuration')
    require(status == 200)
    value = decode(raw)
    require(value.get('issuer') == ISSUER and 'S256' in value.get('code_challenge_methods_supported', []) and
            'client_secret_post' in value.get('token_endpoint_auth_methods_supported', []))


def complete_pair():
    state = decode(private_read(MARKER))
    require(state.get('status') == 'complete' and state.get('schema') == 'colab-stage-enrollment/v1')
    raw = private_read(AUTHORITY)
    gateway = private_read(GATEWAY)
    require(digest(raw) == state.get('authority_sha256') and digest(gateway) == state.get('gateway_sha256') and
            digest(private_read(BACKUP)) == state.get('backup_sha256'))
    _, _, added = plan_pair(decode(raw), decode(gateway))
    require(not added)


def require_identity():
    require(os.geteuid() == 0 and Path('/proc/self/loginuid').read_text().strip() == '1002',
            'Audited executing Codex sudo identity required')


def enroll(*, check=False):
    require_identity()
    no_symlinks(AUTHORITY)
    no_symlinks(STATE)
    with receiver_lock():
        before = installed_stage()
        actor_verified()
        issuer_ready()
        if present(MARKER):
            complete_pair()
            return {'verified': True, 'added': False, 'stage_restarted': False,
                    'synthetic_actor': True, 'production_changed': False}
        require(not check and not present(BACKUP) and not present(GATEWAY), 'Stage enrollment is absent or partial')
        original = private_read(AUTHORITY)
        candidate, gateway, added = plan_pair(decode(original), None, secrets.token_urlsafe(48))
        require(added)
        if not present(STATE):
            STATE.mkdir(mode=0o700)
            os.chown(STATE, OWNER_UID, 1000)
        info = STATE.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER_UID and stat.S_IMODE(info.st_mode) == 0o700)
        new_raw, gateway_raw = bytes_json(candidate), bytes_json(gateway)
        state = {'schema': 'colab-stage-enrollment/v1', 'status': 'pending', **before,
                 'backup_sha256': digest(original), 'authority_sha256': digest(new_raw),
                 'gateway_sha256': digest(gateway_raw)}
        # Persist intent before the first private state write. Values/hashes in
        # this private marker never enter stdout or a public artifact.
        write_new(MARKER, bytes_json(state))
        write_new(BACKUP, original)
        require(private_read(BACKUP) == original and private_read(AUTHORITY) == original)
        require(installed_stage() == before)
        atomic_private(AUTHORITY, new_raw)
        write_new(GATEWAY, gateway_raw)
        require(private_read(AUTHORITY) == new_raw and private_read(GATEWAY) == gateway_raw)
        # Respect the installed Axxium shutdown budget. No image selection,
        # database, other service or stage credential is replaced.
        require(installed_stage() == before)
        run(['docker', 'restart', '--time', '610', before['container_id']], timeout=660)
        deadline = time.monotonic() + 90
        while True:
            try:
                issuer_ready()
                require(installed_stage() == before)
                break
            except Exception:
                require(time.monotonic() < deadline, 'Staging issuer did not recover')
                time.sleep(2)
        state['status'] = 'complete'
        atomic_private(MARKER, bytes_json(state))
        complete_pair()
        return {'verified': True, 'added': True, 'stage_restarted': True,
                'synthetic_actor': True, 'existing_authority_preserved': True,
                'issuer_ready': True, 'production_changed': False}


def main():
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--enroll', action='store_true')
    operation.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(enroll(check=args.check), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'verified': False, 'reason': 'Stage enrollment refused or requires explicit recovery'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
