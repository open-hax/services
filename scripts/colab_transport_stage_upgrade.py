#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""One fixed healthy19e Stage upgrade; unresolved candidate pins refuse apply.

The existing Deploy Stack queue and native source admission enclose this
controller. Preserve old/new bytes on uncertainty, never retry host effects,
and award no production or Google/human execution qualification.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

import colab_transport_stage as stage

OWNER_UID = 1000
MAX_STATE_BYTES = 128 * 1024 * 1024
MAX_STATE_FILES = 4096


class Refused(RuntimeError):
    """A protected Stage input or backup no longer matches the owned state."""


def require(value):
    if not value:
        raise Refused('Transport-stage preservation contract refused')


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER_UID and
            stat.S_IMODE(info.st_mode) == 0o700)
    return info


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER_UID and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= MAX_STATE_BYTES)
        blocks, size = [], 0
        while True:
            block = os.read(descriptor, min(65536, MAX_STATE_BYTES + 1 - size))
            if not block:
                break
            blocks.append(block)
            size += len(block)
            require(size <= MAX_STATE_BYTES)
        after = os.fstat(descriptor)
        current = path.lstat()
        require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
                (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))
        return b''.join(blocks), info
    finally:
        os.close(descriptor)


def tree_manifest(root):
    """Hash bounded private regular files without following links or aliases."""
    root = Path(root)
    require(not any(parent.is_symlink() for parent in (root, *root.parents)))
    _directory(root)
    result, size = {}, 0
    for path in sorted(root.rglob('*')):
        require(len(result) < MAX_STATE_FILES)
        relative = str(path.relative_to(root))
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            _directory(path)
            result[relative] = {'kind': 'directory', 'mode': stat.S_IMODE(info.st_mode),
                                'uid': info.st_uid, 'gid': info.st_gid}
        else:
            require(stat.S_ISREG(info.st_mode))
            raw, info = _read(path)
            size += len(raw)
            require(size <= MAX_STATE_BYTES)
            result[relative] = {'kind': 'file', 'mode': stat.S_IMODE(info.st_mode),
                                'uid': info.st_uid, 'gid': info.st_gid, 'size': len(raw),
                                'sha256': hashlib.sha256(raw).hexdigest()}
    return result


