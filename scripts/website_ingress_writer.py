#!/usr/bin/env python3
"""Candidate website-staging ingress writer under the shared host lease.

Not wired into Services or installed on any host. Only fixed production-host
paths and the fixed Caddy container are accepted by the command-line entrypoint.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from ingress_lock import ingress_lock
from identity_ingress_transaction import IngressTransactionError, replace_under_lease
from ingress_owner import read_selection

RUNTIME = Path("/srv/open-hax/services/staging/website-staging")
MANAGED = Path("/srv/open-hax/state/caddy/managed")
CONTAINER = "caddy-caddy-1"
CANDIDATE = "website-staging.caddy.candidate"
TARGET = "website-staging.caddy"
CONFIG_PATHS = {"/etc/caddy/Caddyfile", "/etc/caddy/identity-ingress/Caddyfile"}


def read_private_candidate(path, expected, owner):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or
                info.st_mode & 0o022 or info.st_size > 2 * 1024 * 1024):
            raise IngressTransactionError("Unsafe staged website ingress")
        data = source.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
        raise IngressTransactionError("Website ingress candidate hash mismatch")
    return data


def docker(arguments):
    return subprocess.run(["docker", *arguments], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=30).stdout


def selected_config(mounts, command, managed):
    good_mounts = [mount for mount in mounts if mount.get("Source") == str(managed) and
                   mount.get("Destination") == "/etc/caddy/managed" and mount.get("RW") is False]
    if len(good_mounts) != 1 or not isinstance(command, list) or len(command) != 6:
        raise IngressTransactionError("Caddy managed-directory mount or command is unexpected")
    if command[:3] != ["caddy", "run", "--config"] or command[4:] != ["--adapter", "caddyfile"]:
        raise IngressTransactionError("Unexpected Caddy command")
    if command[3] not in CONFIG_PATHS:
        raise IngressTransactionError("Unexpected Caddy configuration path")
    return command[3]


def incumbent(path, owner):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or
                info.st_mode & 0o022 or info.st_size > 2 * 1024 * 1024):
            raise IngressTransactionError("Unsafe incumbent website ingress")
        value = source.read(2 * 1024 * 1024 + 1)
    if len(value) > 2 * 1024 * 1024:
        raise IngressTransactionError("Incumbent website ingress too large")
    return value


def run_under_lease(lease, expected_sha, *, runtime=RUNTIME, managed=MANAGED,
                    owner=None, docker_run=docker, container=CONTAINER):
    """One bounded website writer; caller holds the same host lease throughout."""
    owner = os.getuid() if owner is None else owner
    data = read_private_candidate(runtime / CANDIDATE, expected_sha, owner)
    mounts = json.loads(docker_run(["inspect", "--format", "{{json .Mounts}}", container]))
    command = json.loads(docker_run(["inspect", "--format", "{{json .Config.Cmd}}", container]))
    config = selected_config(mounts, command, managed)
    config_bytes = docker_run(["exec", "--user", "1000:1000", container, "cat", config])
    if not re.search(rb"(?m)^\s*import /etc/caddy/managed/\*\.caddy\s*$", config_bytes):
        raise IngressTransactionError("Selected Caddy configuration does not import managed routes")
    target = managed / TARGET
    before = incumbent(target, owner)
    expected_target = hashlib.sha256(before).hexdigest() if before is not None else None
    # This prevents a file bind or wrong directory from passing a host-only check.
    try:
        inside = docker_run(["exec", "--user", "1000:1000", container, "cat", "/etc/caddy/managed/" + TARGET])
    except subprocess.CalledProcessError:
        inside = None
    if inside != before:
        raise IngressTransactionError("Caddy does not see the same incumbent managed file")

    def validate_and_reload():
        for verb in ("validate", "reload"):
            lease.recheck()
            docker_run(["exec", "--user", "1000:1000", container, "caddy", verb,
                        "--config", config, "--adapter", "caddyfile"])
    return replace_under_lease(lease, target, data, expected_target, validate_and_reload, owner=owner)


def main():
    if (len(sys.argv) != 3 or len(sys.argv[1]) != 64 or
            any(c not in "0123456789abcdef" for c in sys.argv[1]) or
            re.fullmatch(r"[0-9]+-[0-9]+", sys.argv[2]) is None):
        raise SystemExit("Expected reviewed candidate SHA-256 and numeric run-attempt ID")
    candidate_runtime = RUNTIME / ".ingress-candidates" / sys.argv[2]
    with ingress_lock() as lease:
        selected = read_selection()
        def selected_docker(arguments):
            lease.recheck()
            if read_selection() != selected:
                raise IngressTransactionError('Ingress owner changed during website transaction')
            return docker(arguments)
        digest = run_under_lease(lease, sys.argv[1], runtime=candidate_runtime,
                                 docker_run=selected_docker, container=selected[1])
    print(json.dumps({"service": "website-staging", "sha256": digest, "status": "reloaded"}))


if __name__ == "__main__":
    main()
