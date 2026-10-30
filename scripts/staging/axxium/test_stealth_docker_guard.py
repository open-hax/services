"""Check accidental-target guard without contacting Docker."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('stealth_docker_guard_test', HERE / 'stealth_docker_guard.py')
guard_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard_module)
RUN = 'a' * 32
PREFIX = 'axxium-fixture-' + RUN
ID = 'b' * 64
IMAGES = ['sha256:' + c * 64 for c in 'cde']


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.container = {'Id': ID, 'Name': '/' + PREFIX + '-axxium-1', 'Image': IMAGES[0],
            'Config': {'Labels': {'codex.axxium-recovery-fixture': RUN}},
            'HostConfig': {'Privileged': False, 'PortBindings': None, 'PidMode': '',
                           'CapAdd': None, 'NetworkMode': 'private'},
            'Mounts': [], 'NetworkSettings': {'Networks': {}}}
        self.query_calls = []
        self.project_members = []
        def query(args):
            self.query_calls.append(args)
            if args[0] == 'ps':
                return self.project_members
            if args[0] == 'inspect':
                return [self.container]
            if args[:2] == ['network', 'inspect']:
                return [{'Name': args[-1], 'Internal': True,
                         'Labels': {'codex.axxium-recovery-fixture': RUN}}]
            raise AssertionError('Unexpected metadata query')
        self.guard = guard_module.Guard(RUN, self.root, IMAGES, query=query)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_owned_targets_can_be_executed_or_removed(self):
        self.guard.validate(['exec', '-i', ID, 'psql', '-d', 'fixture'])
        self.guard.validate(['rm', ID])
        name = PREFIX + '-axxium-1'
        self.assertEqual(self.guard.validate(['exec', '-i', name, 'node']), ['exec', '-i', ID, 'node'])
        self.assertEqual(self.guard.validate(['stop', '--time', '610', name]), ['stop', '--time', '610', ID])
        for args in (['exec', 'production-app', 'node'], ['rm', 'abc'],
                     ['exec', '--privileged', ID, 'node'], ['rm', '--force', ID]):
            with self.subTest(args=args), self.assertRaises(guard_module.Refused):
                self.guard.validate(args)
        self.container['Config']['Labels'] = {}
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(['exec', ID, 'node'])

    def test_container_privilege_network_and_bind_refusals(self):
        cases = [('Privileged', True), ('NetworkMode', 'host'), ('PidMode', 'host'),
                 ('CapAdd', ['SYS_ADMIN']), ('PortBindings', {'80/tcp': []})]
        for key, value in cases:
            previous = self.container['HostConfig'].get(key)
            self.container['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(guard_module.Refused):
                self.guard.validate(['exec', ID, 'node'])
            self.container['HostConfig'][key] = previous
        self.container['Mounts'] = [{'Type': 'bind', 'Source': '/etc/passwd', 'Destination': '/run/axxium/oidc'}]
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(['exec', ID, 'node'])

    def test_run_requires_labels_internal_network_and_no_excess_flags(self):
        args = ['run', '-d', '--pull', 'never', '--name', PREFIX + '-pds',
                '--label', self.guard.ownership, '--network', PREFIX + '-ingress', IMAGES[0]]
        self.guard.validate(args)
        for extra in (['--privileged'], ['-p', '123:80'], ['--mount', 'type=bind,src=/etc,dst=/run/axxium/oidc']):
            with self.subTest(extra=extra), self.assertRaises(guard_module.Refused):
                self.guard.validate(args[:-1] + extra + args[-1:])
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(['run', '--pull', 'never', '--name', PREFIX + '-x', '--network', 'host', IMAGES[0]])
        caddy = ['run', '-d', '--pull', 'never', '--name', PREFIX + '-caddy',
                 '--label', self.guard.ownership, '--network', PREFIX + '-ingress', IMAGES[2]]
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(caddy)
        self.guard.validate(caddy[:-1] + ['--tmpfs', '/data:rw', '--tmpfs', '/config:rw'] + caddy[-1:])

    def test_compose_closed_schema_blocks_ports_unknown_paths_and_host_network(self):
        runtime = self.root / 'services/axxium'; runtime.mkdir(parents=True)
        (runtime / '.env').write_text('')
        (runtime / '.env').chmod(0o600)
        config = {'services': {'axxium': {'image': IMAGES[0], 'labels': {
                    'codex.axxium-recovery-fixture': RUN}, 'networks': {'default': {'aliases': ['axxium']}}}},
                  'networks': {'default': {'external': True, 'name': PREFIX + '-ingress'},
                               'database': {'external': True, 'name': PREFIX + '-database'}}}
        path = runtime / 'compose.yaml'; path.write_text(json.dumps(config))
        args = ['compose', '--project-name', PREFIX, '--project-directory', str(runtime),
                '--env-file', str(runtime / '.env'), '-f', str(path), 'up', '-d', '--no-deps', '--pull', 'never', 'axxium']
        self.guard.validate(args)
        (runtime / '.env').unlink()
        (runtime / '.env').symlink_to('/etc/passwd')
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(args)
        (runtime / '.env').unlink()
        (runtime / '.env').write_text('')
        (runtime / '.env').chmod(0o600)
        self.project_members = [ID]
        self.guard.validate(args)
        self.container['Config']['Labels'] = {}
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(args)
        self.container['Config']['Labels'] = {'codex.axxium-recovery-fixture': RUN}
        for key, value in (('ports', ['80:80']), ('privileged', True), ('network_mode', 'host')):
            config['services']['axxium'][key] = value
            path.write_text(json.dumps(config))
            with self.subTest(key=key), self.assertRaises(guard_module.Refused):
                self.guard.validate(args)
            del config['services']['axxium'][key]
        with self.assertRaises(guard_module.Refused):
            self.guard.validate(args[:-1] + ['other-service'])

    def test_unknown_daemon_operations_fail_closed(self):
        for args in (['load'], ['system', 'prune'], ['network', 'rm', PREFIX + '-database'],
                     ['volume', 'rm', 'anything'], ['run', '--help'], ['compose', '--help']):
            with self.subTest(args=args), self.assertRaises(guard_module.Refused):
                self.guard.validate(args)

    def test_container_metadata_projection_excludes_environment(self):
        values = [ID, '/' + PREFIX + '-axxium-1', IMAGES[0],
                  {'codex.axxium-recovery-fixture': RUN}, False, None, '', None, 'private',
                  False, [], {}]
        result = subprocess.CompletedProcess([], 0, stdout='\n'.join(json.dumps(x) for x in values) + '\n')
        with patch.object(guard_module.subprocess, 'run', return_value=result) as command:
            metadata = guard_module.Guard._query(['inspect', ID])
        self.assertEqual(metadata[0]['Id'], ID)
        args = command.call_args.args[0]
        self.assertIn('--format', args)
        self.assertNotIn('.Config.Env', args[args.index('--format') + 1])


if __name__ == '__main__':
    unittest.main()
