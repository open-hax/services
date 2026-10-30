"""Unwired, fixed-incident filesystem handoff preparation.

This module is deliberately not a CLI.  It cannot safely be used until a
reviewed caller proves the native lock, incident journal, identity, incumbent,
and process containment and adopts the staged state into the release controller.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat

SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
RUN = '41088376408'


class HandoffRefused(Exception):
    pass


def _rename_noreplace(source, destination):
    """Atomic same-filesystem rename which never replaces an existing entry."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise HandoffRefused('Atomic no-replace rename is unavailable')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL):
            raise HandoffRefused('Atomic no-replace rename is unavailable')
        raise OSError(code, os.strerror(code))


def _private_dir(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise HandoffRefused('Private directory protection differs')


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise HandoffRefused('Private file protection differs')
        with os.fdopen(fd, 'rb', closefd=False) as src:
            return src.read()
    finally:
        os.close(fd)


def _digest_private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise HandoffRefused('Private file protection differs')
        with os.fdopen(fd, 'rb', closefd=False) as src:
            return hashlib.file_digest(src, 'sha256').hexdigest()
    finally:
        os.close(fd)


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as out:
            out.write(data); out.flush(); os.fsync(fd)
    finally: os.close(fd)
    _sync(path.parent)


def handoff_files(*, operations, pending, expected_hold, plan, snapshots, recheck):
    """Prepare the fixed incident's archive and staged fresh operation.

    Caller MUST own and recheck the native lock, and MUST have already checked
    exact incident, live runtime, identity and containment.  No checks here
    grant authority to run a deployment.  On interruption, retain the hold.
    """
    operations, pending = Path(operations), Path(pending)
    _private_dir(operations)
    old = operations / SOURCE
    archive = operations / (SOURCE + '.interrupted.' + RUN)
    stage = operations / (SOURCE + '.retry.' + RUN)
    intent = operations / (SOURCE + '.handoff.' + RUN + '.json')
    _private_dir(old)
    if set(snapshots) != {'incumbent.compose.yaml', 'incumbent.env', 'incumbent.Caddyfile'}:
        raise HandoffRefused('Fresh snapshot names differ')
    if not isinstance(plan, bytes) or not isinstance(expected_hold, bytes):
        raise HandoffRefused('Unexpected private input type')
    def guard():
        recheck()
        if _read(pending) != expected_hold:
            raise HandoffRefused('Production hold differs')
    guard()
    backup = json.loads(_read(old / 'before-backup.json'))
    sha = _digest_private_file(old / 'before.pgdump')
    if backup.get('path') != str(old / 'before.pgdump') or backup.get('sha256') != sha:
        raise HandoffRefused('Original backup metadata differs')
    # Both target names and the durable intent are specific to this incident.
    # Exclusivity means a partial previous attempt can never be reused silently.
    manifest = {'schema': 'axxium-interrupted-handoff/v1', 'run': RUN,
                'old_operation': str(old), 'archived_operation': str(archive),
                'old_backup_record_path': backup['path'],
                'archived_backup_path': str(archive / 'before.pgdump'),
                'backup_sha256': sha}
    _write(intent, (json.dumps(manifest, sort_keys=True) + '\n').encode())
    guard()
    stage.mkdir(mode=0o700)
    _sync(operations)
    _write(stage / 'plan.json', plan)
    _write(stage / 'parent-handoff.json', (json.dumps(manifest, sort_keys=True) + '\n').encode())
    for name, data in snapshots.items():
        if not isinstance(data, bytes): raise HandoffRefused('Invalid snapshot data')
        _write(stage / name, data)
    _sync(stage)
    guard()
    _rename_noreplace(old, archive)
    _sync(operations)
    guard()
    _rename_noreplace(stage, old)
    _sync(operations)
    guard()
    return manifest
