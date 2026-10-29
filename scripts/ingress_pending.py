"""Fail closed on unfinished Caddy bundles before any ingress writer runs.

Bootstrap interruptions are covered by the root-owned selector's transition
record, which all writers must read under the same lease before calling here.
"""
from pathlib import Path
import stat
from identity_ingress_transaction import IngressTransactionError

JOURNALS = Path('/srv/open-hax/state/caddy/ingress-transactions')
BOOTSTRAP_GUARD = Path('/etc/open-hax/ingress/bootstrap-pending')


def private_dir(path, owner):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise IngressTransactionError('Directory must be private and operator-owned')


def refuse_pending(journals=JOURNALS, owner=None, *, bootstrap_guard=BOOTSTRAP_GUARD):
    import os
    owner = os.getuid() if owner is None else owner
    if bootstrap_guard.exists() or bootstrap_guard.is_symlink():
        raise IngressTransactionError('Prior bootstrap transaction requires recovery')
    private_dir(journals, owner)
    for directory in journals.iterdir():
        private_dir(directory, owner)
        if (directory / 'pending').exists() or (directory / 'pending').is_symlink():
            raise IngressTransactionError('Prior ingress transaction requires recovery')