def _write(path, raw, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    sync_directory(path.parent)


def copy_tree(source, destination):
    """Materialize a fresh private tree; existing recovery bytes are never reused."""
    manifest = tree_manifest(source)
    destination.mkdir(mode=0o700)
    for relative, entry in manifest.items():
        path = destination / relative
        if entry['kind'] == 'directory':
            path.mkdir(mode=entry['mode'])
            path.chmod(entry['mode'])
        else:
            raw, _ = _read(source / relative)
            require(hashlib.sha256(raw).hexdigest() == entry['sha256'])
            _write(path, raw, entry['mode'])
    for path in sorted(destination.rglob('*'), reverse=True):
        if path.is_dir():
            sync_directory(path)
    sync_directory(destination)
    sync_directory(destination.parent)
    require(tree_manifest(source) == manifest and tree_manifest(destination) == manifest)


def snapshot_state(source, destination):
    """Verify original, backup and separate restored bytes before any cutover."""
    source, destination = Path(source), Path(destination)
    expected = tree_manifest(source)
    destination.mkdir(mode=0o700)
    copy_tree(source, destination / 'state')
    copy_tree(destination / 'state', destination / 'restored')
    require(tree_manifest(source) == expected and tree_manifest(destination / 'state') == expected and
            tree_manifest(destination / 'restored') == expected)
    raw = json.dumps(expected, sort_keys=True, separators=(',', ':')).encode()
    _write(destination / 'manifest.json', raw)
    return {'manifest': expected, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'backup_verified': True, 'restore_copy_verified': True}


PLAN_PATH = 'digitalocean/services/colab-mcp/transport-stage-upgrade-plan.json'
CANDIDATE = None
OLD = {
    'source_sha': '19e1adadb1b24eacca993de92f6370b400cf3d33',
    'image_id': 'sha256:6b107b5c77b2f3c780160bedf0fc0e11cc2b801b4639f740f9e8b690161bfc8c',
    'runtime_image_id': 'sha256:8cd5bf61555d7c1096a053715ab39b78d028958ed62f0e20555e158096d7c767',
    'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:8cd5bf61555d7c1096a053715ab39b78d028958ed62f0e20555e158096d7c767',
    'services_sha': '215fe1969b99c3bc4a10a90e110b18d9a36e950d',
    'compose_sha256': '56c036014af9dbb894fa675041c0175b9555dc847d706a6c1d2ed2408a648557',
    'plan_sha256': '5c396ae3a3a82268347e34d4cf6effed2fb3850be3463cb632ee8c8666cadc86',
    # Derived from the authenticated native40963569096 receipt and the accepted
    # producer's json.dump(sort_keys=True, indent=2) host-file serialization.
    # The apply preflight must match these exact bytes; no live read is claimed.
    'receipt_sha256': '846f661916bf94e0fcc8fcf420b0c8e4f049bf08c4e4a8f2fe5a69f6607ca7a4',
}
OLD_SELECTOR = ('COLAB_MCP_IMAGE=' + OLD['image_reference'] + '\n').encode()


def validate_plan(plan):
    require(isinstance(plan, dict) and set(plan) == {'schema', 'candidate', 'predecessor'}
            and plan['schema'] == 'colab-transport-stage-upgrade/v1' and plan['predecessor'] == OLD)
    require(CANDIDATE is not None and plan['candidate'] == CANDIDATE)
    candidate = plan['candidate']
    require(isinstance(candidate, dict) and set(candidate) == {'source_sha', 'source_pr',
        'publisher_run_id', 'publisher_attempt', 'publisher_controller_sha', 'artifact_id',
        'artifact_digest', 'image_reference', 'image_id', 'runtime_image_id', 'license_sha256', 'tests'})
    for key in ('source_sha', 'publisher_controller_sha'):
        require(isinstance(candidate[key], str) and re.fullmatch(r'[0-9a-f]{40}', candidate[key]))
    for key in ('source_pr', 'publisher_run_id', 'publisher_attempt', 'artifact_id', 'tests'):
        require(type(candidate[key]) is int and candidate[key] > 0)
    for key in ('artifact_digest', 'image_id'):
        require(isinstance(candidate[key], str) and re.fullmatch(r'sha256:[0-9a-f]{64}', candidate[key]))
    require(isinstance(candidate['image_reference'], str) and re.fullmatch(
        r'ghcr.io/open-hax/cloud-cuckoo@sha256:[0-9a-f]{64}', candidate['image_reference'])
        and candidate['runtime_image_id'] == candidate['image_reference'].split('@')[1]
        and candidate['source_sha'] != OLD['source_sha'] and candidate['image_reference'] != OLD['image_reference'])
    from colab_stage_admission import LICENSE
    require(candidate['license_sha256'] == LICENSE)
    return plan


def verify_old_receipt(raw):
    require(hashlib.sha256(raw).hexdigest() == OLD['receipt_sha256'])
    value = json.loads(raw)
    require(value.get('schema') == 'colab-transport-stage-receipt/v1' and value.get('phase') == 'staging'
        and value.get('health') == 'success' and value.get('compose_project') == stage.PROJECT
        and all(value.get(key) == OLD[key] for key in ('source_sha', 'image_id', 'runtime_image_id',
            'image_reference', 'services_sha', 'compose_sha256', 'plan_sha256'))
        and value.get('assertions') == 165
        and all(value.get(key) is True for key in ('manifest_config_binding_verified', 'synthetic_actor',
            'synthetic_upstreams', 'oauth_flow_verified', 'gateway_forwarding_verified', 'session_revoked',
            'unrelated_runtime_preserved'))
        and all(value.get(key) is False for key in ('human_login_verified', 'google_execution_verified', 'production_qualified')))
    return value


def execute(host):
    with host.lock():
        host.preflight()
        host.image()
        host.claim()
        for name in ('stop', 'snapshot', 'select', 'start', 'verify'):
            host.record(name, 'pending')
            proof = getattr(host, name)()
            host.record(name, 'succeeded')
        host.record('publish', 'pending')
        return host.publish(proof)


class Host(stage.Host):
    def __init__(self, plan, controller, checkout):
        super().__init__(plan['candidate'], controller, checkout)
        self.full_plan = plan

    def preflight(self):
        require(not self.pending.exists() and not self.pending.is_symlink())
        self.old_receipt, _ = _read(stage.RUNTIME / 'stage-receipt.json')
        verify_old_receipt(self.old_receipt)
        self.old_selector, _ = _read(self.environment)
        require(self.old_selector == OLD_SELECTOR)
        old_checkout = stage.RUNTIME / 'controllers' / OLD['services_sha']
        for path, expected in [('digitalocean/services/colab-mcp/compose.transport-stage.yaml', OLD['compose_sha256']),
                               ('digitalocean/services/colab-mcp/transport-stage-plan.json', OLD['plan_sha256'])]:
            require(hashlib.sha256(_read(old_checkout / path)[0]).hexdigest() == expected)
        require(hashlib.sha256(_read(self.compose)[0]).hexdigest() == OLD['compose_sha256'])
        stage.validate_client(_read(stage.STATE / 'axxium-client.json')[0])
        require(len(_read(stage.STATE / 'fixture-token')[0].strip()) >= 32)
        # Read only the already enrolled dedicated Stage pair. Never create a
        # client, change an authority, reload Axxium or select a production actor.
        from enroll_colab_transport_stage import complete_pair, actor_verified, issuer_ready
        complete_pair()
        actor_verified()
        issuer_ready()
        self.state_before = tree_manifest(stage.STATE)
        self.static_before = {key: self.state_before[key] for key in ('fixture-token', 'axxium-client.json')}
        self.ids = stage.run(['docker', 'ps', '-aq', '--no-trunc', '--filter',
            'label=com.docker.compose.project=' + stage.PROJECT], timeout=20).decode().split()
        require(len(self.ids) == 3 and len(set(self.ids)) == 3)
        records = json.loads(stage.run(['docker', 'inspect', *self.ids], timeout=30))
        stage.validate_containers(records, OLD)
        require(all(r['State'].get('Paused') is False and r.get('Config', {}).get('Entrypoint') is None
            and r.get('HostConfig', {}).get('PidsLimit') == 128 for r in records))
        self.old_records = {r['Id']: {'image': r['Image'], 'command': r['Config']['Cmd']} for r in records}
        self.native_old_records = records
        require(set(self.old_records) == set(self.ids))
        self.before = stage.inventory()
        require(os.statvfs(stage.RUNTIME).f_bavail * os.statvfs(stage.RUNTIME).f_frsize >= 2 * 1024 ** 3)
        memory = Path('/proc/meminfo').read_text()
        require(int(re.search(r'^MemAvailable:\s+(\d+) kB', memory, re.M)[1]) >= 1024 ** 2)

    def claim(self):
        self.transaction = stage.RUNTIME / ('upgrade-' + self.controller + '-' + secrets.token_hex(12))
        self.transaction.mkdir(mode=0o700)
        self.journal = self.transaction / 'journal.jsonl'
        _write(self.journal, b'')
        _write(self.pending, json.dumps({'schema': 'colab-stage-upgrade-pending/v1',
            'controller_sha': self.controller, 'source_sha': self.plan['source_sha'],
            'image_reference': self.plan['image_reference'], 'transaction': self.transaction.name,
            'old_container_ids': self.ids}, sort_keys=True).encode())
        _write(self.transaction / 'previous-receipt.json', self.old_receipt)
        _write(self.transaction / 'previous-environment', self.old_selector)
        _write(self.transaction / 'previous-containers.json', json.dumps(self.native_old_records, sort_keys=True).encode())
        _write(self.transaction / 'preflight-state-manifest.json', json.dumps(self.state_before, sort_keys=True).encode())

    def record(self, name, outcome):
        with self.journal.open('ab') as stream:
            stream.write(json.dumps({'step': name, 'outcome': outcome}, sort_keys=True).encode() + b'\n')
            stream.flush()
            os.fsync(stream.fileno())

    def stop(self):
        stage.run(['docker', 'stop', '--time', '10', *self.ids], timeout=45)
        records = json.loads(stage.run(['docker', 'inspect', *self.ids], timeout=30))
        require(len(records) == 3 and {r['Id'] for r in records} == set(self.ids))
        for record in records:
            require(record['State']['Running'] is False and record['Image'] == self.old_records[record['Id']]['image']
                and record['Config']['Cmd'] == self.old_records[record['Id']]['command'])

    def snapshot(self):
        # A clean stop may checkpoint or remove SQLite WAL/SHM files. Preserve
        # the quiesced logical store, not an impossible equality with its live
        # physical representation. Static fixture credentials cannot change.
        stopped = tree_manifest(stage.STATE)
        require(all(stopped.get(name) == value for name, value in self.state_before.items()
                    if not name.startswith('oauth/')))
        self.backup = snapshot_state(stage.STATE, self.transaction / 'backup')
        require(self.backup['manifest'] == stopped)
        verification = self.transaction / 'sqlite-verification'
        copy_tree(self.transaction / 'backup/restored', verification)
        for name, entry in stopped.items():
            if entry['kind'] == 'file' and name.startswith('oauth/') and name.endswith(('.db', '.sqlite3')):
                with closing(sqlite3.connect((verification / name).absolute().as_uri() + '?mode=ro', uri=True)) as database:
                    require(database.execute('PRAGMA integrity_check').fetchall() == [('ok',)])
        require(tree_manifest(self.transaction / 'backup/state') == stopped
                and tree_manifest(self.transaction / 'backup/restored') == stopped)

    def select(self):
        require(_read(self.environment)[0] == self.old_selector and tree_manifest(stage.STATE) == self.state_before)
        staged = self.transaction / 'new-environment'
        _write(staged, ('COLAB_MCP_IMAGE=' + self.plan['image_reference'] + '\n').encode())
        os.replace(staged, self.environment)
        sync_directory(stage.RUNTIME)

    def start(self):
        self.command('up', '-d', '--pull', 'never', '--no-build')

    def verify(self):
        # This is the accepted actual HTTP/OAuth/PKCE/MCP fixture smoke, including
        # exact owned-session cleanup and revocation. It never calls Google.
        proof = super().verify()
        current = tree_manifest(stage.STATE)
        require(all(current.get(key) == value for key, value in self.static_before.items()))
        require(stage.inventory() == self.before)
        ids = self.command('ps', '-q').decode().split()
        records = json.loads(stage.run(['docker', 'inspect', *ids], timeout=30))
        require(len(records) == 3 and all(r.get('Config', {}).get('Entrypoint') is None
            and r.get('State', {}).get('Paused') is False and r.get('HostConfig', {}).get('PidsLimit') == 128 for r in records))
        return proof

    def publish(self, proof):
        plan_path = self.checkout / PLAN_PATH
        receipt = {'schema': 'colab-transport-stage-receipt/v1', 'phase': 'staging', 'health': 'success',
            'source_repository': 'open-hax/cloud-cuckoo', 'source_sha': self.plan['source_sha'],
            'image_reference': self.plan['image_reference'], 'image_id': self.plan['image_id'],
            'runtime_image_id': self.plan['runtime_image_id'], 'manifest_config_binding_verified': True,
            'services_sha': self.controller, 'compose_project': stage.PROJECT,
            'compose_sha256': hashlib.sha256(_read(self.compose)[0]).hexdigest(),
            'plan_sha256': hashlib.sha256(_read(plan_path)[0]).hexdigest(),
            'synthetic_actor': True, 'synthetic_upstreams': True, 'human_login_verified': False,
            'oauth_flow_verified': True, 'gateway_forwarding_verified': True, 'session_revoked': True,
            'google_execution_verified': False, 'production_qualified': False,
            'assertions': proof['assertions'], 'unrelated_runtime_preserved': True,
            'upgraded_from_source_sha': OLD['source_sha'], 'restore_copy_verified': True,
            'completed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        raw = json.dumps(receipt, sort_keys=True, indent=2).encode()
        _write(self.transaction / 'healthy-receipt.json', raw)
        staged = self.transaction / 'receipt-to-publish.json'
        _write(staged, raw)
        # The exact prior receipt remains in this transaction's private archive.
        require(_read(stage.RUNTIME / 'stage-receipt.json')[0] == self.old_receipt)
        os.replace(staged, stage.RUNTIME / 'stage-receipt.json')
        sync_directory(stage.RUNTIME)
        self.record('publish', 'healthy')
        self.pending.unlink()
        sync_directory(stage.RUNTIME)
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
        require(checkout == stage.RUNTIME / 'controllers' / args.controller_sha)
        _directory(checkout)
        path = checkout / PLAN_PATH
        require(Path(args.plan).resolve() == path)
        raw, _ = _read(path)
        require(hashlib.sha256(raw).hexdigest() == args.plan_sha256)
        plan = validate_plan(json.loads(raw))
        print(json.dumps(execute(Host(plan, args.controller_sha, checkout)), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'health': 'failed', 'reason': 'fixed Stage upgrade refused or operational hold retained'}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
