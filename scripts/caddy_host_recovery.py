#!/usr/bin/env python3
"""Fixed-target, fail-closed boot recovery candidate for the Caddy handoff.

The old-writer access-boundary verifier is intentionally not implemented: the
real `boundary_proven` always refuses. Consequently this program cannot mutate
production in its current form. Its operation bodies are review candidates,
not a qualified host receiver or evidence of an actual cutover.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from caddy_bootstrap_candidate import recover_under_lease, static_fingerprint
from identity_ingress_transaction import IngressTransactionError
from ingress_lock import ingress_lock
from ingress_owner import DIRECTORY, _publish, _read_record, read_selection, settle_transition, clear_bootstrap_pending

JOURNALS = Path('/var/lib/open-hax/ingress/bootstrap')
GUARD = DIRECTORY / 'bootstrap-pending'
NEW = 'caddy-identity-caddy-1'


def command(args, *, allow_missing=False):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=90,
                            env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'})
    if result.returncode and not allow_missing:
        raise IngressTransactionError('Fixed host recovery command failed')
    return result


def boundary_proven():
    # A selector and a GitHub concurrency group do not restrict old SSH keys
    # or direct Docker/rsync access. Implement and independently review a real
    # principal/credential/access check before allowing any live mutation.
    return False


def guard_identity(path=GUARD):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        value = stream.read(66)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or
            info.st_mode & 0o022 or re.fullmatch(rb'[0-9a-f]{64}\n', value) is None):
        raise IngressTransactionError('Bootstrap guard is unsafe or malformed')
    return value[:-1].decode()


class HostRecoveryDriver:
    def __init__(self, lease, *, execute=command, boundary=boundary_proven):
        self.lease = lease
        self.execute = execute
        self.boundary = boundary

    def docker(self, *args, allow_missing=False):
        self.lease.recheck()
        return self.execute(['docker', *args], allow_missing=allow_missing)

    def inspect(self, name, *, allow_missing=False):
        result = self.docker('inspect', name, allow_missing=allow_missing)
        if result.returncode:
            return None
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != 1:
            raise IngressTransactionError('Unexpected Docker inspection result')
        return values[0]

    def checked_incumbent(self, plan):
        old = self.inspect(plan.incumbent_id)
        if old.get('Id') != plan.incumbent_id or static_fingerprint(old) != plan.static_fingerprint:
            raise IngressTransactionError('Retained incumbent identity or configuration changed')
        return old

    def checked_candidate(self, plan):
        candidate = self.inspect(NEW, allow_missing=True)
        if candidate is None:
            # A Docker inspect error is not proof of absence. Listing must
            # independently succeed and contain no exact candidate name.
            names = self.docker('ps', '-aq', '--filter', f'name=^/{NEW}$').stdout.strip()
            if names:
                raise IngressTransactionError('Candidate inspection is ambiguous')
            return None
        labels = (candidate.get('Config') or {}).get('Labels') or {}
        if (candidate.get('Id') == plan.incumbent_id or candidate.get('Image') != plan.incumbent_image or
                (candidate.get('Config') or {}).get('User') not in ('1000', '1000:1000') or
                labels.get('com.docker.compose.project') != 'caddy-identity' or
                labels.get('com.docker.compose.service') != 'caddy'):
            raise IngressTransactionError('Candidate identity, image or Compose ownership is unexpected')
        bindings = (candidate.get('HostConfig') or {}).get('PortBindings') or {}
        for host, published, internal in plan.ports:
            expected = [{'HostIp': host, 'HostPort': str(published)}]
            if bindings.get(f'{internal}/tcp') != expected:
                raise IngressTransactionError('Candidate port bindings differ from incumbent')
        if set(bindings) != {'80/tcp', '443/tcp'}:
            raise IngressTransactionError('Candidate has unexpected published ports')
        mounts = {m.get('Destination'): (m.get('Source'), m.get('RW')) for m in candidate.get('Mounts', [])}
        snapshot = '/srv/open-hax/state/caddy/nonroot-adoption-candidates/' + plan.incumbent_id
        required = {'/etc/caddy/identity-ingress': ('/srv/open-hax/state/caddy/identity-ingress', False),
                    '/etc/caddy/axxium-identity/did.json': ('/srv/open-hax/services/caddy/axxium-did.json', False),
                    '/etc/caddy/managed': ('/srv/open-hax/state/caddy/managed', False),
                    '/data': (snapshot + '/data', True), '/config': (snapshot + '/config', True)}
        # The original Caddyfile bind may remain alongside the new directory.
        optional = mounts.pop('/etc/caddy/Caddyfile', None)
        if optional not in (None, ('/srv/open-hax/services/caddy/Caddyfile', False)) or mounts != required:
            raise IngressTransactionError('Candidate mounts differ from the approved recovery topology')
        networks = (candidate.get('NetworkSettings') or {}).get('Networks') or {}
        if set(networks) - {'open-hax', 'caddy-dev-ingress', 'axxium-identity-ingress'}:
            raise IngressTransactionError('Candidate is attached to an unexpected network')
        for name, ip in (('caddy-dev-ingress', '172.31.255.2'), ('axxium-identity-ingress', '172.30.115.2')):
            if name in networks:
                current = networks[name].get('IPAddress') or (networks[name].get('IPAMConfig') or {}).get('IPv4Address')
                if current != ip:
                    raise IngressTransactionError('Candidate fixed network peer differs')
        return candidate

    def recovery_preflight(self, plan):
        if guard_identity() != plan.incumbent_id or self.boundary() is not True:
            return False
        self.checked_incumbent(plan)
        self.checked_candidate(plan)
        return True

    def rollback_to_same_incumbent(self, plan):
        old = self.checked_incumbent(plan)
        candidate = self.checked_candidate(plan)
        # Guard already blocks normal writers. Re-enter transitioning if the
        # interrupted process committed the new owner before its final marker.
        state = _read_record()
        if state == {'version': 1, 'phase': 'stable', 'project': 'caddy-identity'}:
            _publish(self.lease, DIRECTORY, state,
                     {'version': 1, 'phase': 'transition', 'from': 'caddy', 'to': 'caddy-identity'}, 0)
        elif state not in ({'version': 1, 'phase': 'stable', 'project': 'caddy'},
                           {'version': 1, 'phase': 'transition', 'from': 'caddy', 'to': 'caddy-identity'}):
            raise IngressTransactionError('Ingress selector cannot safely enter rollback')
        if candidate is not None:
            self.docker('rm', '-f', candidate['Id'])
        old = self.checked_incumbent(plan)
        if (old.get('State') or {}).get('Running'):
            return
        attached = (old.get('NetworkSettings') or {}).get('Networks') or {}
        if set(attached) - {'open-hax', 'caddy-dev-ingress'}:
            raise IngressTransactionError('Incumbent is attached to an unexpected network')
        aliases = dict(plan.network_aliases)
        for name, address in plan.networks:
            if name in attached:
                if name == 'caddy-dev-ingress' and (attached[name].get('IPAMConfig') or {}).get('IPv4Address') != address:
                    raise IngressTransactionError('Retained dev peer is not the fixed address')
                continue
            args = ['network', 'connect']
            if name == 'caddy-dev-ingress':
                args += ['--ip', address]
            # open-hax uses daemon-assigned addresses; preserve its aliases,
            # never assert that the earlier dynamic address can be reallocated.
            for alias in aliases[name]:
                args += ['--alias', alias]
            self.docker(*args, name, plan.incumbent_id)
        self.docker('start', plan.incumbent_id)

    def verify_incumbent_tls_routes_and_identity(self, plan):
        old = self.checked_incumbent(plan)
        if not (old.get('State') or {}).get('Running'):
            raise IngressTransactionError('Retained incumbent is not running')
        networks = (old.get('NetworkSettings') or {}).get('Networks') or {}
        if set(networks) != {'open-hax', 'caddy-dev-ingress'} or networks['caddy-dev-ingress'].get('IPAddress') != '172.31.255.2':
            raise IngressTransactionError('Restored incumbent network peers differ')
        for name, aliases in plan.network_aliases:
            if not set(aliases).issubset(set(networks[name].get('Aliases') or [])):
                raise IngressTransactionError('Restored incumbent aliases differ')
        self.docker('exec', '--user', '1000:1000', plan.incumbent_id, 'caddy', 'validate',
                    '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
        self.execute(['curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                      '--resolve', 'axxium.promethean.rest:443:127.0.0.1',
                      'https://axxium.promethean.rest/health'])
        # A single Axxium health response cannot establish that all incumbent
        # routes and certificate identities survived. Keep recovery pending
        # until the complete production probe set is independently qualified.
        raise IngressTransactionError('Complete incumbent route verification is not qualified')

    def restore_owner_selection(self, plan):
        state = _read_record()
        if state == {'version': 1, 'phase': 'stable', 'project': 'caddy'}:
            return
        settle_transition(self.lease, 'caddy',
                          lambda project, container: project == 'caddy' and container == 'caddy-caddy-1' and
                          self.checked_incumbent(plan)['Id'] == plan.incumbent_id)

    def clear_pending_guard(self, plan, target):
        if not GUARD.exists() and not GUARD.is_symlink():
            if read_selection()[0] != target:
                raise IngressTransactionError('Missing guard and unexpected settled owner')
            return
        clear_bootstrap_pending(self.lease, plan.incumbent_id, target)

    def verify_adoption_and_retained_incumbent(self, plan):
        # Full production validation of all host routes, fixed peers and TLS
        # requires the not-yet-qualified candidate receiver. Never clear an
        # adoption guard based only on Docker labels or a partial probe.
        raise IngressTransactionError('Candidate adoption verification is not qualified')


def main():
    if os.geteuid() != 0 or sys.argv[1:] != ['--boot']:
        raise SystemExit('Root and fixed --boot mode are required')
    if not GUARD.exists() and not GUARD.is_symlink():
        print('No pending Caddy bootstrap recovery')
        return
    identity = guard_identity()
    with ingress_lock() as lease:
        result = recover_under_lease(lease, JOURNALS / identity, HostRecoveryDriver(lease), owner=0)
    print(result)


if __name__ == '__main__':
    main()
