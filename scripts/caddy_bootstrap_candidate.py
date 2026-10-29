"""Review-only Caddy blue/green bootstrap transaction.

This module intentionally has no production CLI or Docker driver. A host driver
must implement the contract below and prove its Compose ownership handoff before
it may pass preflight. In particular, an abstract/fake driver is not deployment
evidence. The incumbent must remain present until adoption is independently
qualified; no method may remove or recreate that incumbent.
"""
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat

from identity_ingress_transaction import IngressTransactionError


@dataclass(frozen=True)
class BootstrapPlan:
    incumbent_id: str
    incumbent_image: str
    ports: tuple[tuple[str, int, int], ...]
    networks: tuple[tuple[str, str], ...]
    identity_peer: tuple[str, str]
    inspected_fingerprint: str
    compose_project: str = 'caddy'
    compose_service: str = 'caddy'


def production_plan(container):
    """Recognize only the reviewed incumbent topology, with exact endpoints.

    No container fields, labels, mounts or values are printed. A new port,
    network, mount or Compose owner requires a fresh reviewed implementation.
    """
    ident = container.get('Id', '')
    image = container.get('Image', '')
    config = container.get('Config') or {}
    labels = config.get('Labels') or {}
    if not re.fullmatch(r'[0-9a-f]{64}', ident) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise IngressTransactionError('Unexpected incumbent container or image identity')
    if labels.get('com.docker.compose.project') != 'caddy' or labels.get('com.docker.compose.service') != 'caddy':
        raise IngressTransactionError('Incumbent is not owned by the expected Compose service')
    if config.get('User') not in ('', 'root', '0', '0:0'):
        raise IngressTransactionError('This bootstrap only models the existing root incumbent')
    bindings = (container.get('HostConfig') or {}).get('PortBindings') or {}
    if set(bindings) != {'80/tcp', '443/tcp'}:
        raise IngressTransactionError('Unexpected incumbent published ports')
    ports = []
    for name, expected in (('80/tcp', 80), ('443/tcp', 443)):
        entries = bindings[name]
        if not isinstance(entries, list) or len(entries) != 1:
            raise IngressTransactionError('Unexpected port binding multiplicity')
        host = entries[0].get('HostIp', '')
        host_port = entries[0].get('HostPort', '')
        if host not in ('', '0.0.0.0') or host_port != str(expected):
            raise IngressTransactionError('Port binding differs from the reviewed public endpoint')
        ports.append((host, expected, expected))
    networks = (container.get('NetworkSettings') or {}).get('Networks') or {}
    if set(networks) != {'open-hax', 'caddy-dev-ingress'}:
        raise IngressTransactionError('Unexpected incumbent network topology')
    pairs = []
    for name in sorted(networks):
        address = networks[name].get('IPAddress')
        try:
            valid = isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address)
        except ValueError:
            valid = False
        if not valid:
            raise IngressTransactionError('Missing incumbent network address')
        pairs.append((name, address))
    if networks['caddy-dev-ingress']['IPAddress'] != '172.31.255.2':
        raise IngressTransactionError('Dev-ingress fixed peer differs')
    mounts = container.get('Mounts', [])
    sources = {m.get('Destination'): (m.get('Source'), m.get('RW')) for m in mounts}
    required = {'/etc/caddy/Caddyfile': ('/srv/open-hax/services/caddy/Caddyfile', False),
                '/etc/caddy/axxium-identity/did.json': ('/srv/open-hax/services/caddy/axxium-did.json', False),
                '/etc/caddy/managed': ('/srv/open-hax/state/caddy/managed', False),
                '/data': ('/srv/open-hax/state/caddy/data', True),
                '/config': ('/srv/open-hax/state/caddy/config', True)}
    if sources != required or len(sources) != len(mounts):
        raise IngressTransactionError('Unexpected incumbent mounts')
    # Keep the complete original topology and environment bound to the plan
    # without logging or serializing their potentially sensitive values.
    fingerprint = hashlib.sha256(json.dumps(
        {key: container.get(key) for key in ('Id', 'Image', 'Config', 'HostConfig', 'Mounts', 'NetworkSettings')},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return BootstrapPlan(ident, image, tuple(ports), tuple(pairs),
                         ('axxium-identity-ingress', '172.30.115.2'), fingerprint)


def _event(journal, index, name):
    """Write and fsync an append-only phase marker before each host effect."""
    path = journal / f'{index:02d}-{name}.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'phase': name}, out)
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def bootstrap_under_lease(lease, journal: Path, driver, plan: BootstrapPlan, *, owner=None):
    """Run a journaled candidate handoff; return only after an adopted check.

    Every driver method must be exact-target, idempotent for crash recovery and
    fail on identity/endpoint drift. `rollback` must verify the retained original
    ID, exact old endpoints, TLS and routes; it must not manufacture a root
    replacement. A crash leaves `pending` for an external recovery controller.
    """
    owner = os.getuid() if owner is None else owner
    info = journal.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700 or list(journal.iterdir()):
        raise IngressTransactionError('Bootstrap journal must be a private empty directory')
    lease.recheck()
    # Admission must cover all writers, snapshot permissions and rollback before
    # stopping anything. It is deliberately not inferred from matching labels.
    if driver.preflight(plan) is not True or driver.prevalidate_nonroot(plan) is not True:
        raise IngressTransactionError('Bootstrap preflight or nonroot validation failed')
    if driver.adoption_preflight(plan) is not True:
        raise IngressTransactionError('Compose ownership handoff is not qualified')
    _event(journal, 0, 'pending')
    try:
        lease.recheck(); _event(journal, 1, 'owner-transition-intent'); driver.begin_owner_transition(plan)
        lease.recheck(); _event(journal, 2, 'stop-intent'); driver.stop_incumbent(plan)
        lease.recheck(); _event(journal, 3, 'snapshot-intent'); driver.snapshot_quiescent_and_verify(plan)
        lease.recheck(); _event(journal, 4, 'release-intent'); driver.release_incumbent_endpoints(plan)
        lease.recheck(); _event(journal, 5, 'start-intent'); driver.start_nonroot_candidate(plan)
        lease.recheck(); _event(journal, 6, 'verify-intent'); driver.verify_candidate_tls_routes_peer(plan)
        lease.recheck(); _event(journal, 7, 'adopt-intent'); driver.adopt_without_removing_incumbent(plan)
        lease.recheck(); driver.verify_adoption_and_retained_incumbent(plan)
        lease.recheck(); _event(journal, 8, 'owner-commit-intent'); driver.commit_owner_selection(plan)
        lease.recheck(); _event(journal, 9, 'adopted-and-verified')
        return 'adopted-and-verified'
    except Exception as error:
        try:
            lease.recheck(); _event(journal, 10, 'rollback-intent')
            driver.rollback_to_same_incumbent(plan)
            lease.recheck(); driver.verify_incumbent_tls_routes_and_identity(plan)
            lease.recheck(); driver.restore_owner_selection(plan)
            _event(journal, 11, 'rolled-back-and-verified')
        except Exception as rollback_error:
            raise IngressTransactionError('Bootstrap requires manual recovery; preserve journal and both containers') from rollback_error
        raise IngressTransactionError('Bootstrap failed; retained incumbent restored and verified') from error
