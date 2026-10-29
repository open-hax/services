"""Review-only Caddy blue/green bootstrap transaction.

This module intentionally has no production CLI or Docker driver. A host driver
must implement the contract below and prove its Compose ownership handoff before
it may pass preflight. In particular, an abstract/fake driver is not deployment
evidence. The incumbent must remain present until adoption is independently
qualified; no method may remove or recreate that incumbent.
"""
from dataclasses import asdict, dataclass
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


def _save_plan(journal, plan):
    """Persist the exact incumbent identity before the first host effect."""
    path = journal / 'plan.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(asdict(plan), out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _load_plan(journal, owner):
    info = journal.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise IngressTransactionError('Unsafe bootstrap recovery journal')
    fd = os.open(journal / 'plan.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        f = os.fstat(stream.fileno())
        if not stat.S_ISREG(f.st_mode) or f.st_uid != owner or f.st_nlink != 1 or stat.S_IMODE(f.st_mode) != 0o600 or f.st_size > 8192:
            raise IngressTransactionError('Unsafe bootstrap recovery plan')
        value = json.load(stream)
    try:
        plan = BootstrapPlan(value['incumbent_id'], value['incumbent_image'],
                             tuple(tuple(x) for x in value['ports']), tuple(tuple(x) for x in value['networks']),
                             tuple(value['identity_peer']), value['inspected_fingerprint'],
                             value['compose_project'], value['compose_service'])
    except (KeyError, TypeError, ValueError) as error:
        raise IngressTransactionError('Invalid bootstrap recovery plan') from error
    if (set(value) != set(asdict(plan)) or not re.fullmatch(r'[0-9a-f]{64}', plan.incumbent_id) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', plan.incumbent_image) or
            not re.fullmatch(r'[0-9a-f]{64}', plan.inspected_fingerprint) or
            plan.compose_project != 'caddy' or plan.compose_service != 'caddy' or
            plan.identity_peer != ('axxium-identity-ingress', '172.30.115.2') or
            len(plan.ports) != 2 or any(len(x) != 3 for x in plan.ports) or
            any(x[0] not in ('', '0.0.0.0') for x in plan.ports) or
            tuple(x[1:] for x in plan.ports) != ((80, 80), (443, 443)) or
            len(plan.networks) != 2 or {name for name, _ in plan.networks} != {'open-hax', 'caddy-dev-ingress'} or
            dict(plan.networks).get('caddy-dev-ingress') != '172.31.255.2'):
        raise IngressTransactionError('Bootstrap recovery plan is outside the fixed topology')
    try:
        for _, address in plan.networks:
            if not isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address):
                raise ValueError('not IPv4')
    except ValueError as error:
        raise IngressTransactionError('Invalid recovery network address') from error
    return plan


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
    _save_plan(journal, plan)
    # The shared root-owned guard precedes all host effects and survives even
    # after owner commit until the terminal journal record is durable.
    driver.mark_pending_guard(plan)
    try:
        _event(journal, 0, 'pending')
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
    except Exception as error:
        try:
            lease.recheck(); _event(journal, 10, 'rollback-intent')
            driver.rollback_to_same_incumbent(plan)
            lease.recheck(); driver.verify_incumbent_tls_routes_and_identity(plan)
            lease.recheck(); driver.restore_owner_selection(plan)
            _event(journal, 11, 'rolled-back-and-verified')
            lease.recheck(); driver.clear_pending_guard(plan, 'caddy')
        except Exception as rollback_error:
            raise IngressTransactionError('Bootstrap requires manual recovery; preserve journal and both containers') from rollback_error
        raise IngressTransactionError('Bootstrap failed; retained incumbent restored and verified') from error
    # Never roll back an already-recorded adoption merely because guard
    # clearing failed: recovery must re-verify adoption and clear it separately.
    try:
        lease.recheck(); driver.clear_pending_guard(plan, 'caddy-identity')
    except Exception as error:
        raise IngressTransactionError('Adoption is recorded but bootstrap guard requires recovery') from error
    return 'adopted-and-verified'


def recover_under_lease(lease, journal: Path, driver, *, owner=None):
    """Roll an interrupted transaction back to the retained original container.

    The driver must positively verify identity and endpoint ownership before
    removing a candidate or reconnecting anything. It must be idempotent so an
    interrupted recovery may safely be retried. A terminal successful adoption
    is never silently reversed by this entry point.
    """
    owner = os.getuid() if owner is None else owner
    lease.recheck()
    plan = _load_plan(journal, owner)
    if driver.recovery_preflight(plan) is not True:
        raise IngressTransactionError('Bootstrap recovery preflight failed')
    if (journal / '09-adopted-and-verified.json').exists():
        lease.recheck(); driver.verify_adoption_and_retained_incumbent(plan)
        lease.recheck(); driver.clear_pending_guard(plan, 'caddy-identity')
        return 'adoption-confirmed'
    if (journal / '11-rolled-back-and-verified.json').exists() or (journal / '13-recovered-and-verified.json').exists():
        lease.recheck(); driver.verify_incumbent_tls_routes_and_identity(plan)
        lease.recheck(); driver.clear_pending_guard(plan, 'caddy')
        return 'rollback-confirmed'
    if not (journal / '12-recovery-intent.json').exists():
        _event(journal, 12, 'recovery-intent')
    try:
        lease.recheck(); driver.rollback_to_same_incumbent(plan)
        lease.recheck(); driver.verify_incumbent_tls_routes_and_identity(plan)
        lease.recheck(); driver.restore_owner_selection(plan)
        lease.recheck(); _event(journal, 13, 'recovered-and-verified')
        lease.recheck(); driver.clear_pending_guard(plan, 'caddy')
    except Exception as error:
        raise IngressTransactionError('Bootstrap recovery remains pending') from error
    return 'recovered-and-verified'
