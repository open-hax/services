"""Fail closed on unfinished Caddy bundles before any ingress writer runs.

Bootstrap interruptions are covered by the root-owned selector's transition
record, which all writers must read under the same lease before calling here.
"""
from pathlib import Path
import stat
from identity_ingress_transaction import IngressTransactionError, _read

JOURNALS = Path('/srv/open-hax/state/caddy/ingress-transactions')
BOOTSTRAP_GUARD = Path('/etc/open-hax/ingress/bootstrap-pending')


def private_dir(path, owner):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise IngressTransactionError('Directory must be private and operator-owned')


def refuse_pending(journals=JOURNALS, owner=None, *, bootstrap_guard=BOOTSTRAP_GUARD, own_journal=None, bootstrap_owner=0):
    import os
    owner = os.getuid() if owner is None else owner
    from ingress_owner import bootstrap_guard_pending
    if bootstrap_guard_pending(bootstrap_guard, owner=bootstrap_owner):
        raise IngressTransactionError('Prior bootstrap transaction requires recovery')
    private_dir(journals, owner)
    for directory in journals.iterdir():
        private_dir(directory, owner)
        if own_journal is not None and directory == own_journal:
            continue
        if (directory / 'pending').exists() or (directory / 'pending').is_symlink():
            # pending is immutable. Only the atomically published, pre-synced
            # positive outcome opens the gate; malformed or partial markers do not.
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                try:
                    settled, mode = _read(fd, 'settled', owner)
                    result, result_mode = _read(fd, 'result', owner)
                except FileNotFoundError:
                    raise IngressTransactionError('Prior ingress transaction requires recovery')
                if (mode != 0o600 or result_mode != 0o600 or settled != result or
                        settled not in (b'applied-and-verified\n', b'restored-and-verified\n')):
                    raise IngressTransactionError('Prior ingress transaction requires recovery')
            finally:
                os.close(fd)
