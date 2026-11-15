#!/usr/bin/env python3
"""Candidate Caddyfile transaction for the existing Services production lock.

This module has no deployment entry point. A reviewed Services controller must
hold the production-host concurrency slot and the shared host ingress lease,
admit the application release, and verify the dedicated network before calling
it. It deliberately preserves the existing single-file bind inode.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid


OLD = """\t@axxium_api {
\t\thost axxium.promethean.rest
\t\tpath / /health /portal/* /api/auth/* /api/actors* /api/entities* /api/atproto/*
\t}
\thandle @axxium_api {
\t\treverse_proxy axxium:8787 {"""
NEW = OLD.replace("/api/atproto/*\n", "/api/atproto/* /oidc /oidc/*\n").replace(
    "reverse_proxy axxium:8787", "reverse_proxy axxium-identity-ingress:8787")


def digest(data):
    """Return the nonsecret identity used for every config comparison."""
    return hashlib.sha256(data).hexdigest()


def candidate(source, expected_sha256):
    """Transform one selected exact-host matcher and retain all other bytes."""
    if digest(source) != expected_sha256:
        raise ValueError("Caddyfile baseline changed")
    text = source.decode("utf-8")
    if text.count(OLD) != 1 or text.count("axxium.promethean.rest, *.axxium.promethean.rest {") != 1:
        raise ValueError("Axxium route is absent or ambiguous")
    if "reverse_proxy axxium-identity-ingress:8787" in text:
        raise ValueError("Identity ingress already selected")
    return text.replace(OLD, NEW, 1).encode("utf-8")


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise OSError("Short journal write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def write_same_inode(fd, data):
    """Keep the mounted inode; callers retain recovery evidence on any error."""
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:
            raise OSError("Short Caddyfile write")
        view = view[written:]
    os.ftruncate(fd, len(data))
    os.fsync(fd)


def read_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    parts = []
    while True:
        part = os.read(fd, 65536)
        if not part:
            return b"".join(parts)
        parts.append(part)


def apply(config_path, journal_root, expected_sha256, *, lease_held,
          inspect_identity, validate, reload_config, verify):
    """Run under an already held lease using fixed trusted controller hooks.

    `inspect_identity` returns the same immutable container ID and mount inode
    tuple before/after; hooks must inspect actual Docker state, validate the
    mounted config, reload Caddy and verify the required public route set.
    These hooks are not supplied by app code, MCP clients, or CLI arguments.
    Interrupted or uncertain writes retain an unfinished journal for explicit
    operator recovery. No callback can grant application release admission.
    """
    if not lease_held():
        raise ValueError("Shared ingress lease is required")
    root = Path(journal_root)
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.geteuid() or root_stat.st_mode & 0o077:
        raise ValueError("Journal root must be an owned private directory")
    if any(not (p / "complete").is_file() for p in root.iterdir()):
        raise ValueError("Unfinished ingress transaction requires explicit recovery")
    path = Path(config_path)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        original_stat = os.fstat(fd)
        if not stat.S_ISREG(original_stat.st_mode) or original_stat.st_nlink != 1:
            raise ValueError("Caddyfile must be an unlinked regular file")
        original = read_fd(fd)
        changed = candidate(original, expected_sha256)
        identity = inspect_identity()
        txn = root / str(uuid.uuid4())
        txn.mkdir(mode=0o700)
        sync_dir(root)
        private_file(txn / "before", original)
        private_file(txn / "candidate", changed)
        metadata = {"device": original_stat.st_dev, "inode": original_stat.st_ino,
                    "before_sha256": digest(original), "candidate_sha256": digest(changed)}
        private_file(txn / "plan.json", (json.dumps(metadata, sort_keys=True) + "\n").encode())
        sync_dir(txn)

        def unchanged_identity():
            current = path.lstat()
            return (lease_held() and inspect_identity() == identity and
                    (current.st_dev, current.st_ino) == (original_stat.st_dev, original_stat.st_ino))

        if not unchanged_identity() or read_fd(fd) != original:
            raise ValueError("Ingress baseline changed before write")
        try:
            write_same_inode(fd, changed)
            if not unchanged_identity() or read_fd(fd) != changed:
                raise ValueError("Ingress changed during write")
            validate()
            reload_config()
            verify(True)
            if not unchanged_identity() or read_fd(fd) != changed:
                raise ValueError("Ingress changed during verification")
        except Exception:
            # A partial/uncertain write is never silently overwritten. Its
            # private before-image and pending journal remain for recovery.
            if unchanged_identity() and read_fd(fd) == changed:
                write_same_inode(fd, original)
                validate()
                reload_config()
                verify(False)
                if unchanged_identity() and read_fd(fd) == original:
                    private_file(txn / "complete", b"rolled-back\n")
                    sync_dir(txn)
            raise
        private_file(txn / "complete", b"committed\n")
        sync_dir(txn)
        return {"before_sha256": digest(original), "candidate_sha256": digest(changed),
                "journal": str(txn), "inode": original_stat.st_ino}
    finally:
        os.close(fd)
