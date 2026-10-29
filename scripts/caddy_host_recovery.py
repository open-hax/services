#!/usr/bin/env python3
"""Fixed-target, fail-closed boot recovery candidate for the Caddy handoff.

The old-writer access-boundary verifier is intentionally not implemented: the
real `boundary_proven` always refuses. Consequently this program cannot mutate
production in its current form. Its operation bodies are review candidates,
not a qualified host receiver or evidence of an actual cutover.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from caddy_bootstrap_candidate import recover_under_lease, static_fingerprint
from identity_ingress_transaction import IngressTransactionError
from ingress_lock import ingress_lock
from caddy_runtime_security import check_inspected_security
from ingress_owner import DIRECTORY, _publish, _read_record, read_selection, settle_transition, clear_bootstrap_pending, bootstrap_guard_pending

JOURNALS = Path('/var/lib/open-hax/ingress/bootstrap')
RUNTIME = Path('/srv/open-hax/services/caddy')
STATE = Path('/srv/open-hax/state/caddy')
GUARD = DIRECTORY / 'bootstrap-pending'
NEW = 'caddy-identity-caddy-1'
VALIDATION = 'caddy-identity-bootstrap-validation'
ROUTES = {'axxium.promethean.rest': '/health', 'knoxx.promethean.rest': '/',
          'proxx.promethean.rest': '/', 'open-hax.promethean.rest': '/',
          'knoxx-dev.promethean.rest': '/', 'opencode-dev.promethean.rest': '/',
          'shadow-dev.promethean.rest': '/'}
DEV_ROUTES = {'knoxx-dev.promethean.rest', 'opencode-dev.promethean.rest', 'shadow-dev.promethean.rest'}
CANDIDATE_COMMAND = ['caddy', 'run', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']


def environment_digest(values):
    if not isinstance(values, list) or any(not isinstance(value, str) or '=' not in value for value in values):
        raise IngressTransactionError('Container environment is malformed')
    pairs = [value.split('=', 1) for value in values]
    if len({name for name, _ in pairs}) != len(pairs):
        raise IngressTransactionError('Container environment contains duplicate names')
    return hashlib.sha256(json.dumps(dict(pairs), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def command(args, *, allow_missing=False, input_bytes=None):
    options = {'stdin': subprocess.DEVNULL} if input_bytes is None else {'input': input_bytes}
    result = subprocess.run(args, **options, stdout=subprocess.PIPE,
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
    def __init__(self, lease, *, execute=command, boundary=boundary_proven, journal=None, journal_owner=0):
        self.lease = lease
        self.execute = execute
        self.boundary = boundary
        self.journal = journal
        self.journal_owner = journal_owner

    def probe_routes(self):
        results = {}
        for host, path in ROUTES.items():
            self.lease.recheck()
            result = self.execute(['curl', '--silent', '--max-filesize', '2097152', '--write-out',
                                   '\n__INGRESS_META__%{http_code}\t%{redirect_url}\t%{content_type}',
                                   '--max-time', '15', '--resolve', f'{host}:443:127.0.0.1',
                                   f'https://{host}{path}'], allow_missing=True)
            try:
                body, metadata = result.stdout.rsplit(b'\n__INGRESS_META__', 1)
                code, redirect, content_type = metadata.decode('utf-8', errors='strict').split('\t')
            except (ValueError, UnicodeDecodeError) as error:
                raise IngressTransactionError('Unexpected route-probe metadata') from error
            if re.fullmatch(r'[0-9]{3}', code) is None:
                raise IngressTransactionError('Unexpected route-probe result')
            if result.returncode and code != '000':
                raise IngressTransactionError('Inconsistent route-probe failure')
            if len(body) > 2*1024*1024 or len(redirect) > 4096 or len(content_type) > 512:
                raise IngressTransactionError('Route-probe response exceeds bounds')
            if code == '000':
                results[host] = {'status': code, 'outcome': 'tls-or-connect-error'}
            else:
                if host == 'axxium.promethean.rest':
                    try:
                        health = json.loads(body)
                        semantic = {'service': health['service'], 'status': health['status']}
                    except (KeyError, TypeError, ValueError) as error:
                        raise IngressTransactionError('Axxium health response is malformed') from error
                    if semantic != {'service': 'axxium', 'status': 'ok'}:
                        raise IngressTransactionError('Axxium health is not healthy')
                    body = json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode()
                results[host] = {'status': code, 'redirect': redirect, 'content_type': content_type,
                                 'body_sha256': hashlib.sha256(body).hexdigest()}
        return results

    def baseline(self):
        if self.journal is None:
            raise IngressTransactionError('Route baseline journal is unavailable')
        fd = os.open(self.journal / 'routes.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.journal_owner or info.st_nlink != 1 or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096):
                raise IngressTransactionError('Route baseline is unsafe')
            value = json.load(stream)
        def valid(entry):
            if not isinstance(entry, dict) or re.fullmatch(r'[0-9]{3}', str(entry.get('status', ''))) is None:
                return False
            if entry['status'] == '000':
                return entry == {'status': '000', 'outcome': 'tls-or-connect-error'}
            return (set(entry) == {'status', 'redirect', 'content_type', 'body_sha256'} and
                    isinstance(entry['redirect'], str) and len(entry['redirect']) <= 4096 and
                    isinstance(entry['content_type'], str) and len(entry['content_type']) <= 512 and
                    isinstance(entry['body_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', entry['body_sha256']) is not None)
        if (not isinstance(value, dict) or set(value) != set(ROUTES) or not all(valid(x) for x in value.values()) or
                any(value[host]['status'] not in {'200', '301', '302', '303', '307', '308', '401', '403'} for host in set(ROUTES)-DEV_ROUTES)):
            raise IngressTransactionError('Route baseline lacks valid public endpoints')
        return value

    def verify_route_baseline(self):
        if self.probe_routes() != self.baseline():
            raise IngressTransactionError('Public or development route behavior differs from incumbent baseline')

    def baseline_caddy_sha(self, name='caddyfile.sha256'):
        if self.journal is None:
            raise IngressTransactionError('Caddyfile baseline journal is unavailable')
        if name not in ('caddyfile.sha256', 'candidate.sha256'):
            raise IngressTransactionError('Unknown Caddyfile baseline')
        fd = os.open(self.journal / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            value = stream.read(66)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.journal_owner or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600 or re.fullmatch(rb'[0-9a-f]{64}\n', value) is None):
            raise IngressTransactionError('Caddyfile baseline is unsafe')
        return value[:-1].decode()

    def runtime_contract(self):
        if self.journal is None:
            raise IngressTransactionError('Candidate runtime contract is unavailable')
        fd = os.open(self.journal / 'candidate-runtime.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.journal_owner or info.st_nlink != 1 or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096):
                raise IngressTransactionError('Candidate runtime contract is unsafe')
            value = json.load(stream)
        if (not isinstance(value, dict) or set(value) != {'command', 'entrypoint', 'environment_sha256'} or
                value['command'] != CANDIDATE_COMMAND or value['entrypoint'] is not None or
                not isinstance(value['environment_sha256'], str) or
                re.fullmatch(r'[0-9a-f]{64}', value['environment_sha256']) is None):
            raise IngressTransactionError('Candidate runtime contract is invalid')
        return value

    def docker(self, *args, allow_missing=False, input_bytes=None):
        self.lease.recheck()
        if input_bytes is None:
            return self.execute(['docker', *args], allow_missing=allow_missing)
        return self.execute(['docker', *args], allow_missing=allow_missing, input_bytes=input_bytes)

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

    def checked_candidate(self, plan, *, name=NEW):
        candidate = self.inspect(name, allow_missing=True)
        if candidate is None:
            # A Docker inspect error is not proof of absence. Listing must
            # independently succeed and contain no exact candidate name.
            names = self.docker('ps', '-aq', '--filter', f'name=^/{NEW}$').stdout.strip()
            if names:
                raise IngressTransactionError('Candidate inspection is ambiguous')
            return None
        check_inspected_security(candidate)
        labels = (candidate.get('Config') or {}).get('Labels') or {}
        if (candidate.get('Id') == plan.incumbent_id or candidate.get('Image') != plan.incumbent_image or
                (candidate.get('Config') or {}).get('User') not in ('1000', '1000:1000') or
                labels.get('com.docker.compose.project') != 'caddy-identity' or
                labels.get('com.docker.compose.service') != 'caddy'):
            raise IngressTransactionError('Candidate identity, image or Compose ownership is unexpected')
        config = candidate.get('Config') or {}
        contract = self.runtime_contract()
        if (config.get('Cmd') != contract['command'] or config.get('Entrypoint') != contract['entrypoint'] or
                environment_digest(config.get('Env')) != contract['environment_sha256']):
            raise IngressTransactionError('Candidate command, entrypoint or environment differs from reviewed model')
        bindings = (candidate.get('HostConfig') or {}).get('PortBindings') or {}
        for host, published, internal in plan.ports:
            expected = [{'HostIp': host, 'HostPort': str(published)}]
            if bindings.get(f'{internal}/tcp') != expected:
                raise IngressTransactionError('Candidate port bindings differ from incumbent')
        if set(bindings) != {'80/tcp', '443/tcp'}:
            raise IngressTransactionError('Candidate has unexpected published ports')
        mount_entries = candidate.get('Mounts', [])
        mounts = {m.get('Destination'): (m.get('Source'), m.get('RW')) for m in mount_entries}
        if len(mounts) != len(mount_entries):
            raise IngressTransactionError('Candidate mount destinations are ambiguous')
        snapshot = STATE / 'nonroot-adoption-candidates' / plan.incumbent_id
        required = {'/etc/caddy/identity-ingress': (str(STATE / 'identity-ingress'), False),
                    '/etc/caddy/axxium-identity/did.json': (str(RUNTIME / 'axxium-did.json'), False),
                    '/etc/caddy/managed': (str(STATE / 'managed'), False),
                    '/data': (str(snapshot / 'data'), True), '/config': (str(snapshot / 'config'), True)}
        # The original Caddyfile bind may remain alongside the new directory.
        optional = mounts.pop('/etc/caddy/Caddyfile', None)
        if optional not in (None, (str(RUNTIME / 'Caddyfile'), False)) or mounts != required:
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

    def checked_validation(self, plan, *, name=VALIDATION):
        validator = self.inspect(name, allow_missing=True)
        if validator is None:
            names = self.docker('ps', '-aq', '--filter', f'name=^/{VALIDATION}$').stdout.strip()
            if names:
                raise IngressTransactionError('Validation container inspection is ambiguous')
            return None
        check_inspected_security(validator)
        config = validator.get('Config') or {}
        labels = config.get('Labels') or {}
        host = validator.get('HostConfig') or {}
        if (validator.get('Id') == plan.incumbent_id or validator.get('Image') != plan.incumbent_image or
                config.get('User') not in ('1000', '1000:1000') or
                labels.get('com.docker.compose.project') != 'caddy-validation' or
                labels.get('com.docker.compose.service') != 'caddy' or
                config.get('Entrypoint') != ['caddy'] or
                config.get('Cmd') != ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile'] or
                host.get('NetworkMode') != 'none' or host.get('PortBindings')):
            raise IngressTransactionError('Validation container identity or isolation differs')
        if environment_digest(config.get('Env')) != self.runtime_contract()['environment_sha256']:
            raise IngressTransactionError('Validation environment differs from reviewed model')
        mount_entries = validator.get('Mounts', [])
        mounts = {m.get('Destination'): (m.get('Source'), m.get('RW')) for m in mount_entries}
        if len(mounts) != len(mount_entries):
            raise IngressTransactionError('Validation container mount destinations are ambiguous')
        snapshot = STATE / 'nonroot-adoption-candidates' / plan.incumbent_id
        required = {'/etc/caddy/identity-ingress': (str(STATE / 'identity-ingress'), False),
                    '/etc/caddy/axxium-identity/did.json': (str(RUNTIME / 'axxium-did.json'), False),
                    '/etc/caddy/managed': (str(STATE / 'managed'), False),
                    '/data': (str(snapshot / 'data'), True), '/config': (str(snapshot / 'config'), True)}
        optional = mounts.pop('/etc/caddy/Caddyfile', None)
        if optional not in (None, (str(RUNTIME / 'Caddyfile'), False)) or mounts != required:
            raise IngressTransactionError('Validation container mounts differ')
        return validator

    def recovery_preflight(self, plan):
        if guard_identity() != plan.incumbent_id or self.boundary() is not True:
            return False
        self.checked_incumbent(plan)
        self.checked_candidate(plan)
        self.checked_validation(plan)
        return True

    def rollback_to_same_incumbent(self, plan):
        old = self.checked_incumbent(plan)
        candidate = self.checked_candidate(plan)
        validator = self.checked_validation(plan)
        # Guard already blocks normal writers. Re-enter transitioning if the
        # interrupted process committed the new owner before its final marker.
        state = _read_record()
        if state == {'version': 1, 'phase': 'stable', 'project': 'caddy-identity'}:
            _publish(self.lease, DIRECTORY, state,
                     {'version': 1, 'phase': 'transition', 'from': 'caddy', 'to': 'caddy-identity'}, 0)
        elif state not in ({'version': 1, 'phase': 'stable', 'project': 'caddy'},
                           {'version': 1, 'phase': 'transition', 'from': 'caddy', 'to': 'caddy-identity'}):
            raise IngressTransactionError('Ingress selector cannot safely enter rollback')
        if validator is not None:
            self.docker('rm', '-f', validator['Id'])
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
        actual = self.docker('exec', '--user', '1000:1000', plan.incumbent_id, 'cat', '/etc/caddy/Caddyfile').stdout
        if hashlib.sha256(actual).hexdigest() != self.baseline_caddy_sha():
            raise IngressTransactionError('Restored incumbent Caddyfile differs from baseline')
        self.docker('exec', '--user', '1000:1000', plan.incumbent_id, 'caddy', 'validate',
                    '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
        self.verify_route_baseline()

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
        old = self.checked_incumbent(plan)
        candidate = self.checked_candidate(plan)
        if (old.get('State') or {}).get('Running') or candidate is None or not (candidate.get('State') or {}).get('Running'):
            raise IngressTransactionError('Incumbent retention or candidate running state is incorrect')
        networks = (candidate.get('NetworkSettings') or {}).get('Networks') or {}
        if set(networks) != {'open-hax', 'caddy-dev-ingress', 'axxium-identity-ingress'}:
            raise IngressTransactionError('Candidate network set is incomplete')
        if 'caddy' not in (networks['open-hax'].get('Aliases') or []):
            raise IngressTransactionError('Candidate lost the open-hax caddy alias')
        actual = self.docker('exec', '--user', '1000:1000', candidate['Id'], 'cat', '/etc/caddy/identity-ingress/Caddyfile').stdout
        if hashlib.sha256(actual).hexdigest() != self.baseline_caddy_sha('candidate.sha256'):
            raise IngressTransactionError('Candidate Caddyfile differs from reviewed baseline')
        self.docker('exec', '--user', '1000:1000', candidate['Id'], 'caddy', 'validate',
                    '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile')
        upstream = self.inspect('axxium-axxium-1')
        env = (upstream.get('Config') or {}).get('Env') or []
        identity = ((upstream.get('NetworkSettings') or {}).get('Networks') or {}).get('axxium-identity-ingress') or {}
        if env.count('AXXIUM_TRUST_PROXY=172.30.115.2') != 1 or identity.get('IPAddress') != '172.30.115.3':
            raise IngressTransactionError('Axxium dedicated proxy peer or trust differs')
        self.verify_route_baseline()
        result = self.execute(['curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                               '--resolve', 'axxium.promethean.rest:443:127.0.0.1',
                               'https://axxium.promethean.rest/oidc/.well-known/openid-configuration'])
        discovery = json.loads(result.stdout)
        if (discovery.get('issuer') != 'https://axxium.promethean.rest/oidc' or
                discovery.get('jwks_uri') != 'https://axxium.promethean.rest/oidc/jwks'):
            raise IngressTransactionError('Axxium OIDC discovery differs from the qualified issuer')


def main():
    if os.geteuid() != 0 or sys.argv[1:] != ['--boot']:
        raise SystemExit('Root and fixed --boot mode are required')
    with ingress_lock() as lease:
        if not bootstrap_guard_pending(GUARD):
            print('No pending Caddy bootstrap recovery')
            return
        identity = guard_identity()
        journal = JOURNALS / identity
        result = recover_under_lease(lease, journal, HostRecoveryDriver(lease, journal=journal), owner=0)
    print(result)


if __name__ == '__main__':
    main()
