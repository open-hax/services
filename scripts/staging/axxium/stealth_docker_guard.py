#!/usr/bin/env python3
"""Accidental-target guard for trusted, run-scoped staging Docker commands.

This is a PATH wrapper, not a security sandbox: its user can still access the
real Docker socket. It does not authorize image loading or resource cleanup.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

DOCKER = '/usr/bin/docker'
HEX = re.compile(r'[0-9a-f]{32}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')
CONTAINER_ID = re.compile(r'[0-9a-f]{64}\Z')


class Refused(ValueError):
    pass


def require(condition, message='Docker fixture command refused'):
    if not condition:
        raise Refused(message)


class Guard:
    def __init__(self, run_id, root, images, query=None):
        require(HEX.fullmatch(run_id) is not None, 'Invalid fixture run ID')
        root = Path(root)
        require(root.is_absolute() and root.resolve() == root and not root.is_symlink(), 'Invalid fixture root')
        info = root.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o700, 'Fixture root is not private')
        require(len(images) == 3 and all(IMAGE.fullmatch(x) for x in images), 'Invalid pinned image IDs')
        self.run = run_id
        self.prefix = 'axxium-fixture-' + run_id
        self.root = root
        self.images = set(images)
        self.caddy_image = images[2]
        self.networks = {self.prefix + '-ingress', self.prefix + '-database'}
        self.ownership = 'codex.axxium-recovery-fixture=' + run_id
        self.query = query or self._query

    @staticmethod
    def _query(args):
        if args[:1] == ['ps']:
            result = subprocess.run([DOCKER, *args], check=True, capture_output=True,
                                    text=True, timeout=20)
            return [json.loads(line) for line in result.stdout.splitlines()]
        if args[:1] == ['inspect'] and len(args) == 2:
            # Never fetch a full container inspect: an accidental foreign ID
            # could otherwise expose that container's environment variables.
            fields = ('.Id', '.Name', '.Image', '.Config.Labels', '.HostConfig.Privileged',
                      '.HostConfig.PortBindings', '.HostConfig.PidMode', '.HostConfig.CapAdd',
                      '.HostConfig.NetworkMode', '.HostConfig.PublishAllPorts', '.Mounts',
                      '.NetworkSettings.Networks')
            template = '\n'.join('{{json ' + field + '}}' for field in fields)
            result = subprocess.run([DOCKER, 'inspect', '--format', template, args[1]],
                                    check=True, capture_output=True, text=True, timeout=20)
            lines = result.stdout.splitlines()
            require(len(lines) == len(fields), 'Container metadata unavailable')
            ident, name, image, labels, privileged, ports, pid, caps, mode, publish, mounts, networks = (
                json.loads(line) for line in lines)
            return [{'Id': ident, 'Name': name, 'Image': image, 'Config': {'Labels': labels},
                     'HostConfig': {'Privileged': privileged, 'PortBindings': ports, 'PidMode': pid,
                                    'CapAdd': caps, 'NetworkMode': mode, 'PublishAllPorts': publish},
                     'Mounts': mounts, 'NetworkSettings': {'Networks': networks}}]
        result = subprocess.run([DOCKER, *args], check=True, capture_output=True,
                                text=True, timeout=20)
        return json.loads(result.stdout)

    def private_path(self, value):
        path = Path(value)
        require(path.is_absolute() and path.resolve() == path and self.root in path.parents,
                'Bind or config path is outside the fixture root')
        return path

    def network(self, name):
        require(name in self.networks, 'Network is outside the fixture')
        network = self.query(['network', 'inspect', name])
        require(isinstance(network, list) and len(network) == 1, 'Network inspection failed')
        info = network[0]
        require(info.get('Name') == name and info.get('Internal') is True and
                (info.get('Labels') or {}).get('codex.axxium-recovery-fixture') == self.run,
                'Fixture network identity differs')

    def owned(self, target):
        require(target.startswith(self.prefix + '-') or CONTAINER_ID.fullmatch(target),
                'Container target is outside the fixture')
        rows = self.query(['inspect', target])
        require(isinstance(rows, list) and len(rows) == 1, 'Container inspection failed')
        item = rows[0]
        name, ident = item.get('Name', ''), item.get('Id', '')
        require(name.startswith('/' + self.prefix + '-') and CONTAINER_ID.fullmatch(ident), 'Container identity differs')
        require(target in (ident, name[1:]), 'Ambiguous container target')
        require((item.get('Config', {}).get('Labels') or {}).get('codex.axxium-recovery-fixture') == self.run,
                'Container ownership differs')
        host = item.get('HostConfig') or {}
        require(item.get('Image') in self.images and not host.get('Privileged') and not host.get('PublishAllPorts') and
                host.get('PortBindings') in (None, {}) and host.get('PidMode') in ('', None, 'private') and
                host.get('CapAdd') in (None, []) and host.get('NetworkMode') != 'host',
                'Unsafe container host configuration')
        for mount in item.get('Mounts') or []:
            require(mount.get('Type') == 'tmpfs' or mount.get('Type') == 'bind', 'Unowned volume mount')
            if mount.get('Type') == 'bind':
                self.private_path(mount.get('Source', ''))
                require(mount.get('Destination') in ('/etc/caddy/Caddyfile', '/run/axxium/google-client.json',
                                                      '/run/axxium/oidc'), 'Unsafe bind destination')
        nets = item.get('NetworkSettings', {}).get('Networks') or {}
        require(set(nets) <= self.networks or not nets, 'Container network differs')
        for name in nets:
            self.network(name)
        return ident

    def mount(self, value):
        pairs = value.split(',')
        require(len(pairs) in (3, 4) and pairs[0] == 'type=bind' and pairs[1].startswith('src=') and
                pairs[2].startswith('dst=') and (len(pairs) == 3 or pairs[3] == 'readonly'),
                'Unsafe fixture mount')
        self.private_path(pairs[1][4:])
        require(pairs[2][4:] in ('/etc/caddy/Caddyfile', '/run/axxium/google-client.json', '/run/axxium/oidc'),
                'Unexpected bind destination')

    def run_command(self, args):
        flags = {'-d', '-i', '--read-only'}
        valued = {'--name', '--network', '--network-alias', '--label', '--pull', '--user',
                  '--cap-drop', '--security-opt', '--memory', '--pids-limit', '--tmpfs',
                  '--env-file', '--mount', '--entrypoint', '--health-cmd', '--health-interval',
                  '--health-timeout', '--health-retries'}
        options = {}
        position = 0
        while position < len(args) and args[position].startswith('-'):
            option = args[position]
            require(option in flags or option in valued, 'Unknown Docker run option')
            if option in flags:
                require(option not in options, 'Duplicate Docker run flag')
                options[option] = True
                position += 1
                continue
            require(position + 1 < len(args), 'Missing Docker run option value')
            value = args[position + 1]
            require(option in ('--label', '--tmpfs', '--mount') or option not in options,
                    'Duplicate Docker run option')
            options.setdefault(option, []).append(value)
            position += 2
        require(position < len(args) and args[position] in self.images, 'Unpinned fixture image')
        names = options.get('--name', [])
        require(len(names) == 1 and names[0].startswith(self.prefix + '-') and
                re.fullmatch(re.escape(self.prefix) + r'-[a-z0-9-]+', names[0]), 'Unsafe container name')
        labels = options.get('--label', [])
        require(self.ownership in labels and all(
            label == self.ownership or label == 'open-hax.fixture-' + self.run + '=58234df96ca86507e6cdd87f65aaa9ab35621415'
            for label in labels), 'Unsafe container label')
        require(options.get('--pull') == ['never'], 'Fixture must not pull images')
        network = options.get('--network', [])
        require(len(network) == 1 and (network[0] == 'none' or network[0] in self.networks), 'Unsafe container network')
        if network[0] != 'none':
            self.network(network[0])
        require(all(x in ('axxium-pds', 'axxium.promethean.rest') for x in options.get('--network-alias', [])),
                'Unexpected network alias')
        require(options.get('--security-opt', ['no-new-privileges']) == ['no-new-privileges'], 'Unsafe security option')
        require(options.get('--cap-drop', ['ALL']) == ['ALL'], 'Unsafe capability selection')
        require(options.get('--user', ['1000:1000']) == ['1000:1000'], 'Unexpected fixture user')
        for value in options.get('--mount', []):
            self.mount(value)
        for value in options.get('--env-file', []):
            self.private_path(value)
        for value in options.get('--tmpfs', []):
            require(value.split(':', 1)[0] in ('/tmp', '/data', '/config'), 'Unexpected tmpfs destination')
        if args[position] == self.caddy_image:
            require(names[0] == self.prefix + '-caddy' and
                    {'/data', '/config'} <= {value.split(':', 1)[0] for value in options.get('--tmpfs', [])},
                    'Fixture Caddy requires both temporary data mounts')

    def compose_config(self, path, *, overlay):
        file = self.private_path(path)
        require(file.is_file() and not file.is_symlink(), 'Unsafe compose file')
        data = json.loads(file.read_bytes())
        require(isinstance(data, dict) and set(data) <= {'services', 'networks'}, 'Unknown compose section')
        services = data.get('services')
        require(isinstance(services, dict) and set(services) <= {'axxium', 'axxium-db'} and services,
                'Unexpected compose service')
        if overlay:
            require(set(services) == {'axxium'} and 'networks' not in data, 'Unexpected compose overlay')
        for name, service in services.items():
            allowed = {'image', 'environment', 'healthcheck', 'networks', 'labels', 'user',
                       'tmpfs', 'volumes', 'stop_grace_period'}
            require(isinstance(service, dict) and set(service) <= allowed, 'Unsafe compose service keys')
            require(service.get('image') in self.images, 'Unpinned compose image')
            if not overlay:
                require(service.get('labels') == {'codex.axxium-recovery-fixture': self.run},
                        'Compose ownership label differs')
            for mount in service.get('volumes', []):
                if isinstance(mount, str):
                    parts = mount.split(':')
                    require(len(parts) == 3 and parts[1] == '/run/axxium/google-client.json' and parts[2] == 'ro',
                            'Unsafe compose bind')
                    self.private_path(parts[0])
                else:
                    require(isinstance(mount, dict) and set(mount) <= {'type', 'source', 'target', 'read_only', 'bind'} and
                            mount.get('type') == 'bind' and mount.get('target') == '/run/axxium/oidc' and
                            mount.get('read_only') is True and mount.get('bind') == {'create_host_path': False},
                            'Unsafe compose overlay bind')
                    self.private_path(mount['source'])
            for tmpfs in service.get('tmpfs', []):
                require(isinstance(tmpfs, str) and tmpfs.split(':', 1)[0] in
                        ('/var/lib/postgresql/data', '/var/run/postgresql'), 'Unsafe compose tmpfs')
            if 'networks' in service:
                require(not overlay and set(service['networks']) <= {'default', 'database'}, 'Unsafe compose networks')
                if isinstance(service['networks'], dict):
                    require(all(value in ({}, {'aliases': ['axxium']}) for value in service['networks'].values()),
                            'Unsafe compose network configuration')
        if not overlay:
            networks = data.get('networks')
            require(isinstance(networks, dict) and set(networks) == {'default', 'database'}, 'Compose networks differ')
            for key, suffix in (('default', '-ingress'), ('database', '-database')):
                require(networks[key] == {'external': True, 'name': self.prefix + suffix}, 'Compose network differs')
                self.network(self.prefix + suffix)

    def compose(self, args):
        fixed = ['--project-name', self.prefix, '--project-directory', str(self.root / 'services/axxium'),
                 '--env-file', str(self.root / 'services/axxium/.env'), '-f', str(self.root / 'services/axxium/compose.yaml')]
        require(args[:len(fixed)] == fixed, 'Compose project or paths differ')
        env_file = self.private_path(fixed[5])
        info = env_file.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600, 'Compose environment file is not private')
        self.compose_config(fixed[-1], overlay=False)
        rest = args[len(fixed):]
        if rest[:1] == ['-f']:
            require(len(rest) >= 3, 'Missing compose overlay')
            self.compose_config(rest[1], overlay=True)
            rest = rest[2:]
        require(rest in (['up', '-d', '--no-deps', '--pull', 'never', 'axxium'],
                         ['up', '-d', '--no-deps', '--pull', 'never', 'axxium-db'],
                         ['stop', '--timeout', '610', 'axxium']), 'Unknown compose operation')
        # Compose may adopt an existing project container. Check every selected
        # project member before forwarding a mutator; an empty project is OK.
        self.project_members()

    def project_members(self):
        members = self.query(['ps', '-a', '--no-trunc', '--filter',
                              'label=com.docker.compose.project=' + self.prefix,
                              '--format', '{{json .ID}}'])
        require(isinstance(members, list) and len(members) == len(set(members)) and
                all(isinstance(member, str) and CONTAINER_ID.fullmatch(member) for member in members),
                'Compose project inventory differs')
        for member in members:
            self.owned(member)
        return members

    def validate(self, args):
        require(args, 'Missing Docker operation')
        if args[0] == 'run':
            return self.run_command(args[1:])
        if args[0] == 'compose':
            return self.compose(args[1:])
        if args[:2] == ['network', 'create']:
            require(len(args) == 6 and args[2:5] == ['--internal', '--label', self.ownership] and
                    args[5] in self.networks, 'Unsafe network creation')
            return
        if args[:2] == ['network', 'inspect']:
            rest = args[2:]
            if len(rest) == 3 and rest[0] == '--format':
                rest = rest[2:]
            require(len(rest) == 1, 'Unsafe network inspection')
            return self.network(rest[0])
        if args[:2] == ['network', 'ls']:
            require(args[2:] == ['--format', '{{.Name}}'], 'Unknown network listing')
            return
        if args[:2] == ['image', 'inspect']:
            rest = args[2:]
            if len(rest) == 3 and rest[0] == '--format':
                rest = rest[2:]
            refs = self.images | {self.prefix + ':fixture-' + self.run,
                'postgres@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685',
                'caddy@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17'}
            require(len(rest) == 1 and rest[0] in refs, 'Image reference outside fixture')
            return
        if args[0] == 'ps':
            require(args[1:] in (['-a', '--format', '{{.Names}}'],
                ['-aq', '--no-trunc', '--filter', 'label=open-hax.fixture-' + self.run + '=58234df96ca86507e6cdd87f65aaa9ab35621415']),
                'Unsafe container listing')
            return
        if args[0] in ('inspect', 'diff', 'stop', 'rm'):
            rest = args[1:]
            if args[0] == 'inspect' and len(rest) == 3 and rest[0] == '--format':
                rest = rest[2:]
            if args[0] == 'stop' and len(rest) == 3 and rest[:2] == ['--time', '610']:
                rest = rest[2:]
            require(len(rest) == 1, 'Unsafe container operation')
            ident = self.owned(rest[0])
            rewritten = list(args)
            rewritten[-1] = ident
            return rewritten
        if args[0] == 'exec':
            rest = args[1:]
            if rest[:1] == ['-i']:
                rest = rest[1:]
            require(len(rest) >= 2 and not rest[0].startswith('-'), 'Unsafe exec options')
            ident = self.owned(rest[0])
            require(rest[1] in ('node', 'psql', 'pg_dump', 'pg_restore', 'createdb', 'dropdb', 'stat', 'cat', 'caddy', 'wget'),
                    'Unknown fixture executable')
            rewritten = list(args)
            rewritten[2 if args[1] == '-i' else 1] = ident
            return rewritten
        raise Refused('Unknown Docker operation')


def main():
    try:
        env = os.environ
        require(env.get('DOCKER_HOST') in (None, '', 'unix:///var/run/docker.sock') and
                not env.get('DOCKER_CONTEXT'), 'Remote Docker selection refused')
        env['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
        guard = Guard(env['AXXIUM_FIXTURE_RUN_ID'], env['AXXIUM_FIXTURE_ROOT'],
                      [env['AXXIUM_FIXTURE_CANDIDATE_ID'], env['AXXIUM_FIXTURE_POSTGRES_ID'],
                       env['AXXIUM_FIXTURE_CADDY_ID']])
        args = sys.argv[1:]
        args = guard.validate(args) or args
        os.execv(DOCKER, [DOCKER, *args])
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        print('Docker staging operation refused; no command forwarded', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
