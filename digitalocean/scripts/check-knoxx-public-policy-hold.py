#!/usr/bin/env python3
"""Refuse a Knoxx/Caddy stack change while its public policy overlay needs retirement."""

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys

STATE = Path("/srv/open-hax/state")


def require(condition):
    if not condition:
        raise ValueError("active, interrupted or unknown Knoxx policy overlay")


def kind(path):
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return 0


def unique_keys(pairs):
    data = dict(pairs)
    require(len(data) == len(pairs))
    return data


def stack_is_clear(state):
    """A fresh state or a verified rollback with no policy files can proceed."""
    for directory in (*reversed(state.parents), state, state / "caddy"):
        mode = kind(directory)
        if not mode:
            return
        require(stat.S_ISDIR(mode))
    caddy = state / "caddy"
    lock = caddy / ".knoxx-public-policy-release.lock"
    if mode := kind(lock):
        require(stat.S_ISREG(mode) and mode & 0o077 == 0)
        fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            file_mode = os.fstat(fd).st_mode
            require(stat.S_ISREG(file_mode) and file_mode & 0o077 == 0)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            inspect_state(caddy)
        finally:
            os.close(fd)
    else:
        inspect_state(caddy)


def inspect_state(caddy):
    managed = caddy / "managed"
    mode = kind(managed)
    if mode:
        require(stat.S_ISDIR(mode))
        require(not any(kind(managed / name) for name in
                        ("knoxx-public-policy.caddy", "knoxx-public-policy")))

    receipt = caddy / ".knoxx-public-policy-release.json"
    mode = kind(receipt)
    if not mode:
        return
    require(stat.S_ISREG(mode) and mode & 0o077 == 0)
    with os.fdopen(os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        file_mode = os.fstat(stream.fileno()).st_mode
        require(stat.S_ISREG(file_mode) and file_mode & 0o077 == 0)
        raw = stream.read(65537)
    require(len(raw) <= 65536)
    data = json.loads(raw, object_pairs_hook=unique_keys)
    require(isinstance(data, dict) and data.get("phase") == "rolled_back")
    for name, length in (("controller_sha", 40), ("plan_sha256", 64),
                         ("incumbent_config_sha256", 64), ("candidate_config_sha256", 64)):
        value = data.get(name)
        require(isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value))


if __name__ == "__main__":
    try:
        stack_is_clear(STATE)
    except (OSError, ValueError):
        print("Knoxx policy overlay is active, interrupted or unreadable. Reconcile or retire it before deploying Knoxx/Caddy.",
              file=sys.stderr)
        sys.exit(74)
