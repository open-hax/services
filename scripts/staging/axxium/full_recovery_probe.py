#!/usr/bin/env python3
"""Disposable-VM fixture driver for the actual interrupted recovery sequence.

This is preparation, not evidence of a successful staging run. It requires a
fresh GitHub-hosted VM and loopback-only process network namespace; the fixture
containers are on internal-only Docker bridges. It uses the candidate as its
incumbent and cannot prove compatibility with the former production image.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import identity_fixture
import network_fixture
import fixture_netns

IMAGE = 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
IMAGE_CONFIG = 'sha256:c45e58aadb02622183ccf60a971da2669fc033231b677c12e963920234b9b542'
POSTGRES = 'sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
CADDY = 'sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17'
ROOT = Path('/srv/open-hax')
SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
CLONE = 'axxium_verify_0123456789abcdef01234567'
ORPHAN = 'axxium-verify-0123456789abcdef01234567'

# Only static guard messages may leave this synthetic fixture. Never print an
# arbitrary exception, since it could contain process output or private data.
SAFE_GUARD_REASONS = frozenset({
    'Private operation file protections differ',
    'Colab hold state is inaccessible or aliased',
    'Colab production transition is held',
    'Could not validate Docker target boundaries',
    'Observed Knoxx Docker client boundaries differ',
    'Process visibility is incomplete',
    'A related Docker client remains active',
    'Docker client arguments are unavailable',
    'An active Docker client prevents bounded recovery',
    'A related controller or Docker/database client remains active',
    'Canonical owner and held lock required',
    'Reviewed plan hash differs',
    'Reviewed plan differs',
    'Operation plan differs from reviewed plan',
    'Production hold differs',
    'Operation journal advanced or differs',
    'Operation journal contents differ',
    'Verified backup metadata or hash differs',
    'Saved incumbent ingress differs',
    'Restored public route differs',
    'Caddy bind inode differs',
    'Restored incumbent differs',
    'Database rehearsal network is not isolated',
    'Proxy network identity differs',
    'Incumbent Axxium alias differs',
    'Shared network inspection unavailable',
    'Incumbent missing from proxy network',
    'Unexpected Axxium network alias',
    'Rehearsal identity, network, or database differs',
    'Verification database clients differ',
    'Rehearsal changed during observation',
    'Public Axxium or PDS health differs',
    'Insufficient private backup space',
    'Existing identity is not complete',
    'Release controller module unavailable',
})


def enable_fixture_guard_diagnostics(incident):
    """Report only known static guard reasons, preserving every refusal."""
    verify = incident.verify

    def checked(*args, **kwargs):
        try:
            return verify(*args, **kwargs)
        except incident.Blocked as error:
            reason = str(error)
            safe = reason if reason in SAFE_GUARD_REASONS else 'unclassified guard refusal'
            print('Synthetic fixture incident guard: ' + safe, file=sys.stderr, flush=True)
            raise

    incident.verify = checked


def fail(message):
    raise RuntimeError(message)


def cmd(args, *, data=None, timeout=120):
    try:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        fail('Synthetic fixture command unavailable')
    if result.returncode:
        fail('Synthetic fixture command failed: ' + ' '.join(args[:2]))
    return result.stdout


def private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def encode_hold(value):
    """Match the native controller's exact pending-marker serialization."""
    return json.dumps(value, sort_keys=True).encode()


def inspect(name):
    return json.loads(cmd(['docker', 'inspect', name]))[0]


def image_id(reference):
    raw = cmd(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference]).decode().strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', raw):
        fail('Pinned fixture image identity is unavailable')
    return raw


