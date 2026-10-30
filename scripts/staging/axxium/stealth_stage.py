#!/usr/bin/env python3
"""Run one reviewed synthetic recovery fixture on Stealth's shared daemon.

The fixture code and Docker guard are trusted reviewed inputs; the PATH guard
prevents accidental target mistakes, not malicious access to the Docker socket.
Any failed or unavailable mutation leaves run resources for inspection.
"""
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

DOCKER = '/usr/bin/docker'
POSTGRES = 'postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
CADDY = 'caddy@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17'
CANDIDATE_IDS = {'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652',
                 'sha256:c45e58aadb02622183ccf60a971da2669fc033231b677c12e963920234b9b542'}
SOURCE = Path('/home/err/Documents/Codex/2026-10-29/task/services-handoff')
PARENT = Path('/home/err/Documents/Codex/2026-10-29/task/fixture-runs')
ZIP = Path('/tmp/axxium-artifact-verify.dj8TOa9D/candidate.zip')
EXPECTED_TOOLS = {
    'stealth_rebind.py': '9c2687fc038ffbfcb3d84e294047847f392e0416f5ccf588474b6a7452b76e33',
    'derive_fixture_image.py': 'f695739e1f460eb60a533bec98f3874149cb266fd0fe7b30c31917b039c789e8',
    'stealth_docker_guard.py': '06529c35a7316930dd4222b68c85e0bc5c7907d07c94369bca579449b566dc3a',
}
EXPECTED_QUALIFICATION = {
    'axxium_production_qualification.py': 'a2e901a3a7dbfd40bf36986cab937567b87520d1b01d528f7cebb936b81cf060',
    'environment_policy.py': 'b32748ae2d103cacb9ccdecf17119659aac9c0797461e32d5ce96b2e1ce58b33',
    'github_environment_admission.py': '5862842c575f45222facc5cd91a039d008961e4537fffe8c563a4d41ef359637',
}


class Refused(RuntimeError):
    pass


