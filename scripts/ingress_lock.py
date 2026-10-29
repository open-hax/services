"""Fail-closed lock primitive for cooperating ingress controllers.

This module is not wired into a deployment path. Operators must provision the
stable lock inode and retire non-cooperating writers before relying on it.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

LOCK_DIRECTORY = Path('/run/open-hax/ingress')
LOCK_NAME = 'transaction.lock'


class IngressLockError(RuntimeError):
    """The ingress lock cannot safely be acquired or retained."""


def _trusted_directory(info, owner):
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == owner
            and not info.st_mode & 0o022)


def _trusted_lock(info, owner):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == owner
            and info.st_nlink == 1 and not info.st_mode & 0o022)


class HeldIngressLock:
    """A held inode; callers must recheck before each change to ingress state."""
    def __init__(self, directory, directory_fd, lock_fd, owner):
        self.directory = directory
        self.directory_fd = directory_fd
        self.lock_fd = lock_fd
        self.owner = owner

    def recheck(self):
        try:
            directory_fd_info = os.fstat(self.directory_fd)
            directory_path_info = os.stat(self.directory, follow_symlinks=False)
            lock_fd_info = os.fstat(self.lock_fd)
            lock_path_info = os.stat(LOCK_NAME, dir_fd=self.directory_fd, follow_symlinks=False)
        except OSError as error:
            raise IngressLockError('Ingress lock state unavailable') from error
        same = lambda a, b: (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
        if (not _trusted_directory(directory_fd_info, self.owner)
                or not _trusted_directory(directory_path_info, self.owner)
                or not same(directory_fd_info, directory_path_info)
                or not _trusted_lock(lock_fd_info, self.owner)
                or not _trusted_lock(lock_path_info, self.owner)
                or not same(lock_fd_info, lock_path_info)):
            raise IngressLockError('Ingress lock identity or ownership changed')


@contextmanager
def ingress_lock(directory=LOCK_DIRECTORY, *, owner=0):
    """Acquire the pre-provisioned lock without waiting; never create or unlink it.

    `directory` and `owner` are injectable for isolated tests. Production callers
    must use the default fixed path and root owner. The directory's ancestors
    must themselves be controlled by the host administrator.
    """
    directory = os.fspath(directory)
    directory_fd = lock_fd = None
    held = False
    try:
        try:
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            lock_fd = os.open(LOCK_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        except OSError as error:
            raise IngressLockError('Ingress lock cannot be opened safely') from error
        lease = HeldIngressLock(directory, directory_fd, lock_fd, owner)
        lease.recheck()
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except BlockingIOError as error:
            raise IngressLockError('Ingress transaction already in progress') from error
        lease.recheck()
        yield lease
    finally:
        if lock_fd is not None:
            if held:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if directory_fd is not None:
            os.close(directory_fd)
