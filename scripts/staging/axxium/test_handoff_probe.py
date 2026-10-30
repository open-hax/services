"""Exercise fixture ownership checks without contacting a Docker daemon."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('staging_handoff_probe', ROOT / 'handoff_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

RUN, DB, APP, NETWORK = 'd' * 32, 'a' * 64, 'b' * 64, 'c' * 64
NETWORK_NAME = 'axxium-oidc-test-' + RUN


def fixtures(app_image=probe.APP_IMAGE, db_image=probe.DB_IMAGE):
    def container(ident, prefix, image):
        return [ident, '/' + prefix + RUN, image, {probe.LABEL: RUN},
                {NETWORK_NAME: {'NetworkID': NETWORK}}, [], None, {}, False]
    return {
        DB: container(DB, 'axxium-oidc-db-', db_image),
        APP: container(APP, 'axxium-oidc-app-', app_image),
        NETWORK: [NETWORK, NETWORK_NAME, True, {probe.LABEL: RUN}, {DB: {}, APP: {}}],
        probe.APP_REF: [app_image],
        probe.DB_REF: [db_image],
    }


class HandoffProbeTests(unittest.TestCase):
    def run_probe(self, metadata, db=DB, app_image=probe.APP_IMAGE, db_image=probe.DB_IMAGE):
        calls = []
        def docker(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['context', 'show']:
                return subprocess.CompletedProcess(args, 0, stdout='default\n', stderr='')
            if args[1:3] == ['context', 'inspect']:
                return subprocess.CompletedProcess(args, 0, stdout='unix:///var/run/docker.sock\n', stderr='')
            if args[1] == 'inspect' or args[1:3] in (['network', 'inspect'], ['image', 'inspect']):
                result = '\n'.join(json.dumps(x) for x in metadata[args[-1]]) + '\n'
                return subprocess.CompletedProcess(args, 0, stdout=result, stderr='')
            if 'pg_dump' in args:
                return subprocess.CompletedProcess(args, 0, stdout=b'synthetic-dump', stderr=b'')
            if 'pg_restore' in args:
                return subprocess.CompletedProcess(args, 0, stdout=b'', stderr=b'')
            raise AssertionError('Unexpected Docker call')
        argv = ['handoff_probe.py', '--db-id', db, '--app-id', APP,
                '--network-id', NETWORK, '--run-id', RUN,
                '--app-image-id', app_image, '--db-image-id', db_image]
        with mock.patch.object(sys, 'argv', argv), \
             mock.patch.dict(probe.os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}, clear=True), \
             mock.patch.object(probe.subprocess, 'run', side_effect=docker):
            try:
                probe.main()
                return True, calls
            except (ValueError, RuntimeError):
                return False, calls

    def test_valid_fixture_and_invalid_exact_id(self):
        passed, calls = self.run_probe(fixtures())
        self.assertTrue(passed)
        self.assertEqual(sum('pg_dump' in call for call in calls), 1)
        passed, calls = self.run_probe(fixtures(), db=DB[:12])
        self.assertFalse(passed)
        self.assertFalse(any('pg_dump' in call or 'inspect' in call and 'context' not in call for call in calls))

    def test_non_hosted_execution_refuses_before_docker(self):
        with mock.patch.dict(probe.os.environ, {'GITHUB_ACTIONS': 'false'}, clear=True), \
             mock.patch.object(probe.subprocess, 'run') as docker:
            with self.assertRaises(RuntimeError):
                probe.main()
            docker.assert_not_called()

    def test_remote_override_refuses_before_docker(self):
        for name in ('DOCKER_HOST', 'DOCKER_CONTEXT'):
            with self.subTest(name=name), \
                 mock.patch.dict(probe.os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', name: 'remote'}, clear=True), \
                 mock.patch.object(probe.subprocess, 'run') as docker:
                with self.assertRaises(RuntimeError):
                    probe.main()
                docker.assert_not_called()

    def test_remote_or_nondefault_context_refuses_before_dump(self):
        for context, endpoint in (('other', 'unix:///var/run/docker.sock'), ('default', 'tcp://host:2375')):
            calls = []
            def docker(args, **kwargs):
                calls.append(args)
                out = context if args[1:3] == ['context', 'show'] else endpoint
                return subprocess.CompletedProcess(args, 0, stdout=out + '\n', stderr='')
            with self.subTest(context=context, endpoint=endpoint), \
                 mock.patch.dict(probe.os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}, clear=True), \
                 mock.patch.object(probe.subprocess, 'run', side_effect=docker):
                with self.assertRaises(RuntimeError):
                    probe.main()
                self.assertFalse(any('pg_dump' in call for call in calls))

    def test_classic_image_store_ids_and_untrusted_arguments(self):
        db_config = 'sha256:' + '2' * 64
        passed, _ = self.run_probe(fixtures(probe.APP_CONFIG, db_config),
                                   app_image=probe.APP_CONFIG, db_image=db_config)
        self.assertTrue(passed)
        passed, calls = self.run_probe(fixtures(), app_image='sha256:' + 'f' * 64)
        self.assertFalse(passed)
        self.assertFalse(any('pg_dump' in call for call in calls))
        for target in (probe.APP_REF, probe.DB_REF):
            foreign = fixtures(); foreign[target] = ['sha256:' + 'e' * 64]
            passed, calls = self.run_probe(foreign)
            self.assertFalse(passed)
            self.assertFalse(any('pg_dump' in call for call in calls))

    def test_foreign_metadata_refuses_before_dump(self):
        cases = []
        foreign = fixtures(); foreign[DB][3] = {probe.LABEL: 'e' * 32}; cases.append(foreign)
        foreign = fixtures(); foreign[APP][2] = 'sha256:' + 'f' * 64; cases.append(foreign)
        foreign = fixtures(); foreign[NETWORK][4]['f' * 64] = {}; cases.append(foreign)
        foreign = fixtures(); foreign[DB][5] = [{'Type': 'bind'}]; cases.append(foreign)
        foreign = fixtures(); foreign[APP][7] = {'8787/tcp': [{'HostPort': '80'}]}; cases.append(foreign)
        foreign = fixtures(); foreign[NETWORK][2] = False; cases.append(foreign)
        foreign = fixtures(); foreign[APP][4][NETWORK_NAME]['NetworkID'] = 'f' * 64; cases.append(foreign)
        foreign = fixtures(); foreign[DB][1] = '/other'; cases.append(foreign)
        for metadata in cases:
            with self.subTest(metadata=metadata):
                passed, calls = self.run_probe(copy.deepcopy(metadata))
                self.assertFalse(passed)
                self.assertFalse(any('pg_dump' in call for call in calls))


if __name__ == '__main__':
    unittest.main()
