"""Initial Caddy bootstrap driver, gated off until production qualification.

There is deliberately no CLI entry point. `boundary_proven` and
`deployment_qualified` both refuse in production. Fake-Docker tests exercise
the methods; neither tests nor an on-disk marker qualify the live boundary.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from axxium_identity_candidate import candidate as identity_candidate
from caddy_bootstrap_candidate import production_plan, _event
from caddy_host_recovery import HostRecoveryDriver, command, boundary_proven, VALIDATION
from caddy_state_candidate import copy_verified, inventory
from identity_ingress_transaction import IngressTransactionError
from ingress_owner import DIRECTORY, read_selection, begin_transition, settle_transition, mark_bootstrap_pending
from ingress_pending import refuse_pending

RUNTIME = Path('/srv/open-hax/services/caddy')
STATE = Path('/srv/open-hax/state/caddy')
STAGED = Path('/etc/open-hax/ingress/candidate')
VALIDATION_OVERLAY = b'services:\n  caddy:\n    network_mode: none\n    networks: !reset null\n'


def deployment_qualified():
    # The specific source, image, actor binding and required promotion gates
    # must be independently verified before a real production implementation.
    return False


def _mount_map(service):
    result = {}
    for mount in service.get('volumes', []):
        if not isinstance(mount, dict) or mount.get('type') != 'bind' or mount.get('target') in result:
            raise IngressTransactionError('Compose volume model is ambiguous')
        result[mount['target']] = (mount.get('source'), mount.get('read_only', False))
    return result


def validate_candidate_models(legacy, candidate, plan):
    """Allow only reviewed image/user/config/state/network changes."""
    if set(legacy.get('services', {})) != {'caddy'} or set(candidate.get('services', {})) != {'caddy'}:
        raise IngressTransactionError('Compose service ownership differs')
    before, after = legacy['services']['caddy'], candidate['services']['caddy']
    permitted = {'image', 'user', 'command', 'healthcheck', 'volumes', 'networks'}
    if {k: v for k, v in before.items() if k not in permitted} != {k: v for k, v in after.items() if k not in permitted}:
        raise IngressTransactionError('Compose changes unrelated service behavior')
    if after.get('image') != plan.incumbent_image or after.get('user') not in ('1000', '1000:1000'):
        raise IngressTransactionError('Candidate image or user is not pinned')
    if after.get('command') != ['caddy', 'run', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']:
        raise IngressTransactionError('Candidate command differs')
    health = after.get('healthcheck') or {}
    if health.get('test') != ['CMD', 'caddy', 'validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']:
        raise IngressTransactionError('Candidate healthcheck differs')
    if {k: v for k, v in (before.get('healthcheck') or {}).items() if k != 'test'} != {k: v for k, v in health.items() if k != 'test'}:
        raise IngressTransactionError('Candidate health timing differs')
    old_mounts, new_mounts = _mount_map(before), _mount_map(after)
    if old_mounts != {'/etc/caddy/Caddyfile': (str(RUNTIME / 'Caddyfile'), True),
                      '/etc/caddy/axxium-identity/did.json': (str(RUNTIME / 'axxium-did.json'), True),
                      '/etc/caddy/managed': (str(STATE / 'managed'), True),
                      '/data': (str(STATE / 'data'), False), '/config': (str(STATE / 'config'), False)}:
        raise IngressTransactionError('Legacy Compose mounts differ from incumbent contract')
    snapshot = STATE / 'nonroot-adoption-candidates' / plan.incumbent_id
    required = dict(old_mounts)
    required.update({'/data': (str(snapshot / 'data'), False), '/config': (str(snapshot / 'config'), False),
                     '/etc/caddy/identity-ingress': (str(STATE / 'identity-ingress'), True)})
    if new_mounts != required:
        raise IngressTransactionError('Candidate Compose mounts differ')
    old_networks, new_networks = before.get('networks') or {}, after.get('networks') or {}
    if (set(old_networks) != {'default', 'dev-ingress'} or
            set(new_networks) != {'default', 'dev-ingress', 'axxium-identity-ingress'} or
            old_networks['default'] != new_networks['default'] or old_networks['dev-ingress'] != new_networks['dev-ingress'] or
            (new_networks['dev-ingress'] or {}).get('ipv4_address') != '172.31.255.2' or
            (new_networks['axxium-identity-ingress'] or {}).get('ipv4_address') != '172.30.115.2' or
            set(new_networks['axxium-identity-ingress'] or {}) != {'ipv4_address'}):
        raise IngressTransactionError('Candidate network topology differs')
    for key, expected in (('default', 'open-hax'), ('dev-ingress', 'caddy-dev-ingress'),
                          ('axxium-identity-ingress', 'axxium-identity-ingress')):
        network = (candidate.get('networks') or {}).get(key) or {}
        if set(network) != {'name', 'external'} or network.get('name') != expected or network.get('external') is not True:
            raise IngressTransactionError('Candidate must use the existing fixed networks')
    expected_ports = {(host, published, internal) for host, published, internal in plan.ports}
    try:
        actual_ports = {(p.get('host_ip', ''), int(p['published']), int(p['target'])) for p in before.get('ports', [])
                        if p.get('protocol', 'tcp') == 'tcp'}
    except (TypeError, ValueError, KeyError) as error:
        raise IngressTransactionError('Legacy Compose ports are malformed') from error
    if actual_ports != expected_ports or len(before.get('ports', [])) != len(expected_ports):
        raise IngressTransactionError('Legacy Compose ports differ from incumbent')


def validate_isolated_model(candidate, isolated):
    if set(isolated.get('services', {})) != {'caddy'}:
        raise IngressTransactionError('Isolated validation service differs')
    before = candidate['services']['caddy']
    after = isolated['services']['caddy']
    if (after.get('network_mode') != 'none' or after.get('networks') not in (None, {}) or
            {k: v for k, v in before.items() if k not in ('networks', 'network_mode')} !=
            {k: v for k, v in after.items() if k not in ('networks', 'network_mode')}):
        raise IngressTransactionError('Validation overlay changes more than network isolation')


def read_root_file(path, limit=2*1024*1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > limit:
            raise IngressTransactionError('Bootstrap candidate input is unsafe')
        return stream.read(limit + 1)


def verify_quiescent_snapshot(source, destination, *, owner=1000, group=1000):
    fd = os.open(destination / 'source-manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 4*1024*1024:
            raise IngressTransactionError('Snapshot manifest is unsafe')
        manifest = json.load(stream)
    if set(manifest) != {'data', 'config'}:
        raise IngressTransactionError('Snapshot manifest is malformed')
    for section in ('data', 'config'):
        expected = manifest[section]
        if inventory(source / section) != expected:
            raise IngressTransactionError('Quiescent incumbent state changed after copy')
        actual = inventory(destination / section)
        if set(actual) != set(expected):
            raise IngressTransactionError('Snapshot paths changed during validation')
        for path, item in expected.items():
            other = actual[path]
            if (other['kind'] != item['kind'] or other['uid'] != owner or other['gid'] != group or
                    other['mode'] != (0o700 if item['kind'] == 'directory' else 0o600) or
                    item['kind'] == 'file' and (other['size'], other['sha256']) != (item['size'], item['sha256'])):
                raise IngressTransactionError('Snapshot bytes or permissions changed during validation')


class HostBootstrapDriver(HostRecoveryDriver):
    def __init__(self, lease, journal, *, execute=command, boundary=boundary_proven, qualification=deployment_qualified):
        super().__init__(lease, execute=execute, boundary=boundary, journal=journal)
        self.qualification = qualification

    def preflight(self, plan):
        if self.boundary() is not True or self.qualification() is not True:
            return False
        self.lease.recheck()
        if read_selection() != ('caddy', 'caddy-caddy-1'):
            raise IngressTransactionError('Legacy ingress is not the selected owner')
        refuse_pending(owner=1000)
        current = self.inspect(plan.incumbent_id)
        if production_plan(current) != plan or not (current.get('State') or {}).get('Running'):
            raise IngressTransactionError('Incumbent changed before bootstrap')
        if self.checked_candidate(plan) is not None:
            raise IngressTransactionError('Candidate project already exists')
        staged_info = STAGED.lstat()
        if not stat.S_ISDIR(staged_info.st_mode) or staged_info.st_uid != 0 or staged_info.st_mode & 0o022:
            raise IngressTransactionError('Bootstrap staging directory is not root-controlled')
        existing = (RUNTIME / 'Caddyfile').read_bytes()
        inside = self.docker('exec', '--user', '1000:1000', plan.incumbent_id, 'cat', '/etc/caddy/Caddyfile').stdout
        if inside != existing:
            raise IngressTransactionError('Running Caddy does not see the selected incumbent baseline')
        expected = identity_candidate(existing, hashlib.sha256(existing).hexdigest())
        if read_root_file(STAGED / 'Caddyfile') != expected:
            raise IngressTransactionError('Candidate would change unrelated routes')
        config_dir = STATE / 'identity-ingress'
        directory = config_dir.lstat()
        target = config_dir / 'Caddyfile'
        info = target.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 1000 or directory.st_mode & 0o022 or
                not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_nlink != 1 or info.st_mode & 0o022 or
                target.read_bytes() != expected):
            raise IngressTransactionError('Inactive directory-bind candidate is missing or differs')
        baseline = self.probe_routes()
        if self.probe_routes() != baseline:
            raise IngressTransactionError('Route baseline changed between consecutive observations')
        fd = os.open(self.journal / 'routes.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(baseline, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.journal / 'caddyfile.sha256', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(hashlib.sha256(existing).hexdigest() + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.journal / 'candidate.sha256', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(hashlib.sha256(expected).hexdigest() + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        dirfd = os.open(self.journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        self.baseline()  # reject unavailable core routes before any host effects
        return True

    def prevalidate_nonroot(self, plan):
        # Exec as UID 1000 inherits the exact incumbent environment and imports;
        # send reviewed config on stdin rather than expose credentials in argv.
        self.docker('exec', '-i', '--user', '1000:1000', plan.incumbent_id,
                    'caddy', 'validate', '--config', '/dev/stdin', '--adapter', 'caddyfile',
                    input_bytes=read_root_file(STAGED / 'Caddyfile'))
        return True

    def adoption_preflight(self, plan):
        # Both fixed Compose files must be root-controlled, and the complete
        # semantic model is compared before any ingress change. The candidate
        # overlay must already pin the exact image and private state paths.
        read_root_file(STAGED / 'compose.bootstrap.yaml')
        if read_root_file(STAGED / 'compose.validation.yaml') != VALIDATION_OVERLAY:
            raise IngressTransactionError('Validation overlay differs from reviewed bytes')
        base = self.execute(['docker', 'compose', '--project-name', 'caddy', '--env-file', str(RUNTIME / '.env'),
                             '-f', str(RUNTIME / 'compose.yaml'), 'config', '--format', 'json'])
        new = self.execute(['docker', 'compose', '--project-name', 'caddy-identity', '--env-file', str(RUNTIME / '.env'),
                            '-f', str(RUNTIME / 'compose.yaml'), '-f', str(STAGED / 'compose.bootstrap.yaml'),
                            'config', '--format', 'json'])
        isolated = self.execute(['docker', 'compose', '--project-name', 'caddy-identity', '--env-file', str(RUNTIME / '.env'),
                                 '-f', str(RUNTIME / 'compose.yaml'), '-f', str(STAGED / 'compose.bootstrap.yaml'),
                                 '-f', str(STAGED / 'compose.validation.yaml'), 'config', '--format', 'json'])
        old_model, new_model = json.loads(base.stdout), json.loads(new.stdout)
        validate_candidate_models(old_model, new_model, plan)
        validate_isolated_model(new_model, json.loads(isolated.stdout))
        live = self.checked_incumbent(plan)
        actual_env = dict(value.split('=', 1) for value in (live['Config'].get('Env') or []) if '=' in value)
        for name in ('PATH', 'CADDY_VERSION', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
            actual_env.pop(name, None)
        if actual_env != (old_model['services']['caddy'].get('environment') or {}):
            raise IngressTransactionError('Legacy Compose environment differs from the running incumbent')
        if live['Config'].get('Cmd') != ['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile']:
            raise IngressTransactionError('Legacy Caddy command differs from the running incumbent')
        return True

    def mark_pending_guard(self, plan):
        mark_bootstrap_pending(self.lease, plan.incumbent_id)

    def begin_owner_transition(self, plan):
        begin_transition(self.lease)

    def stop_incumbent(self, plan):
        current = self.checked_incumbent(plan)
        if not (current.get('State') or {}).get('Running'):
            raise IngressTransactionError('Incumbent is not running before stop')
        self.docker('stop', '--time', '30', plan.incumbent_id)

    def snapshot_quiescent_and_verify(self, plan):
        old = self.checked_incumbent(plan)
        if (old.get('State') or {}).get('Running'):
            raise IngressTransactionError('Cannot snapshot a running incumbent')
        copy_verified(STATE, STATE / 'nonroot-adoption-candidates' / plan.incumbent_id)
        self.adoption_preflight(plan)
        # Validate the actual candidate user, env, mounts and fresh snapshot
        # without publishing any port or attaching to a production network.
        if self.checked_validation(plan) is not None:
            raise IngressTransactionError('Prior bootstrap validation container still exists')
        _event(self.journal, 30, 'validation-container-intent')
        self.execute(['docker', 'compose', '--project-name', 'caddy-identity', '--env-file', str(RUNTIME / '.env'),
                      '-f', str(RUNTIME / 'compose.yaml'), '-f', str(STAGED / 'compose.bootstrap.yaml'),
                      '-f', str(STAGED / 'compose.validation.yaml'),
                      'run', '--rm', '--name', VALIDATION, '--no-deps', '--entrypoint', 'caddy', 'caddy',
                      'validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile'])
        if self.checked_validation(plan) is not None:
            raise IngressTransactionError('Bootstrap validation container did not exit cleanly')
        verify_quiescent_snapshot(STATE, STATE / 'nonroot-adoption-candidates' / plan.incumbent_id)

    def release_incumbent_endpoints(self, plan):
        old = self.checked_incumbent(plan)
        if (old.get('State') or {}).get('Running'):
            raise IngressTransactionError('Cannot release a running incumbent')
        attached = (old.get('NetworkSettings') or {}).get('Networks') or {}
        if set(attached) != {'open-hax', 'caddy-dev-ingress'}:
            raise IngressTransactionError('Incumbent network set changed')
        for name, _ in plan.networks:
            self.docker('network', 'disconnect', '--force', name, plan.incumbent_id)

    def start_nonroot_candidate(self, plan):
        self.lease.recheck()
        self.adoption_preflight(plan)
        config = (STATE / 'identity-ingress/Caddyfile').read_bytes()
        if hashlib.sha256(config).hexdigest() != self.baseline_caddy_sha('candidate.sha256'):
            raise IngressTransactionError('Staged candidate changed before start')
        self.execute(['docker', 'compose', '--project-name', 'caddy-identity', '--env-file', str(RUNTIME / '.env'),
                      '-f', str(RUNTIME / 'compose.yaml'), '-f', str(STAGED / 'compose.bootstrap.yaml'),
                      'up', '-d', '--no-deps', '--pull', 'never', '--no-build', 'caddy'])
        self.checked_candidate(plan)

    def verify_candidate_tls_routes_peer(self, plan):
        self.verify_adoption_and_retained_incumbent(plan)

    def adopt_without_removing_incumbent(self, plan):
        # Candidate already has a distinct fixed Compose project; adoption is
        # only confirmed after both project identities and endpoints are read.
        self.checked_candidate(plan)
        self.checked_incumbent(plan)

    def commit_owner_selection(self, plan):
        settle_transition(self.lease, 'caddy-identity',
                          lambda project, container: project == 'caddy-identity' and container == 'caddy-identity-caddy-1'
                          and self.verify_adoption_and_retained_incumbent(plan) is None)
