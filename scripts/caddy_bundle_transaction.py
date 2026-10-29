"""Unactivated multi-file Caddy transaction for a fixed trusted dispatcher.

This primitive does not select files, images, Compose commands or credentials.
The production dispatcher must stage and validate its own fixed inputs before
calling it under the shared ingress lease. Its journal contains private backups;
retain it and do not start another transaction while its pending marker exists.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from identity_ingress_transaction import MAX_CONFIG_BYTES, IngressTransactionError, _check_directory, _read, _replace


@dataclass(frozen=True)
class BundleItem:
    target: Path
    candidate: bytes
    expected_sha256: str | None


def _write_private(directory_fd, name, content):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory_fd)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory_fd)


def _snapshot(item, owner):
    parent = item.target.parent
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _check_directory(fd, parent, owner)
        try:
            value, mode = _read(fd, item.target.name, owner)
        except FileNotFoundError:
            value, mode = None, 0o600
        return fd, value, mode
    except Exception:
        os.close(fd)
        raise


def _digest(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _publish_settlement(directory_fd, content):
    """Publish a verified outcome without ever removing the pending guard.

    The prepared file and its directory are synced before the atomic rename.
    No fallible durability operation follows publication: after a crash the
    rename may be lost, in which case the retained guard conservatively closes
    the gate. A visible settlement can only describe already durable state.
    """
    _write_private(directory_fd, 'settlement-prepared', content)
    os.rename('settlement-prepared', 'settled', src_dir_fd=directory_fd, dst_dir_fd=directory_fd)


def apply_bundle_under_lease(lease, items, journal: Path, apply_and_verify, restore_and_verify, *, owner=None):
    """Apply known files and a Caddy recreation as one recoverable operation.

    Inputs are chosen by a trusted, fixed dispatcher, not an MCP caller. The
    private journal directory must already exist, be owner-controlled and empty;
    its pending marker persists on failure if rollback cannot be confirmed. All
    callers must refuse any pending earlier operation before invoking this API.
    Non-cooperating writers remain an external hazard; the common lease must
    first be wired into every actual ingress writer and out-of-band writers
    stopped or made to cooperate. A journal records bytes; it is not a lock.
    """
    owner = os.getuid() if owner is None else owner
    if not items or len({str(item.target) for item in items}) != len(items):
        raise IngressTransactionError("Empty or duplicate Caddy bundle")
    if any(not item.target.is_absolute() or not isinstance(item.candidate, bytes) or
           len(item.candidate) > MAX_CONFIG_BYTES for item in items):
        raise IngressTransactionError("Invalid Caddy bundle")
    journal_fd = os.open(journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    snapshots = []
    attempted = []
    pending = False
    settled = False
    try:
        info = os.fstat(journal_fd)
        _check_directory(journal_fd, journal, owner)
        if info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700 or os.listdir(journal_fd):
            raise IngressTransactionError("Caddy journal must be an empty private directory")
        lease.recheck()
        for item in items:
            fd, original, mode = _snapshot(item, owner)
            snapshots.append((item, fd, original, mode))
            if _digest(original) != item.expected_sha256:
                raise IngressTransactionError("Caddy bundle baseline changed")
        _write_private(journal_fd, "pending", b"pending\n")
        pending = True
        manifest = []
        for index, (item, _, original, mode) in enumerate(snapshots):
            if original is not None:
                _write_private(journal_fd, f"before-{index}", original)
            manifest.append({"target": str(item.target), "before_sha256": _digest(original),
                             "after_sha256": _digest(item.candidate), "mode": mode})
        _write_private(journal_fd, "manifest.json", json.dumps(manifest, sort_keys=True).encode())
        for item, fd, original, mode in snapshots:
            lease.recheck()
            _check_directory(journal_fd, journal, owner)
            _check_directory(fd, item.target.parent, owner)
            try:
                current, _ = _read(fd, item.target.name, owner)
            except FileNotFoundError:
                current = None
            if current != original:
                raise IngressTransactionError("Caddy bundle changed before installation")
            # Rename may succeed even if its following directory fsync fails.
            # Treat the target as potentially affected before entering replace.
            attempted.append((item, fd, original, mode))
            _replace(fd, item.target.name, item.candidate, mode)
        lease.recheck()
        apply_and_verify()
        for item, fd, _, _ in attempted:
            lease.recheck()
            _check_directory(fd, item.target.parent, owner)
            current, _ = _read(fd, item.target.name, owner)
            if current != item.candidate:
                raise IngressTransactionError("Caddy bundle changed during verification")
        _write_private(journal_fd, "result", b"applied-and-verified\n")
        settled = True
        _check_directory(journal_fd, journal, owner)
        _publish_settlement(journal_fd, b"applied-and-verified\n")
        pending = False
        return manifest
    except Exception as error:
        if settled:
            raise IngressTransactionError("Caddy bundle verified but settlement requires recovery") from error
        if not pending:
            raise
        try:
            lease.recheck()
            # Check every attempted target before changing any of them; a
            # competing writer means no part of the rollback is safe to apply.
            changed = []
            for item, fd, original, mode in attempted:
                _check_directory(fd, item.target.parent, owner)
                try:
                    current, _ = _read(fd, item.target.name, owner)
                except FileNotFoundError:
                    current = None
                if current == original:
                    continue
                if current != item.candidate:
                    raise IngressTransactionError("Newer Caddy state exists; rollback refused")
                changed.append((item, fd, original, mode))
            for item, fd, original, mode in reversed(changed):
                lease.recheck()
                _check_directory(fd, item.target.parent, owner)
                if original is None:
                    os.unlink(item.target.name, dir_fd=fd)
                    os.fsync(fd)
                else:
                    _replace(fd, item.target.name, original, mode)
            lease.recheck()
            for item, fd, original, _ in snapshots:
                _check_directory(fd, item.target.parent, owner)
                try:
                    current, _ = _read(fd, item.target.name, owner)
                except FileNotFoundError:
                    current = None
                if _digest(current) != _digest(original):
                    raise IngressTransactionError("Original Caddy bundle could not be verified")
            restore_and_verify()
            for item, fd, original, _ in snapshots:
                lease.recheck()
                _check_directory(fd, item.target.parent, owner)
                try:
                    current, _ = _read(fd, item.target.name, owner)
                except FileNotFoundError:
                    current = None
                if _digest(current) != _digest(original):
                    raise IngressTransactionError("Original Caddy bundle changed during recovery")
            _write_private(journal_fd, "result", b"restored-and-verified\n")
            _check_directory(journal_fd, journal, owner)
            _publish_settlement(journal_fd, b"restored-and-verified\n")
        except Exception as rollback_error:
            raise IngressTransactionError("Caddy bundle failed; manual recovery is required") from rollback_error
        raise IngressTransactionError("Caddy bundle failed; prior bundle restored and verified") from error
    finally:
        for _, fd, _, _ in snapshots:
            os.close(fd)
        os.close(journal_fd)
