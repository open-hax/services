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
