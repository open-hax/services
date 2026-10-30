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


def fixtures():
    def container(ident, prefix, image):
        return [ident, '/' + prefix + RUN, image, {probe.LABEL: RUN},
                {NETWORK_NAME: {'NetworkID': NETWORK}}, [], None, {}, False]
    return {
        DB: container(DB, 'axxium-oidc-db-', probe.DB_IMAGE),
        APP: container(APP, 'axxium-oidc-app-', probe.APP_IMAGE),
        NETWORK: [NETWORK, NETWORK_NAME, True, {probe.LABEL: RUN}, {DB: {}, APP: {}}],
    }


class HandoffProbeTests(unittest.TestCase):
    def run_probe(self, metadata, db=DB):
        calls = []
        def docker(args, **kwargs):
            calls.append(args)
            if args[1] == 'inspect' or args[1:3] == ['network', 'inspect']:
                result = '\n'.join(json.dumps(x) for x in metadata[args[-1]]) + '\n'
                return subprocess.CompletedProcess(args, 0, stdout=result, stderr='')
            if 'pg_dump' in args:
                return subprocess.CompletedProcess(args, 0, stdout=b'synthetic-dump', stderr=b'')
            if 'pg_restore' in args:
                return subprocess.CompletedProcess(args, 0, stdout=b'', stderr=b'')
            raise AssertionError('Unexpected Docker call')
        argv = ['handoff_probe.py', '--db-id', db, '--app-id', APP,
                '--network-id', NETWORK, '--run-id', RUN]
        with mock.patch.object(sys, 'argv', argv), \
             mock.patch.dict(probe.os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}), \
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
        self.assertEqual(calls, [])

    def test_non_hosted_execution_refuses_before_docker(self):
        with mock.patch.dict(probe.os.environ, {'GITHUB_ACTIONS': 'false'}, clear=True), \
             mock.patch.object(probe.subprocess, 'run') as docker:
            with self.assertRaises(RuntimeError):
                probe.main()
            docker.assert_not_called()

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
