"""Read-only, fixed incident checks for the interrupted Axxium retry.

A held native lock is mandatory. Snapshots cannot prove that the Docker daemon
has no queued operation or exclude unrelated, non-cooperating host writers.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
PLAN_HASH = '6025ff0257a28711cc7847ca6935b97ffc3b11e4ccef76c3eb27a89f16bcafe1'
OPERATION = Path('/srv/open-hax/state/axxium/operations') / SOURCE
COLAB = Path('/srv/open-hax/services/colab-mcp')
REHEARSAL_ID = 'ae2c02a034407e5d51f9151094037d34a2146a0740dd8b28adea3caf7f473b18'
REHEARSAL_NAME = 'axxium-verify-bd802a8afaca950aebca44b3'
KNOXX_ID = '92346c9e74b47fc816eefaae4294b0e9ed1a20545bf3e9a3f40263561095d7b5'
KNOXX_NAME = 'knoxx-knoxx-backend-1'
PROTECTED_IDS = ('a63756b4a0133699c6adff752110454e3199fdd3b57244d3811716d6b9efa0d1',
                 'b66563abe4e7ad99cc1ae805dc676394524830f2d9adfcc5a62a46c82ecd0ea8',
                 'fe02d8d9860e30c0ed1e6f33031568de41407eeb53c6c1e439300d2c80199a49',
                 '5ef621be013356092ea07330a7e5dbc9c50b49cc55da46b09f3a935d0cac6992', REHEARSAL_ID)
CLONE = 'axxium_verify_e094a0612c789c9d41c2d7b2'
EXPECTED_PHASES = ('fence_public_api-pending','fence_public_api-succeeded',
                   'stop_incumbent-pending','stop_incumbent-succeeded',
                   'verified_before_backup-pending','verified_before_backup-succeeded',
                   'provision_identity-pending','provision_identity-succeeded',
                   'candidate_rollback_rehearsal-pending')


class Blocked(RuntimeError):
    pass


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
        raise Blocked('Private operation file protections differ')
    return path


def safe_clone_states(states):
    return states == [] or states == ['idle']


def check_colab_hold():
    """Refuse an existing, inaccessible, or aliased independent Colab hold."""
    for directory in reversed((COLAB, *COLAB.parents)):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or not os.access(directory, os.X_OK):
            raise Blocked('Colab hold state is inaccessible or aliased')
    try:
        (COLAB / '.colab-production-pending').lstat()
    except FileNotFoundError:
        return
    raise Blocked('Colab production transition is held')


def safe_knoxx_exec(args):
    """The single observed CLI shape; argv excludes the executable."""
    return (len(args) >= 6 and args[:3] == ['exec', '-i', '--user'] and
            bool(args[3]) and not args[3].startswith('-') and
            args[4] in (KNOXX_NAME, KNOXX_ID) and bool(args[5]))


def paths_overlap(first, second):
    first, second = os.path.realpath(first), os.path.realpath(second)
    try:
        return os.path.commonpath((first, second)) in (first, second)
    except ValueError:
        return True


def safe_knoxx_metadata(metadata, protected):
    """Check only direct mount and privilege boundaries; not payload isolation."""
    if not isinstance(metadata, dict) or not isinstance(protected, list) or len(protected) != len(PROTECTED_IDS):
        return False
    host = metadata.get('HostConfig')
    if (metadata.get('Id') != KNOXX_ID or metadata.get('Name') != '/' + KNOXX_NAME or
        not isinstance(host, dict) or not {'Privileged', 'PidMode', 'CapAdd'} <= host.keys() or
        host['Privileged'] is not False or host['PidMode'] not in ('', 'private') or
        host['CapAdd'] not in (None, [])):
        return False
    if {item.get('Id') for item in protected if isinstance(item, dict)} != set(PROTECTED_IDS):
        return False
    def sources(item):
        mounts = item.get('Mounts')
        if not isinstance(mounts, list):
            raise ValueError('Missing mount metadata')
        result = []
        for mount in mounts:
            if not isinstance(mount, dict):
                raise ValueError('Invalid mount metadata')
            source, destination = mount.get('Source'), mount.get('Destination')
            if not isinstance(source, str) or not os.path.isabs(source) or not isinstance(destination, str) or not os.path.isabs(destination):
                raise ValueError('Invalid mount paths')
            result.append((source, destination))
        return result
    try:
        own = sources(metadata)
        others = [src for item in protected for src, _ in sources(item)]
        sockets = ('/var/run/docker.sock', '/run/docker.sock', '/run/containerd/containerd.sock')
        for src, dst in own:
            if any(paths_overlap(src, socket) or paths_overlap(dst, socket) for socket in sockets):
                return False
            if any(paths_overlap(src, other) for other in others):
                return False
    except (TypeError, ValueError, OSError):
        return False
    return True


def inspect_process_targets():
    """Read selected metadata only, never container environment or command."""
    template = '{{json .Id}}\n{{json .Name}}\n{{json .HostConfig.Privileged}}\n{{json .HostConfig.PidMode}}\n{{json .HostConfig.CapAdd}}\n{{json .Mounts}}'
    def inspect(target):
        try:
            result = subprocess.run(['docker', 'inspect', '--format', template, target],
                                    capture_output=True, text=True, timeout=10, check=True)
            parts = [json.loads(line) for line in result.stdout.splitlines()]
            if len(parts) != 6:
                raise ValueError('Invalid metadata')
            ident, name, privileged, pid_mode, caps, mounts = parts
            return {'Id': ident, 'Name': name, 'HostConfig': {'Privileged': privileged,
                    'PidMode': pid_mode, 'CapAdd': caps}, 'Mounts': mounts}
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise Blocked('Could not validate Docker target boundaries') from error
    if not safe_knoxx_metadata(inspect(KNOXX_NAME), [inspect(i) for i in PROTECTED_IDS]):
        raise Blocked('Observed Knoxx Docker client boundaries differ')


def observed_clients():
    """Reject known active transaction clients; not proof about daemon queues."""
    targets = (b'axxium_minimal_release.py', REHEARSAL_NAME.encode(),
               SOURCE.encode(), CLONE.encode(), b'axxium-axxium-1',
               b'axxium-axxium-db-1', b'caddy-caddy-1',
               b'a63756b4a013', b'ae2c02a03440', b'b66563abe4e7', b'5ef621be0133',
               b'/srv/open-hax/services/axxium', b'--project-name\0axxium\0')
    me = os.getpid()
    with os.scandir('/proc') as entries:
        processes = list(entries)
    for entry in processes:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            raw = Path(entry.path, 'cmdline').read_bytes()
        except FileNotFoundError:
            continue
        except (PermissionError, OSError) as error:
            raise Blocked('Process visibility is incomplete') from error
        args = raw.split(b'\0')
        if not args or not args[0]:
            continue
        command = os.path.basename(os.fsdecode(args[0]))
        if command in ('docker', 'docker-compose'):
            if any(target in raw for target in targets):
                raise Blocked('A related Docker client remains active')
            try:
                argv = [os.fsdecode(arg) for arg in (args[1:-1] if args[-1] == b'' else args[1:])]
            except (ValueError, UnicodeError) as error:
                raise Blocked('Docker client arguments are unavailable') from error
            if command != 'docker' or not safe_knoxx_exec(argv):
                raise Blocked('An active Docker client prevents bounded recovery')
            inspect_process_targets()
        if command in ('python', 'python3', 'psql', 'pg_restore'):
            if any(target in raw for target in targets):
                raise Blocked('A related controller or Docker/database client remains active')


def validate(host, release, plan, plan_raw):
    """Validate the one restored incident under caller's already-held lock.

    This does not acquire a lock, archive files, start containers, or authorize
    production writes. It initializes the host's ordinary preflight observations
    so the caller can adopt the fresh operation after a reviewed handoff.
    """
    h, m = host, release
    if os.getuid() != 1000 or h.lock_fd is None:
        raise Blocked('Canonical owner and held lock required')
    h.recheck_lock()
    check_colab_hold()
    if h.plan_digest != PLAN_HASH or hashlib.sha256(plan_raw).hexdigest() != PLAN_HASH:
        raise Blocked('Reviewed plan hash differs')
    if m.validate_plan(json.loads(plan_raw)) != plan:
        raise Blocked('Reviewed plan differs')
    if json.loads(m.private_read(private_file(OPERATION / 'plan.json'))) != plan:
        raise Blocked('Operation plan differs from reviewed plan')
    pending = m.private_read(private_file(m.PENDING))
    expected_hold = {'schema': 'axxium-production-pending/v1', 'operation': str(OPERATION),
                     'source_sha': SOURCE, 'plan_sha256': PLAN_HASH}
    if pending != json.dumps(expected_hold, sort_keys=True).encode():
        raise Blocked('Production hold differs')
    h.pending_bytes = pending
    h.recheck_lock()
    phases = sorted(p.name for p in OPERATION.iterdir() if re.match(r'^[0-9]+-', p.name))
    expected = [f'{i:03d}-{name}.json' for i, name in enumerate(EXPECTED_PHASES)]
    if phases != expected:
        raise Blocked('Operation journal advanced or differs')
    for i, phase in enumerate(EXPECTED_PHASES):
        item = json.loads(m.private_read(private_file(OPERATION / expected[i])))
        name, status = phase.rsplit('-', 1)
        if item != {'sequence': i, 'phase': name, 'status': status}:
            raise Blocked('Operation journal contents differ')
    backup = json.loads(m.private_read(private_file(OPERATION / 'before-backup.json')))
    before = private_file(OPERATION / 'before.pgdump')
    if backup.get('path') != str(before) or backup.get('database') != CLONE or backup.get('sha256') != digest(before):
        raise Blocked('Verified backup metadata or hash differs')
    original = m.private_read(private_file(OPERATION / 'incumbent.Caddyfile'))
    if hashlib.sha256(original).hexdigest() != plan['expected']['caddyfile_sha256']:
        raise Blocked('Saved incumbent ingress differs')
    h.original = original
    h.maintenance = m.maintenance(original, plan['expected']['caddyfile_sha256'])
    h.final = m.prepare(original, plan['expected']['caddyfile_sha256'])
    h.unchanged_dependencies(plan)
    if m.CADDY.is_symlink() or m.CADDY.read_bytes() != original:
        raise Blocked('Restored public route differs')
    info = m.CADDY.stat()
    bound = h.command(['docker', 'exec', m.PROXY, 'stat', '-c', '%d:%i', '/etc/caddy/Caddyfile']).decode().strip()
    if bound != f'{info.st_dev}:{info.st_ino}':
        raise Blocked('Caddy bind inode differs')
    app = h.inspect(m.APP)
    if (app['Id'] != plan['expected']['app_container_id'] or app['Image'] != plan['expected']['app_image_id'] or
        app['State']['Status'] != 'running' or app['State'].get('Health', {}).get('Status') != 'healthy' or
        h.environment_hash(app) != plan['expected']['app_environment_sha256']):
        raise Blocked('Restored incumbent differs')
    h.original_layer(plan)
    h.old_environment = dict(x.split('=', 1) for x in app['Config']['Env'])
    networks = json.loads(h.command(['docker', 'network', 'inspect', 'axxium_database']))
    if (not isinstance(networks, list) or len(networks) != 1 or
            not isinstance(networks[0], dict) or networks[0].get('Internal') is not True):
        raise Blocked('Database rehearsal network is not isolated')
    proxy = h.inspect(m.PROXY)['NetworkSettings']['Networks']['open-hax']
    h.proxy_ip = proxy['IPAddress']
    if h.proxy_ip != plan['expected']['caddy_proxy_ip']:
        raise Blocked('Proxy network identity differs')
    app_net = app.get('NetworkSettings', {}).get('Networks', {}).get('open-hax') or {}
    if 'axxium' not in (app_net.get('Aliases') or []):
        raise Blocked('Incumbent Axxium alias differs')
    shared = json.loads(h.command(['docker', 'network', 'inspect', 'open-hax']))
    if not isinstance(shared, list) or len(shared) != 1 or not isinstance(shared[0], dict):
        raise Blocked('Shared network inspection unavailable')
    peers = shared[0].get('Containers') or {}
    if plan['expected']['app_container_id'] not in peers:
        raise Blocked('Incumbent missing from proxy network')
    for peer_id in peers:
        peer = h.inspect(peer_id)
        aliases = (peer.get('NetworkSettings', {}).get('Networks', {}).get('open-hax') or {}).get('Aliases') or []
        if 'axxium' in aliases and peer_id != plan['expected']['app_container_id']:
            raise Blocked('Unexpected Axxium network alias')
    rehearsal = h.inspect(REHEARSAL_ID)
    labeled = set(h.command(['docker', 'ps', '-aq', '--no-trunc', '--filter',
                             'label=open-hax.axxium-operation='+SOURCE]).decode().splitlines())
    env = dict(v.split('=', 1) for v in rehearsal['Config'].get('Env', []) if '=' in v)
    if (labeled != {REHEARSAL_ID} or rehearsal['Id'] != REHEARSAL_ID or
        rehearsal['Name'] != '/' + REHEARSAL_NAME or rehearsal['Image'] != plan['image_id'] or
        rehearsal['State']['Status'] != 'running' or rehearsal.get('ExecIDs') not in (None, []) or
        rehearsal['Config'].get('Labels', {}).get('open-hax.axxium-operation') != SOURCE or
        env.get('DB_HOST') != 'axxium-db' or env.get('DB_NAME') != CLONE or
        any(env.get(k) for k in ('DATABASE_URL', 'DB_URL', 'PGHOST', 'PGDATABASE', 'PGSERVICE')) or
        set(rehearsal['NetworkSettings']['Networks']) != {'axxium_database'}):
        raise Blocked('Rehearsal identity, network, or database differs')
    states = h.sql(CLONE, "SELECT state FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend' ORDER BY state").decode().splitlines()
    if not safe_clone_states(states):
        raise Blocked('Verification database clients differ')
    observed_clients()
    time.sleep(2)
    observed_clients()
    repeat = h.inspect(REHEARSAL_ID)
    if repeat['State']['Status'] != 'running' or repeat.get('ExecIDs') not in (None, []):
        raise Blocked('Rehearsal changed during observation')
    if h.http('/health')[0] != 200 or h.http('/xrpc/_health')[0] != 200:
        raise Blocked('Public Axxium or PDS health differs')
    h.validate_caddy(original)
    h.validate_caddy(h.maintenance)
    h.validate_caddy(h.final)
    database_size = int(h.sql('axxium', 'SELECT pg_database_size(current_database())').strip())
    if shutil.disk_usage(m.STATE).free < max(1024**3, database_size * 4):
        raise Blocked('Insufficient private backup space')
    binding = Path(m.__file__).resolve().parents[1] / 'production-binding.json'
    identity = h.command([sys.executable, str(Path(m.__file__).with_name('provision_axxium_colab_identity.py')),
                          '--preflight', '--binding-input', str(binding)])
    if json.loads(identity) != {'verified': True, 'status': 'complete'}:
        raise Blocked('Existing identity is not complete')
    check_colab_hold()
    h.recheck_lock()
    return pending


def verify(host, plan):
    """Use the calling release module's admitted plan and retained hold."""
    module = sys.modules.get(host.__class__.__module__)
    if module is None or not getattr(module, '__file__', None):
        raise Blocked('Release controller module unavailable')
    plan_path = Path(module.__file__).resolve().parents[1] / 'digitalocean/services/axxium/minimal-production-plan.json'
    raw = module.private_read(private_file(plan_path))
    pending = validate(host, module, plan, raw)
    host.operation = OPERATION
    host.pending_bytes = pending
    host.recheck_lock()
