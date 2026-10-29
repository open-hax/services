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


def replace_under_lease(lease, target: Path, new_bytes: bytes, expected_sha256: str | None,
                        validate_and_reload, *, owner=None):
    """Install one config; on error restore only our exact bytes.

    `validate_and_reload()` must raise on either failure; after rollback it is
    called again to re-validate and reload the restored config. A competing
    non-cooperating write refuses rollback instead of erasing that newer state.
    A fixed production dispatcher must choose target and verify the mounted
    directory/container. Do not pass paths or callbacks from MCP input.
    """
    owner = os.getuid() if owner is None else owner
    if not target.is_absolute() or not isinstance(new_bytes, bytes) or len(new_bytes) > MAX_CONFIG_BYTES:
        raise IngressTransactionError("Invalid ingress target or candidate")
    parent = target.parent
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _check_directory(parent_fd, parent, owner)
        lease.recheck()
        try:
            before, mode = _read(parent_fd, target.name, owner)
        except FileNotFoundError:
            before, mode = None, 0o644
        actual_sha256 = hashlib.sha256(before).hexdigest() if before is not None else None
        if actual_sha256 != expected_sha256:
            raise IngressTransactionError("Ingress baseline changed")
        lease.recheck()
        _check_directory(parent_fd, parent, owner)
        _replace(parent_fd, target.name, new_bytes, mode)
        try:
            lease.recheck()
            _check_directory(parent_fd, parent, owner)
            validate_and_reload()
            _check_directory(parent_fd, parent, owner)
            current, _ = _read(parent_fd, target.name, owner)
            if current != new_bytes:
                raise IngressTransactionError("Ingress changed during validation")
        except Exception as error:
            try:
                lease.recheck()
                _check_directory(parent_fd, parent, owner)
                current, _ = _read(parent_fd, target.name, owner)
                if current != new_bytes:
                    raise IngressTransactionError("Newer ingress state exists; rollback refused")
                if before is None:
                    os.unlink(target.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                else:
                    _replace(parent_fd, target.name, before, mode)
                lease.recheck()
                _check_directory(parent_fd, parent, owner)
                validate_and_reload()
            except Exception as rollback_error:
                raise IngressTransactionError("Ingress update failed; rollback could not be confirmed") from rollback_error
            raise IngressTransactionError("Ingress update failed; prior bytes restored and reloaded") from error
        return hashlib.sha256(new_bytes).hexdigest()
    finally:
        os.close(parent_fd)
