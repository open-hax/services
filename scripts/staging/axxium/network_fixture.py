#!/usr/bin/env python3
"""Synthetic Docker topology for an ephemeral recovery-controller fixture.

This module has no side effects on import.  It is deliberately restricted to a
GitHub-hosted disposable runner, in a separate network namespace, and refuses
to adopt existing names, networks or host state.  All service containers use
internal networks and no published ports.  It must never run on a shared host.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import fixture_netns

ROOT = Path('/srv/open-hax')
SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
APP = 'axxium-axxium-1'
DB = 'axxium-axxium-db-1'
PDS = 'axxium-pds-1'
CADDY = 'caddy-caddy-1'
NAMES = (APP, DB, PDS, CADDY)
NETWORKS = ('open-hax', 'axxium_database')
LABEL = 'codex.axxium-recovery-fixture=synthetic'

# Exact matcher is deliberately the one consumed by axxium_oidc_ingress.py.
# Caddy's internal CA is used only inside the isolated fixture network.
CADDYFILE = b'''{
\tauto_https disable_redirects
}
axxium.promethean.rest, *.axxium.promethean.rest {
\ttls internal
\t@axxium_api {
\t\thost axxium.promethean.rest
\t\tpath / /health /portal/* /api/auth/* /api/actors* /api/entities* /api/atproto/*
\t}
\thandle @axxium_api {
\t\treverse_proxy axxium:8787 {
\t\t\theader_up -x-open-hax-bridge-auth
\t\t}
\t}
\thandle {
\t\treverse_proxy axxium-pds:3000
\t}
}
'''

PDS_JS = r'''const http=require('node:http');http.createServer((q,r)=>{
  r.setHeader('content-type','application/json');
  if(q.url==='/xrpc/_health')return r.end(JSON.stringify({version:'synthetic'}));
  if(q.url==='/xrpc/com.atproto.server.describeServer')return r.end(JSON.stringify({did:'did:web:axxium.promethean.rest'}));
  r.statusCode=404;r.end('{}');
}).listen(3000,'0.0.0.0');'''


class FixtureError(RuntimeError):
    pass


def command(*args):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True, timeout=120)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise FixtureError('Synthetic fixture command failed: ' + ' '.join(args[:2])) from exc
    return result.stdout.decode().strip()


def private_write(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def preflight(root, image_ids):
    if (Path(root) != ROOT or os.getuid() != 1000 or
        os.environ.get('GITHUB_ACTIONS') != 'true' or
        os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise FixtureError('Fixture requires a GitHub-hosted runner as UID 1000')
    if (os.environ.get('DOCKER_HOST') != 'unix:///var/run/docker.sock' or
        os.environ.get('DOCKER_CONTEXT')):
        raise FixtureError('Fixture requires the local Docker Unix socket')
    try:
        socket_info = Path('/var/run/docker.sock').stat()
    except OSError as exc:
        raise FixtureError('Local Docker socket is unavailable') from exc
    if not stat.S_ISSOCK(socket_info.st_mode):
        raise FixtureError('Local Docker endpoint is not a Unix socket')
    # A network namespace without host interfaces prevents the driver and its
    # child processes from reaching the production issuer directly. The daemon
    # remains accessible only through its local Unix socket.
    if os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net'):
        raise FixtureError('Fixture driver must have a separate network namespace')
    try:
        fixture_netns.require_isolated()
    except (OSError, RuntimeError, ValueError):
        raise FixtureError('Fixture namespace has a route outside loopback') from None
    if root.is_symlink() or not root.is_dir():
        raise FixtureError('Fixture root must be a precreated private directory')
    info = root.stat()
    if (info.st_uid != 1000 or stat.S_IMODE(info.st_mode) != 0o700 or any(root.iterdir())):
        raise FixtureError('Fixture root must be empty and private')
    names = set(command('docker', 'ps', '-a', '--format', '{{.Names}}').splitlines())
    networks = set(command('docker', 'network', 'ls', '--format', '{{.Name}}').splitlines())
    if names.intersection(NAMES) or networks.intersection(NETWORKS):
        raise FixtureError('Existing runtime names cannot be adopted')
    for image in image_ids:
        if not image.startswith('sha256:') or command('docker', 'image', 'inspect', '--format', '{{.Id}}', image) != image:
            raise FixtureError('Fixture image is not locally pinned')


def setup(root, image_id, postgres_image, caddy_image):
    """Create a fixture and return synthetic paths and selected container IDs.

    On failure, retain the synthetic resources for CI inspection. The enclosing
    VM is ephemeral; never guess resource cleanup after an uncertain response.
    """
    root = Path(root)
    preflight(root, (image_id, postgres_image, caddy_image))
    runtime = root / 'services' / 'axxium'
    state = root / 'state' / 'axxium'
    caddy_dir = root / 'services' / 'caddy'
    managed = root / 'state' / 'caddy' / 'managed'
    for directory in (runtime, state, caddy_dir, managed, state / 'oidc', root / 'state' / 'colab-mcp'):
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
    private_write(state / 'google-client.json', b'{"synthetic":true}\n')
    private_write(runtime / '.env', b'# synthetic fixture only\n')
    private_write(caddy_dir / 'Caddyfile', CADDYFILE, mode=0o644)

    postgres = {'POSTGRES_USER': 'axxium', 'POSTGRES_DB': 'axxium',
                'POSTGRES_PASSWORD': 'synthetic-fixture-database-password'}
    app_env = {'NODE_ENV': 'production', 'AXXIUM_PORT': '8787', 'AXXIUM_HOST': '0.0.0.0',
               'AXXIUM_PUBLIC_BASE_URL': 'https://axxium.promethean.rest',
               'DB_HOST': 'axxium-db', 'DB_PORT': '5432', 'DB_NAME': 'axxium',
               'DB_USER': 'axxium', 'DB_PASSWORD': postgres['POSTGRES_PASSWORD'],
               'JWT_SECRET': 'synthetic-fixture-jwt-secret-value-only',
               'JWT_ISSUER': 'axxium', 'JWT_AUDIENCE': 'promethean',
               'SESSION_COOKIE_SECURE': 'true', 'SESSION_COOKIE_SAME_SITE': 'lax'}
    compose = {'services': {
        'axxium-db': {'image': postgres_image, 'environment': postgres,
            'tmpfs': ['/var/lib/postgresql/data:rw,uid=70,gid=70', '/var/run/postgresql:rw,uid=70,gid=70'],
            'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U axxium -d axxium'],
                'interval': '2s', 'timeout': '3s', 'retries': 40},
            'networks': ['database'], 'labels': {'codex.axxium-recovery-fixture': 'synthetic'}},
        'axxium': {'image': image_id, 'user': '1000:1000', 'environment': app_env,
            'healthcheck': {'test': ['CMD', 'node', '-e',
                "fetch('http://127.0.0.1:8787/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],
                'interval': '2s', 'timeout': '3s', 'retries': 60},
            'volumes': [str(state / 'google-client.json') + ':/run/axxium/google-client.json:ro'],
            'networks': {'default': {'aliases': ['axxium']}, 'database': {}},
            'labels': {'codex.axxium-recovery-fixture': 'synthetic'}}},
        'networks': {'default': {'external': True, 'name': 'open-hax'},
                     'database': {'external': True, 'name': 'axxium_database'}}}
    private_write(runtime / 'compose.yaml', (json.dumps(compose, sort_keys=True) + '\n').encode(), mode=0o644)

    for network in NETWORKS:
        command('docker', 'network', 'create', '--internal', '--label', LABEL, network)
        if command('docker', 'network', 'inspect', '--format', '{{.Internal}}', network) != 'true':
            raise FixtureError('Synthetic network is not internal')
    pds_health = "node -e \"fetch('http://127.0.0.1:3000/xrpc/_health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""
    command('docker', 'run', '-d', '--pull', 'never', '--name', PDS, '--label', LABEL,
            '--network', 'open-hax', '--network-alias', 'axxium-pds', '--health-cmd', pds_health,
            '--health-interval', '2s', '--health-timeout', '3s', '--health-retries', '40',
            '--entrypoint', 'node', image_id, '-e', PDS_JS)
    command('docker', 'run', '-d', '--pull', 'never', '--name', CADDY, '--label', LABEL,
            '--network', 'open-hax', '--network-alias', 'axxium.promethean.rest',
            '--mount', 'type=bind,src=' + str(caddy_dir / 'Caddyfile') + ',dst=/etc/caddy/Caddyfile',
            '--health-cmd', 'wget -q -O - http://127.0.0.1:2019/config/ >/dev/null',
            '--health-interval', '2s', '--health-timeout', '3s', '--health-retries', '40', caddy_image)
    compose_prefix = ('docker', 'compose', '--project-name', 'axxium', '--project-directory', str(runtime),
                      '--env-file', str(runtime / '.env'), '-f', str(runtime / 'compose.yaml'))
    command(*compose_prefix, 'up', '-d', '--no-deps', '--pull', 'never', 'axxium-db')
    wait_healthy(DB)
    command(*compose_prefix, 'up', '-d', '--no-deps', '--pull', 'never', 'axxium')
    for name in NAMES:
        wait_healthy(name)
    ids = {name: command('docker', 'inspect', '--format', '{{.Id}}', name) for name in NAMES}
    return {'root': str(root), 'runtime': str(runtime), 'state': str(state),
            'caddyfile': str(caddy_dir / 'Caddyfile'), 'managed': str(managed), 'ids': ids}


def wait_healthy(name):
    for _ in range(90):
        status = command('docker', 'inspect', '--format', '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', name)
        if status == 'running healthy':
            return
        if not status.startswith('running '):
            raise FixtureError('Synthetic service stopped: ' + name)
        time.sleep(2)
    raise FixtureError('Synthetic service did not become healthy: ' + name)
