"""Root-owned, fail-closed selector for the single active Caddy project.

The host administrator provisions /etc/open-hax/ingress and the state file.
No absent-file default exists. Transition records block every normal writer;
only a separately qualified root recovery controller may settle a transition.
"""
import json
import os
from pathlib import Path
import secrets
import re
import stat
import sys

from identity_ingress_transaction import IngressTransactionError

DIRECTORY = Path('/etc/open-hax/ingress')
NAME = 'owner.json'
PENDING = 'bootstrap-pending'
SETTLED = 'bootstrap-settled'
PROJECTS = {'caddy': 'caddy-caddy-1', 'caddy-identity': 'caddy-identity-caddy-1'}


def _read_record(directory=DIRECTORY, *, owner=0):
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        d = os.fstat(directory_fd)
        if d.st_uid != owner or d.st_mode & 0o022:
            raise IngressTransactionError('Ingress owner directory is not administrator-controlled')
        fd = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            f = os.fstat(stream.fileno())
            if not stat.S_ISREG(f.st_mode) or f.st_uid != owner or f.st_nlink != 1 or f.st_mode & 0o022 or f.st_size > 4096:
                raise IngressTransactionError('Ingress owner record is not administrator-controlled')
            value = json.load(stream)
        path_info = os.stat(directory, follow_symlinks=False)
        file_info = os.stat(NAME, dir_fd=directory_fd, follow_symlinks=False)
        if (d.st_dev, d.st_ino) != (path_info.st_dev, path_info.st_ino) or (f.st_dev, f.st_ino) != (file_info.st_dev, file_info.st_ino):
            raise IngressTransactionError('Ingress owner changed during read')
        return value
    finally:
        os.close(directory_fd)


def read_selection(directory=DIRECTORY, *, owner=0):
    value = _read_record(directory, owner=owner)
    if (not isinstance(value, dict) or set(value) != {'version', 'phase', 'project'} or
            type(value['version']) is not int or value['version'] != 1 or
            value['phase'] != 'stable' or value['project'] not in PROJECTS):
        raise IngressTransactionError('Ingress owner is absent, invalid or transitioning; recovery is required')
    return value['project'], PROJECTS[value['project']]


def _publish(lease, directory, expected, value, owner):
    if os.geteuid() != owner:
        raise IngressTransactionError('Only the administrator can change ingress ownership')
    lease.recheck()
    if _read_record(directory, owner=owner) != expected:
        raise IngressTransactionError('Ingress owner baseline changed')
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    name = '.owner-' + secrets.token_hex(16)
    try:
        before = os.fstat(directory_fd)
        path_info = os.stat(directory, follow_symlinks=False)
        if before.st_uid != owner or before.st_mode & 0o022 or (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise IngressTransactionError('Ingress owner directory changed')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=directory_fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(value, sort_keys=True) + '\n').encode())
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        lease.recheck()
        if _read_record(directory, owner=owner) != expected:
            raise IngressTransactionError('Ingress owner changed before publication')
        path_info = os.stat(directory, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise IngressTransactionError('Ingress owner directory replaced')
        os.replace(name, NAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def begin_transition(lease, directory=DIRECTORY, *, owner=0):
    """Block all normal writers before touching the incumbent."""
    before = {'version': 1, 'phase': 'stable', 'project': 'caddy'}
    transition = {'version': 1, 'phase': 'transition', 'from': 'caddy', 'to': 'caddy-identity'}
    _publish(lease, directory, before, transition, owner)


def mark_bootstrap_pending(lease, incumbent_id, directory=DIRECTORY, *, owner=0):
    """Create a durable root-owned guard before any bootstrap side effect."""
    if os.geteuid() != owner or not re.fullmatch(r'[0-9a-f]{64}', incumbent_id):
        raise IngressTransactionError('Invalid bootstrap guard identity')
    lease.recheck()
    # Also verifies trusted root-owned directory and existing owner record.
    _read_record(directory, owner=owner)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open(PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=directory_fd)
        with os.fdopen(fd, 'w') as stream:
            stream.write(incumbent_id + '\n')
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def bootstrap_guard_pending(path=DIRECTORY / PENDING, *, owner=0):
    """A permanent guard opens only with a matching root-owned completion."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return False
    from identity_ingress_transaction import _read
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        directory = os.fstat(directory_fd)
        if directory.st_uid != owner or directory.st_mode & 0o022:
            raise IngressTransactionError('Bootstrap guard directory is unsafe')
        pending, _ = _read(directory_fd, path.name, owner)
        if re.fullmatch(rb'[0-9a-f]{64}\n', pending) is None:
            raise IngressTransactionError('Bootstrap guard is unsafe or malformed')
        try:
            settled, _ = _read(directory_fd, SETTLED, owner)
        except FileNotFoundError:
            return True
        try:
            project = read_selection(path.parent, owner=owner)[0]
        except (IngressTransactionError, OSError, ValueError):
            return True
        return settled != pending + (project + '\n').encode()
    finally:
        os.close(directory_fd)


def clear_bootstrap_pending(lease, incumbent_id, target, directory=DIRECTORY, *, owner=0):
    """Publish a pre-synced terminal marker; retain the original guard forever."""
    if os.geteuid() != owner or not re.fullmatch(r'[0-9a-f]{64}', incumbent_id):
        raise IngressTransactionError('Invalid bootstrap guard identity')
    lease.recheck()
    if read_selection(directory, owner=owner)[0] != target:
        raise IngressTransactionError('Ingress selector is not settled to verified target')
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open(PENDING, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or
                    info.st_mode & 0o022 or stream.read(66) != (incumbent_id + '\n').encode()):
                raise IngressTransactionError('Bootstrap guard identity changed')
        if not bootstrap_guard_pending(directory / PENDING, owner=owner):
            return  # Idempotent re-verification of the exact same outcome.
        lease.recheck()
        current = os.stat(PENDING, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise IngressTransactionError('Bootstrap guard was replaced')
        prepared = '.bootstrap-settlement-' + secrets.token_hex(16)
        fd = os.open(prepared, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o644, dir_fd=directory_fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((incumbent_id + '\n' + target + '\n').encode())
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.fsync(directory_fd)
        lease.recheck()
        if read_selection(directory, owner=owner)[0] != target:
            raise IngressTransactionError('Ingress owner changed before bootstrap settlement')
        current = os.stat(PENDING, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise IngressTransactionError('Bootstrap guard was replaced')
        # Publication is the final fallible operation. If this rename is lost
        # after reboot, the retained guard conservatively blocks all writers.
        os.rename(prepared, SETTLED, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def settle_transition(lease, target, verify, directory=DIRECTORY, *, owner=0):
    """Settle only after the caller verifies the selected live project."""
    lease.recheck()
    if target not in PROJECTS or verify(target, PROJECTS[target]) is not True:
        raise IngressTransactionError('Ingress owner outcome has not been verified')
    lease.recheck()
    transition = {'version': 1, 'phase': 'transition', 'from': 'caddy', 'to': 'caddy-identity'}
    _publish(lease, directory, transition, {'version': 1, 'phase': 'stable', 'project': target}, owner)


def main():
    if sys.argv[1:] != ['project']:
        raise SystemExit('Expected project')
    print(read_selection()[0])


if __name__ == '__main__':
    main()
