"""Local launcher safety checks; never contacts a Docker daemon."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('stealth_stage_test', HERE / 'stealth_stage.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)

RUN = 'a' * 32
TAG = 'axxium-fixture-' + RUN + ':fixture-' + RUN


class LauncherTests(unittest.TestCase):
    def test_selection_excludes_routes_and_docker_pools(self):
        routes = [{'dst': 'default'}, {'dst': '172.16.0.0/24'}, {'dst': '172.16.1.1'}]
        networks = [{'IPAM': {'Config': [{'Subnet': '172.16.2.0/24'}, {'Subnet': 'fd00::/64'}]}}]
        selected = stage.select_subnets(routes, networks)
        self.assertEqual(selected, ['172.16.1.16/28', '172.16.1.32/28'])
        with self.assertRaises(stage.Refused):
            stage.select_subnets([{'dst': '172.16.0.0/12'}], [])
        with self.assertRaises(stage.Refused):
            stage.select_subnets([], [{'IPAM': {'Config': [{'Subnet': 'bad'}]}}])

    def test_precreation_reinventories_each_network_and_records_before_write(self):
        prefix = 'axxium-fixture-' + RUN
        label = 'codex.axxium-recovery-fixture=' + RUN
        calls = []
        def docker(args):
            calls.append(args)
            name = args[-1]
            if args[:2] == ['network', 'create']:
                self.assertTrue((root / ('04-network-' + str(len(calls) // 3) + '-pending.json')).exists())
                return ('a' if name.endswith('ingress') else 'b') * 64
            subnet = '172.16.0.0/28' if name.endswith('ingress') else '172.16.0.16/28'
            return __import__('json').dumps([{'Name': name, 'Id': ('a' if name.endswith('ingress') else 'b') * 64,
                'Internal': True, 'Driver': 'bridge', 'Labels': {'codex.axxium-recovery-fixture': RUN},
                'Containers': {}, 'IPAM': {'Config': [{'Subnet': subnet}]}}])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(stage, 'docker', side_effect=docker), \
                 mock.patch.object(stage, 'network_inventory', side_effect=[['172.16.0.0/28'], ['172.16.0.16/28']]) as inventory:
                result = stage.create_fixture_networks(prefix, label, root)
            self.assertEqual(inventory.call_count, 2)
            self.assertEqual(len(result), 2)
            self.assertTrue((root / '04-network-1-created.json').exists())
        with mock.patch.object(stage, 'docker') as docker:
            with self.assertRaises(stage.Refused):
                stage.create_fixture_networks('production', label, Path('/tmp'))
            docker.assert_not_called()

    def test_existing_image_tag_refuses_before_image_load(self):
        calls = []
        def docker(args, **kwargs):
            calls.append(args)
            if args == ['context', 'show']: return 'default'
            if args[:2] == ['context', 'inspect']: return 'unix:///var/run/docker.sock'
            if args[:2] == ['image', 'ls']: return TAG
            raise AssertionError('Unexpected operation: ' + repr(args))
        def prepare(source, root, run_id):
            return {'host_root': str(root / 'open-hax')}
        def derive(plan, source, target, run_id):
            return {'tag': TAG}
        postgres = {'Id': 'sha256:' + 'b' * 64, 'Config': {'Volumes': {'/var/lib/postgresql/data': {}}}}
        caddy = {'Id': 'sha256:' + 'c' * 64, 'Config': {'Volumes': None}}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(stage, 'PARENT', Path(directory) / 'runs'), \
             mock.patch.object(stage.os, 'getuid', return_value=1000), \
             mock.patch.dict(os.environ, {'AXXIUM_STEALTH_FIXTURE_APPLY': '1'}, clear=True), \
             mock.patch.object(stage, 'load_tools', return_value=(
                 {'fixture': 'synthetic'}, types.SimpleNamespace(prepare=prepare, PLAN='plan'),
                 types.SimpleNamespace(derive=derive))), \
             mock.patch.object(stage, 'image_info', side_effect=[postgres, caddy]), \
             mock.patch.object(stage, 'docker', side_effect=docker), \
             mock.patch.object(stage.secrets, 'token_hex', return_value=RUN):
            # The real owner check is deliberately preserved; when tests run
            # under a non-1000 user, fake only this fresh parent metadata.
            real_stat = Path.stat
            def stat_with_fixture_owner(path, *args, **kwargs):
                value = real_stat(path, *args, **kwargs)
                if path == stage.PARENT:
                    values = list(value)
                    values[4] = 1000
                    return os.stat_result(values)
                return value
            with mock.patch.object(Path, 'stat', stat_with_fixture_owner), self.assertRaises(stage.Refused):
                stage.main()
        self.assertFalse(any(call and call[0] == 'load' for call in calls))

    def test_missing_explicit_fixture_marker_refuses_before_docker(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(stage, 'docker') as docker:
            with self.assertRaises(stage.Refused):
                stage.main()
            docker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
