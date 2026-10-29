import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from contextlib import contextmanager
from unittest.mock import patch

from identity_ingress_transaction import IngressTransactionError
from ingress_owner import read_selection, begin_transition, settle_transition
import website_ingress_writer


class OwnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.record = self.directory / 'owner.json'

    def put(self, value):
        self.record.write_text(json.dumps(value))
        self.record.chmod(0o644)

    def read(self):
        return read_selection(self.directory, owner=os.getuid())

    def test_cutover_changes_both_fixed_project_and_container(self):
        self.put({'version': 1, 'phase': 'stable', 'project': 'caddy'})
        self.assertEqual(self.read(), ('caddy', 'caddy-caddy-1'))
        self.put({'version': 1, 'phase': 'stable', 'project': 'caddy-identity'})
        self.assertEqual(self.read(), ('caddy-identity', 'caddy-identity-caddy-1'))

    def test_interrupted_cutover_is_never_interpreted_as_legacy(self):
        for record in ({'version': 1, 'phase': 'transition', 'project': 'caddy'},
                       {'version': 1, 'phase': 'stable', 'project': 'caddy-identity', 'old': 'caddy'},
                       {'version': 1, 'phase': 'stable', 'project': 'arbitrary'},
                       {'version': 2, 'phase': 'stable', 'project': 'caddy'}):
            self.put(record)
            with self.assertRaises(IngressTransactionError):
                self.read()

    def test_missing_symlink_and_group_writable_record_fail_closed(self):
        with self.assertRaises(FileNotFoundError):
            self.read()
        target = self.directory / 'other'
        target.write_text('{}')
        self.record.symlink_to(target)
        with self.assertRaises(OSError):
            self.read()
        self.record.unlink()
        self.put({'version': 1, 'phase': 'stable', 'project': 'caddy'})
        self.record.chmod(0o664)
        with self.assertRaises(IngressTransactionError):
            self.read()

    def test_verify_refuses_stale_old_project_before_docker(self):
        source = Path('digitalocean/services/caddy/verify.sh').read_text()
        reader = self.directory / 'reader.py'
        reader.write_text('print("caddy-identity")\n')
        script = self.directory / 'verify.sh'
        script.write_text(source.replace('/usr/local/lib/open-hax/ingress_owner.py', str(reader)))
        docker = self.directory / 'docker'
        docker.write_text('#!/bin/sh\ntouch "' + str(self.directory / 'docker-called') + '"\n')
        docker.chmod(0o755)
        environment = dict(os.environ, KNOXX_PUBLIC_HOST='fixture.test', PROXX_PUBLIC_HOST='fixture.test',
                           CADDY_COMPOSE_PROJECT='caddy', PATH=str(self.directory) + ':' + os.environ['PATH'])
        result = subprocess.run(['bash', str(script)], env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not the selected ingress owner', result.stderr)
        self.assertFalse((self.directory / 'docker-called').exists())

    def test_website_cli_targets_new_selected_container(self):
        class Lease:
            def recheck(self):
                pass
        @contextmanager
        def lock():
            yield Lease()
        observed = []
        def writer(_lease, _sha, **kwargs):
            observed.append(kwargs['container'])
            return 'fixture'
        with patch.object(website_ingress_writer, 'ingress_lock', lock), \
             patch.object(website_ingress_writer, 'read_selection', return_value=('caddy-identity', 'caddy-identity-caddy-1')), \
             patch.object(website_ingress_writer, 'run_under_lease', writer), \
             patch('sys.argv', ['website_ingress_writer.py', 'a' * 64, '1-1']):
            website_ingress_writer.main()
        self.assertEqual(observed, ['caddy-identity-caddy-1'])

    def test_atomic_transition_blocks_writers_until_verified_settlement(self):
        class Lease:
            def recheck(self):
                pass
        lease = Lease()
        self.put({'version': 1, 'phase': 'stable', 'project': 'caddy'})
        begin_transition(lease, self.directory, owner=os.getuid())
        with self.assertRaises(IngressTransactionError):
            self.read()
        with self.assertRaisesRegex(IngressTransactionError, 'not been verified'):
            settle_transition(lease, 'caddy-identity', lambda *_: False, self.directory, owner=os.getuid())
        with self.assertRaises(IngressTransactionError):
            self.read()
        settle_transition(lease, 'caddy-identity', lambda project, container: (project, container) ==
                          ('caddy-identity', 'caddy-identity-caddy-1'), self.directory, owner=os.getuid())
        self.assertEqual(self.read(), ('caddy-identity', 'caddy-identity-caddy-1'))
        with self.assertRaisesRegex(IngressTransactionError, 'baseline changed'):
            begin_transition(lease, self.directory, owner=os.getuid())


if __name__ == '__main__':
    unittest.main()
