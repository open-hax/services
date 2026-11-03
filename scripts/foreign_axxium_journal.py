"""Durable one-use journal for the unactivated foreign Axxium transition.

This supplies storage, not host admission or a production lock installation.
The host controller must supply a trusted lock factory shared by every receiver,
ingress and legacy writer. There is deliberately no CLI or default lock factory.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from foreign_axxium_adoption import Refused, validate_plan


DIRECTORY = Path('/srv/open-hax/foreign-adoption/axxium')
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _same(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _open_directory(path, owner):
    """Walk every component without following links or writable ancestors."""
    if not path.is_absolute() or '..' in path.parts:
        raise Refused('Journal path must be an absolute trusted directory')
    fd = os.open('/', _DIRECTORY_FLAGS)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            if info.st_uid not in (0, owner) or info.st_mode & 0o022:
                raise Refused('Journal ancestor is not administrator controlled')
        info = os.fstat(fd)
        if info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
            raise Refused('Journal directory must be private and owned by its controller')
        return fd
    except BaseException:
        os.close(fd)
        raise


class DurableAdoptionJournal:
    """Exclusive immutable plan and append-only fsynced phase records.

    Root must pre-create the private directory on a trusted local filesystem.
    ``lock_factory`` is a trusted integration interface, not caller evidence. It
    must yield a lease whose ``recheck()`` fails if any cooperating writer can
    enter or its stable lock identity changes. Local tests cannot establish
    that the actual host has enrolled or excluded every deployment writer.
    """
    def __init__(self, lock_factory, directory=DIRECTORY, *, owner=0):
        self.directory = Path(directory)
        self.lock_factory = lock_factory
        self.owner = owner
        self._fd = None
        self._lease = None
        self._digest = None
        self._plan_identity = None
        self._sequence = 0
        self._terminal = False
        self._failed = False

    @contextmanager
    def lock(self):
        if self._fd is not None:
            raise Refused('Journal lock cannot be entered recursively')
        with self.lock_factory() as lease:
            fd = _open_directory(self.directory, self.owner)
            self._fd, self._lease = fd, lease
            self._digest, self._plan_identity = None, None
            self._sequence, self._terminal = 0, False
            self._failed = False
            try:
                self._recheck()
                yield
            finally:
                self._fd = self._lease = self._digest = self._plan_identity = None
                os.close(fd)

    def _recheck(self):
        if self._failed:
            raise Refused('Journal uncertainty requires independent recovery')
        try:
            self._verify_trusted_state()
        except BaseException:
            self._failed = True
            raise

    def _verify_trusted_state(self):
        if self._fd is None or self._lease is None:
            raise Refused('Journal operation requires the trusted host lock')
        if self._lease.recheck() is not True:
            raise Refused('Trusted host lock recheck refused')
        current = _open_directory(self.directory, self.owner)
        try:
            if not _same(os.fstat(self._fd), os.fstat(current)):
                raise Refused('Journal directory identity changed')
        finally:
            os.close(current)
        if self._digest is not None:
            self._verify_plan()

    def _verify_plan(self):
        fd = os.open('plan.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=self._fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > 65536
                    or self._plan_identity is not None
                    and self._plan_identity != (info.st_dev, info.st_ino)):
                raise Refused('Claimed plan identity or protection changed')
            raw = b''
            while len(raw) <= 65536:
                chunk = os.read(fd, 65537 - len(raw))
                if not chunk:
                    break
                raw += chunk
            if hashlib.sha256(raw).hexdigest() != self._digest:
                raise Refused('Claimed plan content changed')
            self._plan_identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)

    def _exclusive(self, name, content):
        self._recheck()
        fd = None
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=self._fd)
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner
                    or info.st_nlink != 1):
                raise Refused('New journal record ownership refused')
            with os.fdopen(fd, 'wb', closefd=False) as output:
                output.write(content)
                output.flush()
                os.fsync(fd)
            self._recheck()
            os.fsync(self._fd)
        except BaseException:
            self._failed = True
            raise
        finally:
            if fd is not None:
                os.close(fd)

    def claim(self, digest, raw):
        """A partial/uncertain claim remains a permanent replay barrier."""
        self._recheck()
        _, actual = validate_plan(raw)
        if actual != digest or self._digest is not None:
            raise Refused('Journal claim does not match one immutable plan')
        # No existing object, including a symlink or malformed partial file,
        # is removed, repaired or treated as permission to replay.
        try:
            self._exclusive('plan.json', raw)
        except FileExistsError:
            return False
        self._digest = digest
        self._verify_plan()
        return True

    def record(self, digest, name, status):
        self._recheck()
        if (digest != self._digest or self._digest is None or self._terminal
                or not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', name)
                or status not in ('pending', 'succeeded', 'known_failure', 'uncertain', 'healthy', 'rolled_back')
                or (status in ('healthy', 'rolled_back')) != (name == 'transaction')):
            raise Refused('Journal phase is not bound to the claimed transition')
        entry = {'schema': 'axxium-foreign-adoption-phase/v1', 'sequence': self._sequence,
                 'plan_sha256': digest, 'name': name, 'status': status}
        data = (json.dumps(entry, sort_keys=True, separators=(',', ':')) + '\n').encode()
        # The fixed sequence name is never reused after a failed persistence;
        # neither a retry nor a new process can overwrite a partial record.
        self._exclusive(f'{self._sequence:06d}-{name}-{status}.json', data)
        self._sequence += 1
        self._terminal = name == 'transaction'
