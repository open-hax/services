#!/usr/bin/env python3
"""Exercise the real handoff primitive with a disposable PostgreSQL backup.

This runs only inside the staging harness against its newly created database.
It does not exercise the controller adoption path or prove old-image rollback.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import axxium_interrupted_handoff as handoff

APP_IMAGE = 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
DB_IMAGE = 'sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
LABEL = 'codex.staging-test.id'


def inspect_lines(arguments, count):
    result = subprocess.run(['docker', *arguments], check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=20, text=True)
    lines = result.stdout.splitlines()
    check(len(lines) == count, 'Fixture metadata unavailable')
    return [json.loads(line) for line in lines]


def verify_fixture(db, app, network, run):
    """Verify projected, nonsecret ownership and isolation before pg_dump."""
    for item in (db, app, network):
        check(bool(re.fullmatch(r'[0-9a-f]{64}', item)), 'An exact Docker ID is required')
    check(bool(re.fullmatch(r'[0-9a-f]{32}', run)), 'An exact run ID is required')
    check(len({db, app, network}) == 3, 'Fixture IDs must be distinct')
    network_name = 'axxium-oidc-test-' + run
    container_template = '\n'.join('{{json ' + field + '}}' for field in (
        '.Id', '.Name', '.Image', '.Config.Labels', '.NetworkSettings.Networks',
        '.Mounts', '.HostConfig.PortBindings', '.NetworkSettings.Ports',
        '.HostConfig.PublishAllPorts'))
    for ident, name, image in ((db, 'axxium-oidc-db-' + run, DB_IMAGE),
                               (app, 'axxium-oidc-app-' + run, APP_IMAGE)):
        values = inspect_lines(['inspect', '--format', container_template, ident], 9)
        actual, cname, actual_image, labels, networks, mounts, bindings, ports, publish_all = values
        check(actual == ident and cname == '/' + name and actual_image == image,
              'Fixture container identity differs')
        check(isinstance(labels, dict) and labels.get(LABEL) == run, 'Fixture container label differs')
        check(isinstance(networks, dict) and set(networks) == {network_name},
              'Fixture container network differs')
        check(isinstance(networks[network_name], dict) and
              networks[network_name].get('NetworkID') == network, 'Fixture network ID differs')
        check(isinstance(mounts, list) and all(isinstance(item, dict) and item.get('Type') == 'tmpfs'
              for item in mounts), 'Fixture container has a host mount')
        check(bindings in (None, {}) and publish_all is False and
              (ports is None or isinstance(ports, dict) and all(value in (None, []) for value in ports.values())),
              'Fixture container publishes ports')
    network_template = '\n'.join('{{json ' + field + '}}' for field in (
        '.Id', '.Name', '.Internal', '.Labels', '.Containers'))
    actual, name, internal, labels, peers = inspect_lines(
        ['network', 'inspect', '--format', network_template, network], 5)
    check(actual == network and name == network_name and internal is True and
          isinstance(labels, dict) and labels.get(LABEL) == run and
          isinstance(peers, dict) and set(peers) == {app, db},
          'Fixture network ownership or peers differ')


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    check(os.environ.get('GITHUB_ACTIONS') == 'true' and
          os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and
          not Path('/srv/open-hax').exists(),
          'A separate empty GitHub-hosted VM is required')
    parser = argparse.ArgumentParser()
    parser.add_argument('--db-id', required=True)
    parser.add_argument('--app-id', required=True)
    parser.add_argument('--network-id', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    db = args.db_id
    verify_fixture(db, args.app_id, args.network_id, args.run_id)
    dump = subprocess.run(['docker', 'exec', db, 'pg_dump', '-U', 'fixture',
                           '-d', 'fixture', '-Fc'], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60).stdout
    subprocess.run(['docker', 'exec', '-i', db, 'pg_restore', '--list'],
                   input=dump, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, timeout=60)
    with tempfile.TemporaryDirectory(prefix='axxium-handoff-fixture-') as directory:
        root = Path(directory)
        operations = root / 'operations'
        operations.mkdir(mode=0o700)
        old = operations / handoff.SOURCE
        old.mkdir(mode=0o700)
        backup = old / 'before.pgdump'
        write_private(backup, dump)
        checksum = hashlib.sha256(dump).hexdigest()
        write_private(old / 'before-backup.json', json.dumps({
            'path': str(backup), 'sha256': checksum, 'database': 'fixture'}).encode())
        write_private(old / '008-candidate_rollback_rehearsal-pending.json', b'{"fixture":true}\n')
        hold = root / 'pending'
        hold_bytes = b'{"synthetic":"held"}\n'
        write_private(hold, hold_bytes)
        lock_path = root / 'lock'
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checks = []
            def recheck():
                check(hold.read_bytes() == hold_bytes, 'Fixture hold changed')
                rival = os.open(lock_path, os.O_RDWR)
                try:
                    try:
                        fcntl.flock(rival, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        checks.append('held')
                    else:
                        raise AssertionError('Competing lock unexpectedly succeeded')
                finally:
                    os.close(rival)
            snapshots = {'incumbent.compose.yaml': b'synthetic compose\n',
                         'incumbent.env': b'synthetic environment\n',
                         'incumbent.Caddyfile': b'synthetic ingress\n'}
            plan = b'{"synthetic":"plan"}\n'
            receipt = handoff.handoff_files(operations=operations, pending=hold,
                expected_hold=hold_bytes, plan=plan, snapshots=snapshots, recheck=recheck)
            archive = operations / (handoff.SOURCE + '.interrupted.' + handoff.RUN)
            check(checks and hold.read_bytes() == hold_bytes, 'Hold or lock verification failed')
            check(receipt['backup_sha256'] == checksum, 'Backup digest changed')
            check(receipt['archived_backup_path'] == str(archive / 'before.pgdump'), 'Archive path differs')
            check((archive / 'before.pgdump').read_bytes() == dump, 'Backup bytes changed')
            check((archive / '008-candidate_rollback_rehearsal-pending.json').exists(), 'Journal missing')
            check((old / 'plan.json').read_bytes() == plan, 'Plan differs')
            check(json.loads((old / 'parent-handoff.json').read_bytes()) == receipt, 'Manifest differs')
        finally:
            os.close(lock_fd)
    print('PASS synthetic handoff of verified disposable PostgreSQL backup')


if __name__ == '__main__':
    main()