def container_http(container, path, *, form=None):
    # Real local fixture Caddy/app/PDS response. Docker executes Node on the
    # candidate image for app/PDS; Caddy has BusyBox wget and no Node.
    if container == 'caddy-caddy-1':
        networks = json.loads(cmd(['docker', 'network', 'inspect', 'open-hax']))
        if len(networks) != 1 or networks[0].get('Internal') is not True:
            fail('Synthetic ingress network is not internal')
        peers = networks[0].get('Containers') or {}
        expected = inspect(container)['Id']
        owners = []
        for peer in peers:
            record = inspect(peer)
            aliases = (record.get('NetworkSettings', {}).get('Networks', {}).get('open-hax') or {}).get('Aliases') or []
            if 'axxium.promethean.rest' in aliases:
                owners.append(record['Id'])
        if owners != [expected]:
            fail('Synthetic ingress hostname is not uniquely bound to Caddy')
        args = ['docker', 'exec', container, 'wget', '--no-check-certificate',
                '-S', '-O', '-', '--header', 'Host: axxium.promethean.rest']
        if form is not None:
            args += ['--post-data', form, '--header', 'Content-Type: application/x-www-form-urlencoded']
        result = subprocess.run(args + ['https://axxium.promethean.rest' + path],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        codes = re.findall(rb'HTTP/\d(?:\.\d)?\s+(\d{3})', result.stderr)
        if not codes:
            fail('Synthetic ingress returned no HTTP status')
        return int(codes[-1]), result.stdout
    program = ("const d=JSON.parse(process.argv[1]);fetch('http://127.0.0.1:'+d.port+d.path,"
               "d.form===null?{}:{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:d.form})"
               ".then(async r=>process.stdout.write(JSON.stringify({s:r.status,b:await r.text()})))"
               ".catch(()=>process.exit(1))")
    port = 8787 if container == 'axxium-axxium-1' else 3000
    data = {'port': port, 'path': path, 'form': form}
    response = json.loads(cmd(['docker', 'exec', container, 'node', '-e', program, json.dumps(data)]))
    return response['s'], response['b'].encode()


def main():
    if (os.getuid() != 1000 or os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        fail('Require disposable hosted VM, UID 1000 and isolated network namespace')
    fixture_netns.require_isolated()
    source_root = Path(__file__).resolve().parents[3]
    # The CI artifact verifier binds this config digest to the exact candidate
    # manifest. Docker's classic store returns the config ID, while a
    # containerd-backed store may report the manifest ID.
    candidate_id = image_id('promethean-axxium:' + SOURCE)
    if candidate_id not in (IMAGE, IMAGE_CONFIG):
        fail('Loaded candidate differs from the verified artifact')
    postgres_id = image_id('postgres@' + POSTGRES)
    caddy_id = image_id('caddy@' + CADDY)
    topology = network_fixture.setup(ROOT, candidate_id, postgres_id, caddy_id)
    state = ROOT / 'state'
    private(state / '.axxium-staging-fixture', (topology['ids']['axxium-axxium-1'] + '\n').encode())
    controller = ROOT / 'services/axxium/controllers/fixture'
    (controller / 'scripts').mkdir(parents=True, mode=0o700)
    (controller / 'digitalocean/services/axxium').mkdir(parents=True, mode=0o700)
    files = ('axxium_minimal_release.py', 'axxium_incident_guard.py',
             'axxium_interrupted_handoff.py', 'axxium_oidc_ingress.py',
             'provision_axxium_colab_identity.py')
    for file in files:
        shutil.copyfile(source_root / 'scripts' / file, controller / 'scripts' / file)
        (controller / 'scripts' / file).chmod(0o600)
    identity_fixture.IMAGE = candidate_id
    identity_fixture.create(topology['ids']['axxium-axxium-1'], state, controller)
    sys.path.insert(0, str(controller / 'scripts'))
    release = importlib.import_module('axxium_minimal_release')
    incident = importlib.import_module('axxium_incident_guard')
    enable_fixture_guard_diagnostics(incident)
    release.IMAGE = candidate_id
    host = release.NativeHost()
    plan = json.loads((source_root / 'digitalocean/services/axxium/minimal-production-plan.json').read_bytes())
    plan['image_id'] = candidate_id
    expected = plan['expected']
    for name, prefix in ((release.APP, 'app'), (release.DB, 'db'),
                         (release.PDS, 'pds'), (release.PROXY, 'caddy')):
        data = inspect(name)
        expected[prefix + '_container_id'] = data['Id']
        expected[prefix + '_image_id'] = data['Image']
        if prefix != 'caddy':
            expected[prefix + '_environment_sha256'] = host.environment_hash(data)
    expected['caddy_proxy_ip'] = inspect(release.PROXY)['NetworkSettings']['Networks']['open-hax']['IPAddress']
    expected['compose_sha256'] = release.file_digest(release.RUNTIME / 'compose.yaml')
    expected['environment_file_sha256'] = release.file_digest(release.RUNTIME / '.env')
    expected['caddyfile_sha256'] = release.file_digest(release.CADDY)
    expected['managed_caddy_sha256'] = host.managed_digest()
    changes = cmd(['docker', 'diff', release.APP]).decode().splitlines()
    expected['app_writable_layer_sha256'] = release.digest(json.dumps(sorted(changes), separators=(',', ':')).encode())
    plan_raw = encode(plan)
    plan_path = controller / 'digitalocean/services/axxium/minimal-production-plan.json'
    private(plan_path, plan_raw)
    plan_hash = hashlib.sha256(plan_raw).hexdigest()
    host.plan_digest = plan_hash
    incident.PLAN_HASH = plan_hash
    operations = state / 'axxium/operations'
    operations.mkdir(mode=0o700)
    operation = operations / SOURCE
    operation.mkdir(mode=0o700)
    private(operation / 'plan.json', plan_raw)
    private(operation / 'incumbent.Caddyfile', release.CADDY.read_bytes())
    for i, phase in enumerate(incident.EXPECTED_PHASES):
        name, status = phase.rsplit('-', 1)
        private(operation / f'{i:03d}-{phase}.json', encode({'sequence': i, 'phase': name, 'status': status}))
    dump = cmd(['docker', 'exec', release.DB, 'pg_dump', '-U', 'axxium', '-d', 'axxium', '-Fc'])
    private(operation / 'before.pgdump', dump)
    cmd(['docker', 'exec', release.DB, 'createdb', '-U', 'axxium', '--template', 'template0', CLONE])
    cmd(['docker', 'exec', '-i', release.DB, 'pg_restore', '-U', 'axxium', '-d', CLONE, '--exit-on-error'], data=dump)
    private(operation / 'before-backup.json', encode({'path': str(operation / 'before.pgdump'),
             'sha256': hashlib.sha256(dump).hexdigest(), 'database': CLONE}))
    original_env = dict(x.split('=', 1) for x in inspect(release.APP)['Config']['Env'] if '=' in x)
    values = release.rehearsal_environment(original_env, CLONE, expected['caddy_proxy_ip'])
    env_file = operation / 'fixture-orphan.env'
    private(env_file, ('\n'.join(k + '=' + v for k, v in values.items()) + '\n').encode())
    orphan_id = cmd(['docker', 'run', '-d', '--pull', 'never', '--name', ORPHAN,
        '--network', 'axxium_database', '--user', '1000:1000',
        '--label', 'open-hax.axxium-operation=' + SOURCE,
        '--env-file', str(env_file), '--mount', 'type=bind,src=' + str(state / 'axxium/oidc') + ',dst=/run/axxium/oidc,readonly',
        '--mount', 'type=bind,src=' + str(state / 'axxium/google-client.json') + ',dst=/run/axxium/google-client.json,readonly', candidate_id]).decode().strip()
    if not re.fullmatch('[0-9a-f]{64}', orphan_id):
        fail('Synthetic orphan ID unavailable')
    incident.REHEARSAL_ID, incident.REHEARSAL_NAME, incident.CLONE = orphan_id, ORPHAN, CLONE
    incident.PROTECTED_IDS = tuple(topology['ids'][name] for name in
        (release.APP, release.DB, release.PDS, release.PROXY)) + (orphan_id,)
    host.wait_app(orphan_id)
    for _ in range(60):
        clone_state = cmd(['docker', 'exec', release.DB, 'psql', '-U', 'axxium', '-d', CLONE,
            '-Atq', '-v', 'ON_ERROR_STOP=1', '-c',
            "SELECT state FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid<>pg_backend_pid() AND backend_type='client backend' ORDER BY state"]).decode().splitlines()
        if incident.safe_clone_states(clone_state):
            break
        time.sleep(2)
    else:
        fail('Synthetic rehearsal did not become idle')
    hold = {'schema': 'axxium-production-pending/v1', 'operation': str(operation),
            'source_sha': SOURCE, 'plan_sha256': plan_hash}
    private(release.PENDING, encode_hold(hold))
    host.http = lambda path: container_http(release.PROXY, path)
    original_command = host.command
    def command(args, **kwargs):
        # The real verifier calls external production URLs and loopback ports.
        # Adapt only that final external-network check to fixture Caddy; all
        # other controller commands, including provisioning, are real.
        if len(args) == 2 and args[0] == 'bash' and args[1].endswith('/digitalocean/services/axxium/verify.sh'):
            status, body = container_http(release.PROXY, '/oidc/.well-known/openid-configuration')
            if status != 200:
                fail('Synthetic final discovery failed')
            release.NativeHost.discovery(body)
            return b'synthetic local discovery verified; external verifier substituted\n'
        return original_command(args, **kwargs)
    host.command = command
    result = release.execute(plan, host, recover_interrupted=True)
    if result.get('status') != 'healthy':
        fail('Synthetic controller did not reach healthy outcome')
    archive = state / 'axxium/operations' / (SOURCE + '.interrupted.41088376408')
    if not (archive / 'before.pgdump').is_file() or release.PENDING.exists():
        fail('Synthetic archive or terminal hold differs')
    print('PASS synthetic real-controller recovery; candidate is synthetic incumbent; external verifier substituted')


if __name__ == '__main__':
    main()
