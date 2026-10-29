"""Root-owned, fail-closed selector for the single active Caddy project.

The host administrator provisions /etc/open-hax/ingress and the state file.
No absent-file default exists. Transition records block every normal writer;
only a separately qualified root recovery controller may settle a transition.
"""
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from identity_ingress_transaction import IngressTransactionError

DIRECTORY = Path('/etc/open-hax/ingress')
NAME = 'owner.json'
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
