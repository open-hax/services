#!/usr/bin/env python3
"""Prepare a private, byte-verified nonroot Caddy state copy; never alter source.

This does not deploy or switch a mount. A successful copy proves only the
observed files at the time of copying, not ongoing certificate renewal or TLS.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SOURCE = Path("/srv/open-hax/state/caddy")
PARENT = SOURCE / "nonroot-adoption-candidates"
UID = GID = 1000


def digest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Source is not a uniquely linked regular file")
        return hashlib.file_digest(source, "sha256").hexdigest()


def inventory(root):
    """Read metadata and content digests; reject links and special entries."""
    root = Path(root)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("Caddy state root must be a real directory")
    entries = {}
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories + files:
            path = Path(current) / name
            info = path.lstat()
            key = path.relative_to(root).as_posix()
            common = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid,
                      "gid": info.st_gid, "mtime_ns": info.st_mtime_ns}
            if stat.S_ISDIR(info.st_mode):
                entries[key] = {"kind": "directory", **common}
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                entries[key] = {"kind": "file", "size": info.st_size,
                                "sha256": digest(path), **common}
            else:
                raise ValueError("Caddy state contains a link or unsupported file type")
    return entries


def copy_verified(source_root, destination, *, uid=UID, gid=GID):
    source_root, destination = Path(source_root), Path(destination)
    before = {name: inventory(source_root / name) for name in ("data", "config")}
    destination.mkdir(mode=0o700)  # exclusive: never replace a prior candidate
    os.chown(destination, uid, gid)
    marker = destination / "PENDING"
    marker.write_text("Copy is not yet verified. Preserve for investigation.\n")
    os.chmod(marker, 0o600)
    os.chown(marker, uid, gid)
    for section, entries in before.items():
        target_root = destination / section
        target_root.mkdir(mode=0o700)
        os.chown(target_root, uid, gid)
        for relative, meta in entries.items():
            source = source_root / section / relative
            target = target_root / relative
            if meta["kind"] == "directory":
                target.mkdir(mode=0o700)
                os.chown(target, uid, gid)
                continue
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(source_fd, "rb") as reader, os.fdopen(target_fd, "wb") as writer:
                source_info = os.fstat(reader.fileno())
                if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
                    raise ValueError("Source changed before copy")
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            os.chown(target, uid, gid)
    after = {name: inventory(source_root / name) for name in ("data", "config")}
    if after != before:
        raise ValueError("Source Caddy state changed during copy; candidate retained as pending")
    for section, expected in before.items():
        actual = inventory(destination / section)
        if set(actual) != set(expected):
            raise ValueError("Copied tree differs in paths; candidate retained as pending")
        for relative, item in expected.items():
            other = actual[relative]
            if (other["kind"] != item["kind"] or other["uid"] != uid or other["gid"] != gid or
                    other["mode"] != (0o700 if item["kind"] == "directory" else 0o600) or
                    item["kind"] == "file" and (other["size"], other["sha256"]) != (item["size"], item["sha256"])):
                raise ValueError("Copied content or permissions differ; candidate retained as pending")
    manifest = destination / "source-manifest.json"
    fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(before, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(manifest, uid, gid)
    # Preserve the marker as an audit artifact; the verified result is separate.
    verified = destination / "VERIFIED"
    fd = os.open(verified, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("Source remained unchanged; all copied bytes and private ownership verified.\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(verified, uid, gid)
    return {"source_unchanged": True, "copy_verified": True,
            "entries": sum(map(len, before.values())), "target_uid": uid}


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", sys.argv[1]) is None:
        raise SystemExit("Root and a safe, unique candidate name are required")
    # Parent is intentionally an operator-owned non-mounted state directory.
    if PARENT.exists() or PARENT.is_symlink():
        info = PARENT.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != UID or stat.S_IMODE(info.st_mode) != 0o700:
            raise SystemExit("Candidate parent is not a private operator-owned directory")
    else:
        PARENT.mkdir(mode=0o700)
        os.chown(PARENT, UID, GID)
    print(json.dumps(copy_verified(SOURCE, PARENT / sys.argv[1]), sort_keys=True))


if __name__ == "__main__":
    main()