def docker(args, *, timeout=30):
    try:
        result = subprocess.run([DOCKER, *args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Docker operation outcome unavailable; retain run state') from None
    if result.returncode:
        raise Refused('Docker operation failed or its outcome is uncertain; retain run state')
    return result.stdout.decode('utf-8').strip()


def write_record(path, value):
    data = (json.dumps(value, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def image_info(reference):
    records = json.loads(docker(['image', 'inspect', reference]))
    if not isinstance(records, list) or len(records) != 1:
        raise Refused('Image metadata unavailable')
    return records[0]


def select_subnets(routes, networks):
    """Select /28s avoiding every reported IPv4 route and Docker IPAM pool."""
    if not isinstance(routes, list) or not isinstance(networks, list):
        raise Refused('Network inventory is malformed')
    occupied = []
    try:
        for route in routes:
            if not isinstance(route, dict):
                raise ValueError()
            dst = route.get('dst', 'default')
            if dst == 'default':
                continue  # A default route covers all unallocated destinations.
            net = ipaddress.ip_network(dst, strict=False)
            if net.version != 4:
                raise ValueError()
            occupied.append(net)
        for network in networks:
            config = network['IPAM']['Config'] or []
            if not isinstance(config, list):
                raise ValueError()
            for item in config:
                subnet = item.get('Subnet')
                if subnet is None:
                    raise ValueError()
                net = ipaddress.ip_network(subnet, strict=True)
                if net.version == 4:
                    occupied.append(net)
    except (KeyError, TypeError, ValueError):
        raise Refused('Network inventory is malformed') from None
    selected = []
    for net in ipaddress.ip_network('172.16.0.0/12').subnets(new_prefix=28):
        if not any(net.overlaps(other) for other in occupied):
            selected.append(str(net))
            if len(selected) == 2:
                return selected
    raise Refused('No two non-overlapping fixture subnets are available')


def network_inventory():
    try:
        routes = json.loads(subprocess.run(['/usr/sbin/ip', '-j', '-4', 'route', 'show', 'table', 'all'],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout)
        ids = docker(['network', 'ls', '-q', '--no-trunc']).splitlines()
        if any(not re.fullmatch('[0-9a-f]{64}', value) for value in ids):
            raise ValueError()
        networks = json.loads(docker(['network', 'inspect', *ids])) if ids else []
        if len(networks) != len(ids) or {item['Id'] for item in networks} != set(ids):
            raise ValueError()
        return select_subnets(routes, networks)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError):
        raise Refused('Network inventory is unavailable or malformed') from None


def create_fixture_networks(prefix, label, root):
    match = re.fullmatch(r'axxium-fixture-([0-9a-f]{32})', prefix)
    if not match or label != 'codex.axxium-recovery-fixture=' + match.group(1):
        raise Refused('Fixture network identity differs')
    result = {}
    for index, suffix in enumerate(('-ingress', '-database')):
        subnet = network_inventory()[0]
        if any(ipaddress.ip_network(subnet).overlaps(ipaddress.ip_network(item['subnet']))
               for item in result.values()):
            raise Refused('Fixture network inventory omitted prior subnet')
        name = prefix + suffix
        write_record(root / f'04-network-{index}-pending.json', {'name': name, 'subnet': subnet, 'label': label})
        ident = docker(['network', 'create', '--internal', '--label', label, '--subnet', subnet, name])
        if not re.fullmatch('[0-9a-f]{64}', ident):
            raise Refused('Created network identity unavailable; retain run state')
        info = json.loads(docker(['network', 'inspect', name]))
        if (not isinstance(info, list) or len(info) != 1 or info[0].get('Id') != ident or
            info[0].get('Name') != name or info[0].get('Internal') is not True or
            info[0].get('Driver') != 'bridge' or info[0].get('Labels') != {label.split('=', 1)[0]: label.split('=', 1)[1]} or
            (info[0].get('Containers') or {}) or
            [item.get('Subnet') for item in info[0].get('IPAM', {}).get('Config', [])] != [subnet]):
            raise Refused('Created network metadata differs; retain run state')
        result[name] = {'id': ident, 'subnet': subnet}
        write_record(root / f'04-network-{index}-created.json', result[name])
    return result


def verify_tools():
    directory = Path(__file__).resolve().parent
    scripts = directory.parents[1]
    actual = {}
    for name, expected in list(EXPECTED_TOOLS.items()) + list(EXPECTED_QUALIFICATION.items()):
        path = (directory if name in EXPECTED_TOOLS else scripts) / name
        if path.is_symlink() or not path.is_file():
            raise Refused('Reviewed fixture tool is unavailable')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise Refused('Reviewed fixture tool hash differs')
        actual[name] = digest
    return actual


def load_tools():
    actual = verify_tools()
    rebind = importlib.import_module('stealth_rebind')
    derive = importlib.import_module('derive_fixture_image')
    directory = Path(__file__).resolve().parent
    if (Path(rebind.__file__).resolve() != directory / 'stealth_rebind.py' or
            Path(derive.__file__).resolve() != directory / 'derive_fixture_image.py' or
            Path(derive.qualification.__file__).resolve() != directory.parents[1] / 'axxium_production_qualification.py' or
            verify_tools() != actual):
        raise Refused('Fixture tool import differs from reviewed source')
    return actual, rebind, derive


def main():
    if os.getuid() != 1000 or os.environ.get('AXXIUM_STEALTH_FIXTURE_APPLY') != '1':
        raise Refused('Explicit fixture invocation as UID 1000 is required')
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise Refused('Remote Docker selection is refused')
    tool_hashes, stealth_rebind, derive_fixture_image = load_tools()
    if docker(['context', 'show']) != 'default' or docker(['context', 'inspect', 'default',
            '--format', '{{.Endpoints.docker.Host}}']) != 'unix:///var/run/docker.sock':
        raise Refused('The canonical local Docker socket is required')
    postgres = image_info(POSTGRES)
    caddy = image_info(CADDY)
    if postgres.get('Config', {}).get('Volumes') != {'/var/lib/postgresql/data': {}}:
        raise Refused('PostgreSQL volume declaration differs')
    if caddy.get('Config', {}).get('Volumes') not in (None, {}):
        raise Refused('Caddy declares unexpected volumes')
    for item in (postgres, caddy):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', item.get('Id', '')):
            raise Refused('Pinned image ID unavailable')

    PARENT.mkdir(mode=0o700, exist_ok=True)
    if PARENT.is_symlink() or PARENT.stat().st_uid != 1000 or PARENT.stat().st_mode & 0o077:
        raise Refused('Fixture run parent is not private')
    run_id = secrets.token_hex(16)
    root = Path(tempfile.mkdtemp(prefix=run_id + '-', dir=PARENT))
    os.chmod(root, 0o700)
    tag = 'axxium-fixture-' + run_id + ':fixture-' + run_id
    # Append-only fixed phase records provide a truthful handoff on failure.
    write_record(root / '00-intent.json', {'run_id': run_id, 'tag': tag, 'status': 'preparing',
                 'tool_sha256': tool_hashes, 'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    try:
        # prepare() expects an empty directory, so keep its copied inputs in a
        # dedicated fresh child rather than the directory holding receipts.
        prepared = root / 'prepared'
        prepared.mkdir(mode=0o700)
        rebinding = stealth_rebind.prepare(SOURCE, prepared, run_id)
        host_root = Path(rebinding['host_root'])
        host_root.mkdir(mode=0o700)
        derived = root / 'fixture-image.tar'
        image = derive_fixture_image.derive(SOURCE / stealth_rebind.PLAN, ZIP, derived, run_id)
        if image['tag'] != tag:
            raise Refused('Derived image tag differs')
        write_record(root / '01-prepared.json', {'rebinding': rebinding, 'image': image})

        # Inspect the whole tag inventory once; failure is not interpreted as
        # absence. Never reuse an existing run tag.
        tags = docker(['image', 'ls', '--format', '{{.Repository}}:{{.Tag}}']).splitlines()
        if tag in tags:
            raise Refused('Fixture image tag already exists')
        label = 'codex.axxium-recovery-fixture=' + run_id
        prefix = 'axxium-fixture-' + run_id
        if (docker(['ps', '-aq', '--no-trunc', '--filter', 'label=' + label]) or
                docker(['network', 'ls', '-q', '--no-trunc', '--filter', 'label=' + label])):
            raise Refused('Fixture ownership label already exists')
        if any(name.startswith(prefix + '-') for name in docker(['ps', '-a', '--format', '{{.Names}}']).splitlines()):
            raise Refused('Fixture container name already exists')
        if set(docker(['network', 'ls', '--format', '{{.Name}}']).splitlines()) & {prefix + '-ingress', prefix + '-database'}:
            raise Refused('Fixture network name already exists')
        write_record(root / '02-empty-inventory.json', {'label': label, 'status': 'empty'})
        write_record(root / '03-image-load-pending.json', {'tag': tag})
        docker(['load', '--input', str(derived)], timeout=180)
        candidate = image_info(tag)
        candidate_id = candidate.get('Id', '')
        if candidate_id not in CANDIDATE_IDS or candidate.get('Config', {}).get('Volumes') not in (None, {}):
            raise Refused('Loaded candidate image differs')
        write_record(root / '04-image-loaded.json', {'tag': tag, 'image_id': candidate_id})

        # Inventory immediately before creation. Docker's own overlap check
        # remains authoritative if another process races this selection.
        fixture_networks = create_fixture_networks(prefix, label, root)
        write_record(root / '04-networks-created.json', fixture_networks)

        # The executable wrapper validates expected CLI targets. It is not a
        # hard security boundary against arbitrary socket clients.
        binary = root / 'bin'
        binary.mkdir(mode=0o700)
        wrapper = binary / 'docker'
        guard_bytes = Path(__file__).with_name('stealth_docker_guard.py').read_bytes()
        if hashlib.sha256(guard_bytes).hexdigest() != tool_hashes['stealth_docker_guard.py']:
            raise Refused('Reviewed Docker guard changed')
        fd = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, 'wb') as out:
            out.write(guard_bytes)
            out.flush()
            os.fsync(out.fileno())
        source = prepared / 'source'
        probe = source / 'scripts/staging/axxium/full_recovery_probe.py'
        environment = {'PATH': str(binary) + ':' + os.defpath, 'HOME': str(root),
            'DOCKER_HOST': 'unix:///var/run/docker.sock', 'PYTHONDONTWRITEBYTECODE': '1',
            'AXXIUM_FIXTURE_MODE': 'local-isolated-v1', 'AXXIUM_FIXTURE_HOST': 'stealth',
            'FIXTURE_HOST_NETNS_FD': '3', 'AXXIUM_FIXTURE_RUN_ID': run_id,
            'AXXIUM_FIXTURE_ROOT': str(host_root), 'AXXIUM_FIXTURE_CANDIDATE_ID': candidate_id,
            'AXXIUM_FIXTURE_POSTGRES_ID': postgres['Id'], 'AXXIUM_FIXTURE_CADDY_ID': caddy['Id'],
            'AXXIUM_FIXTURE_NETWORKS': json.dumps(fixture_networks, sort_keys=True)}
        invocation = ('exec 3</proc/self/ns/net; exec unshare --user --map-current-user '
                      '--net --keep-caps --fork --kill-child=TERM "$@"')
        write_record(root / '05-fixture-pending.json', {'status': 'running'})
        log_path = root / 'fixture.log'
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as log:
                try:
                    result = subprocess.run(['bash', '-c', invocation, 'bash', sys.executable, str(probe)],
                        env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        timeout=4500, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    raise Refused('Fixture outcome unavailable; retain run state') from None
                log.flush()
                os.fsync(log.fileno())
        finally:
            pass
        if result.returncode != 0:
            raise Refused('Fixture failed; retain labeled resources and private log')
        marker = b'PASS synthetic real-controller recovery; candidate is synthetic incumbent; external verifier substituted'
        if marker not in log_path.read_bytes():
            raise Refused('Fixture success marker unavailable; retain resources')
        write_record(root / '06-fixture-passed.json', {'status': 'passed', 'exit_code': result.returncode})
        containers = docker(['ps', '-aq', '--no-trunc', '--filter', 'label=' + label]).splitlines()
        networks = docker(['network', 'ls', '-q', '--no-trunc', '--filter', 'label=' + label]).splitlines()
        if any(not re.fullmatch('[0-9a-f]{64}', value) for value in containers + networks):
            raise Refused('Fixture final inventory is malformed')
        write_record(root / '07-retained-inventory.json', {'containers': containers, 'networks': networks})
        return {'status': 'passed', 'run_id': run_id, 'receipt_root': str(root),
                'manifest_sha256': hashlib.sha256((root / '01-prepared.json').read_bytes()).hexdigest(),
                'containers_retained': containers, 'networks_retained': networks, 'image_retained': True,
                'limits': ['candidate-as-incumbent', 'public verifier substituted',
                           'trusted CLI wrapper is not a socket sandbox', 'not hosted CI',
                           'native rehearsal stops and removes its own verified fixture containers']}
    except BaseException:
        # Do not log private command output or guess which effect happened.
        print('Synthetic fixture stopped; retain run resources and inspect private receipt at ' + str(root), file=sys.stderr)
        raise


if __name__ == '__main__':
    print(json.dumps(main(), sort_keys=True))
