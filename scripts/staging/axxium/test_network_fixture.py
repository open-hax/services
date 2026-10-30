"""Offline safety checks for synthetic topology construction; no daemon."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('network_fixture_under_test', HERE / 'network_fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
ingress_spec = importlib.util.spec_from_file_location('ingress_under_test', HERE.parents[1] / 'axxium_oidc_ingress.py')
ingress = importlib.util.module_from_spec(ingress_spec)
ingress_spec.loader.exec_module(ingress)


class NetworkFixtureTests(unittest.TestCase):
    def test_host_namespace_descriptor_must_be_valid_and_distinct(self):
        host = SimpleNamespace(st_dev=1, st_ino=10)
        other = SimpleNamespace(st_dev=1, st_ino=11)
        with patch.dict(os.environ, {'FIXTURE_HOST_NETNS_FD': '3'}, clear=True), \
                patch.object(fixture.fcntl, 'ioctl', return_value=0x40000000), \
                patch.object(fixture.os, 'fstat', return_value=host), \
                patch.object(fixture.os, 'stat', return_value=other), \
                patch.object(fixture.os, 'close') as close:
            fixture.require_separate_namespace()
            close.assert_called_once_with(3)
        for fd, kind, current in (('3', 0x40000000, host), ('3', 0, other), ('4', 0x40000000, other)):
            with self.subTest(fd=fd, kind=kind, current=current.st_ino), \
                    patch.dict(os.environ, {'FIXTURE_HOST_NETNS_FD': fd}, clear=True), \
                    patch.object(fixture.fcntl, 'ioctl', return_value=kind), \
                    patch.object(fixture.os, 'fstat', return_value=host), \
                    patch.object(fixture.os, 'stat', return_value=current), \
                    patch.object(fixture.os, 'close') as close:
                with self.assertRaises(fixture.FixtureError):
                    fixture.require_separate_namespace()
                close.assert_not_called()
        with patch.dict(os.environ, {'FIXTURE_HOST_NETNS_FD': '3'}, clear=True), \
                patch.object(fixture.fcntl, 'ioctl', side_effect=OSError('missing')):
            with self.assertRaises(fixture.FixtureError):
                fixture.require_separate_namespace()

    def test_caddy_fixture_matches_actual_ingress_transform(self):
        expected = hashlib.sha256(fixture.CADDYFILE).hexdigest()
        updated = ingress.prepare(fixture.CADDYFILE, expected)
        fenced = ingress.maintenance(fixture.CADDYFILE, expected)
        self.assertIn(b'/oidc/*', updated)
        self.assertIn(b'respond "Axxium sign-in is updating" 503', fenced)

    def test_preflight_rejects_untrusted_environment_before_docker(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}, clear=True), \
                patch.object(fixture.subprocess, 'run') as run:
            with self.assertRaises(fixture.FixtureError):
                fixture.preflight(Path('/srv/open-hax'), ())
            run.assert_not_called()

    def test_fixture_has_only_internal_networks_and_no_host_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            def fake(*args):
                calls.append(args)
                if args[:3] == ('docker', 'network', 'inspect'):
                    return 'true'
                if args[:2] == ('docker', 'inspect'):
                    return 'a' * 64
                return ''
            with patch.object(fixture, 'ROOT', root), patch.object(fixture, 'preflight'), \
                    patch.object(fixture, 'command', side_effect=fake), \
                    patch.object(fixture, 'wait_healthy'):
                result = fixture.setup(root, 'candidate', 'postgres', 'caddy')
            compose = json.loads((Path(result['runtime']) / 'compose.yaml').read_bytes())
            self.assertEqual(set(compose['services']), {'axxium-db', 'axxium'})
            for service in compose['services'].values():
                self.assertNotIn('ports', service)
                self.assertNotIn('privileged', service)
            self.assertTrue(all('--internal' in c for c in calls if c[:3] == ('docker', 'network', 'create')))
            self.assertFalse(any('-p' in c or '--publish' in c for c in calls))
            proxy = next(c for c in calls if c[:3] == ('docker', 'run', '-d') and fixture.CADDY in c)
            self.assertEqual(proxy[proxy.index('--network-alias') + 1], 'axxium.promethean.rest')
            self.assertIn(b'\t\tpath / /health /portal/*', Path(result['caddyfile']).read_bytes())


if __name__ == '__main__':
    unittest.main()
