#!/usr/bin/env python3
"""Provision fixed ingress control files without replacing existing inodes.

The two directories must already be administrator-owned and not writable by
the deploy account. This does not install a Docker access boundary, a recovery
service, or any runtime files. Do not run until old ingress writers are blocked.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from caddy_bootstrap_candidate import production_plan
from identity_ingress_transaction import IngressTransactionError
from ingress_owner import read_selection

SELECTOR_DIR = Path('/etc/open-hax/ingress')
LOCK_DIR = Path('/run/open-hax/ingress')


def check_dir(path, owner):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    entry = os.stat(path, follow_symlinks=False)
    if (info.st_uid != owner or info.st_mode & 0o022 or
            (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino)):
        os.close(fd)
        raise IngressTransactionError('Ingress control directory is unsafe')
    return fd


def create_exclusive(directory_fd, name, content, owner):
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o644, dir_fd=directory_fd)
    except FileExistsError:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or
                    info.st_mode & 0o022):
                raise IngressTransactionError('Existing ingress control file is unsafe')
        finally:
            os.close(fd)
        return False
    with os.fdopen(fd, 'wb') as out:
        out.write(content)
        out.flush()
        os.fchmod(out.fileno(), 0o644)
        os.fsync(out.fileno())
    os.fsync(directory_fd)
    return True


def provision(selector_dir, lock_dir, *, owner, validate_incumbent):
    if os.geteuid() != owner:
        raise IngressTransactionError('Only the ingress administrator may provision controls')
    selector_fd = check_dir(selector_dir, owner)
    try:
        lock_fd = check_dir(lock_dir, owner)
        try:
            if (selector_dir / 'bootstrap-pending').exists() or (selector_dir / 'bootstrap-pending').is_symlink():
                raise IngressTransactionError('Bootstrap guard requires recovery')
            existing = (selector_dir / 'owner.json').exists() or (selector_dir / 'owner.json').is_symlink()
            if existing:
                read_selection(selector_dir, owner=owner)
            elif validate_incumbent() is not True:
                raise IngressTransactionError('Current incumbent was not positively verified')
            created_lock = create_exclusive(lock_fd, 'transaction.lock', b'', owner)
            created_owner = create_exclusive(selector_fd, 'owner.json',
                b'{"version":1,"phase":"stable","project":"caddy"}\n', owner)
            read_selection(selector_dir, owner=owner)
            return {'created_lock': created_lock, 'created_owner': created_owner}
        finally:
            os.close(lock_fd)
    finally:
        os.close(selector_fd)


def verify_incumbent():
    def inspect(name):
        result = subprocess.run(['sudo', '-n', '-u', 'deploy', 'docker', 'inspect', name],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return result
    incumbent = inspect('caddy-caddy-1')
    if incumbent.returncode:
        return False
    try:
        plan = production_plan(json.loads(incumbent.stdout)[0])
    except (ValueError, KeyError, IndexError, IngressTransactionError):
        return False
    # An existing new-project name makes initialization ambiguous. Docker
    # inspect failure is not taken as proof of absence: classify via `ps`.
    listing = subprocess.run(['sudo', '-n', '-u', 'deploy', 'docker', 'ps', '-aq',
                              '--filter', 'name=^/caddy-identity-caddy-1$'],
                             check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    return bool(plan.incumbent_id) and not listing.stdout.strip()


def main():
    if os.geteuid() != 0 or sys.argv[1:] != ['--provision']:
        raise SystemExit('Root and explicit --provision are required')
    print(json.dumps(provision(SELECTOR_DIR, LOCK_DIR, owner=0, validate_incumbent=verify_incumbent), sort_keys=True))


if __name__ == '__main__':
    main()
