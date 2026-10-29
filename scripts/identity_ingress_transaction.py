"""Candidate primitive for coordinated directory-mounted ingress writes.

The caller must hold the shared host lease through its entire operation and
must separately establish that every other writer uses that same lease. This
module is NOT wired to the production workflow and makes no host changes by
itself. Full Caddy Compose/image/env updates require a wider transaction.
"""
import hashlib
import os
from pathlib import Path
import secrets
import stat

MAX_CONFIG_BYTES = 2 * 1024 * 1024


class IngressTransactionError(RuntimeError):
    pass


def _read(parent_fd, name, owner):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or
                info.st_mode & 0o022 or info.st_size > MAX_CONFIG_BYTES):
            raise IngressTransactionError("Unsafe incumbent ingress configuration")
        data = source.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise IngressTransactionError("Ingress configuration too large")
        return data, stat.S_IMODE(info.st_mode)


def _replace(parent_fd, name, data, mode):
    temp = ".ingress-candidate-" + secrets.token_hex(16)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 mode, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        try:
            os.unlink(temp, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _check_directory(parent_fd, parent, owner):
    try:
        descriptor = os.fstat(parent_fd)
        path_info = os.stat(parent, follow_symlinks=False)
    except OSError as error:
        raise IngressTransactionError("Ingress directory unavailable") from error
    if (not stat.S_ISDIR(descriptor.st_mode) or not stat.S_ISDIR(path_info.st_mode) or
            descriptor.st_uid != owner or path_info.st_uid != owner or
            descriptor.st_mode & 0o022 or path_info.st_mode & 0o022 or
            (descriptor.st_dev, descriptor.st_ino) != (path_info.st_dev, path_info.st_ino)):
        raise IngressTransactionError("Unsafe ingress directory")


def create_private_journal(path: Path, *, owner=None):
    """Durably link a new private journal before any target can change."""
    owner = os.getuid() if owner is None else owner
    if not path.is_absolute() or path.name in ('', '.', '..'):
        raise IngressTransactionError('Invalid ingress journal path')
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _check_directory(parent_fd, path.parent, owner)
        os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        _check_directory(parent_fd, path.parent, owner)
    finally:
        os.close(parent_fd)


def replace_under_lease(lease, target: Path, new_bytes: bytes, expected_sha256: str | None,
                        validate_and_reload, *, owner=None, journal=None):
    """Install one config through the durable, guarded bundle transaction.

    A caller must supply a private, empty journal under the shared ingress
    journal root. The root must be checked by every cooperating writer.
    """
    if journal is None:
        raise IngressTransactionError('A private ingress recovery journal is required')
    # Late import avoids a cycle: the bundle uses this module's safe file I/O.
    from caddy_bundle_transaction import BundleItem, apply_bundle_under_lease
    try:
        apply_bundle_under_lease(lease, [BundleItem(target, new_bytes, expected_sha256)], journal,
                                 validate_and_reload, validate_and_reload, owner=owner)
    except IngressTransactionError as error:
        if 'prior bundle restored and verified' in str(error):
            raise IngressTransactionError('Ingress update failed; prior bytes restored and reloaded') from error
        if 'manual recovery' in str(error):
            raise IngressTransactionError('Ingress update failed; rollback could not be confirmed') from error
        raise
    return hashlib.sha256(new_bytes).hexdigest()
