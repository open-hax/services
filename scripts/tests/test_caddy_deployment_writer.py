import json
import os
from pathlib import Path
import tempfile
import unittest

import caddy_deployment_writer as writer
from identity_ingress_transaction import IngressTransactionError


class Lease:
    def recheck(self):
        pass


class DeploymentWriterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.runtime = root / 'runtime'
        self.managed = root / 'managed'
        self.journals = root / 'journals'
        self.stage = root / '1-1'
        for path in (self.runtime, self.managed, self.journals, self.stage, self.managed.parent / 'identity-ingress'):
            path.mkdir(mode=0o700)
        for name in writer.FILES:
            (self.runtime / name).write_bytes((name + '-before').encode())
            (self.stage / name).write_bytes((name + '-before').encode())
            (self.runtime / name).chmod(0o600)
            (self.stage / name).chmod(0o600)
        (self.managed.parent / 'identity-ingress/Caddyfile').write_bytes(b'Caddyfile-before')
        (self.managed / 'website.caddy').write_bytes(b'old route')
        (self.managed.parent / 'identity-ingress/Caddyfile').chmod(0o600)
        (self.managed / 'website.caddy').chmod(0o600)
        self.container = {'Id': 'abc', 'Image': 'sha256:' + 'a' * 64,
                          'Config': {'User': '1000:1000', 'Cmd': ['caddy', 'run', '--config',
                              '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']},
                          'Mounts': [{'Source': str(self.managed), 'Destination': '/etc/caddy/managed', 'RW': False},
                                     {'Source': str(self.managed.parent / 'identity-ingress'),
                                      'Destination': '/etc/caddy/identity-ingress', 'RW': False}]}
        self.calls = []
        self.capture()

    def capture(self):
        baseline = writer.capture(self.runtime, self.managed, os.getuid(), lambda: self.container)
        candidate = {name: writer.sha((self.stage / name).read_bytes()) for name in writer.STAGE_FILES}
        (self.stage / 'manifest.json').write_text(json.dumps({'baseline': baseline, 'candidate': candidate}))
        (self.stage / 'manifest.json').chmod(0o600)

    def run_transaction(self, verify=None):
        return writer.run_transaction(Lease(), runtime=self.runtime, managed=self.managed,
            journals=self.journals, stage=self.stage, owner=os.getuid(), inspect=lambda: self.container,
            prepare=lambda image: self.calls.append(('prepare', image)),
            recreate=lambda image: self.calls.append(('recreate', image)),
            verify=verify or (lambda image, _oidc: self.calls.append(('verify', image))))

    def test_running_image_is_used_and_both_configs_update(self):
        self.run_transaction()
        self.assertEqual([x[0] for x in self.calls], ['prepare', 'recreate', 'verify'])
        self.assertTrue(all(x[1] == self.container['Image'] for x in self.calls))
        self.assertFalse((self.journals / '1-1/pending').exists())
        self.assertEqual((self.managed.parent / 'identity-ingress/Caddyfile').read_bytes(), b'Caddyfile-before')

    def test_failed_health_restores_prior_files_and_recreates_old_image(self):
        (self.stage / 'verify.sh').write_bytes(b'new verify')
        self.capture()
        calls = []
        def failing_once(image, _oidc):
            calls.append(image)
            if len(calls) == 1:
                raise RuntimeError('health failed')
        with self.assertRaisesRegex(IngressTransactionError, 'restored and verified'):
            self.run_transaction(failing_once)
        self.assertEqual((self.runtime / 'verify.sh').read_bytes(), b'verify.sh-before')
        self.assertEqual(len(calls), 2)
        self.assertEqual(sum(c[0] == 'recreate' for c in self.calls), 2)

    def test_drift_and_pending_refuse_before_recreation(self):
        (self.managed / 'website.caddy').write_bytes(b'new route')
        with self.assertRaisesRegex(IngressTransactionError, 'drifted'):
            self.run_transaction()
        self.assertEqual(self.calls, [])
        (self.journals / 'prior').mkdir(mode=0o700)
        (self.journals / 'prior/pending').write_text('pending')
        with self.assertRaisesRegex(IngressTransactionError, 'requires recovery'):
            self.run_transaction()

    def test_root_and_unrelated_changes_refused(self):
        self.container['Config']['User'] = ''
        with self.assertRaisesRegex(IngressTransactionError, 'UID 1000'):
            self.run_transaction()
        self.container['Config']['User'] = '1000:1000'
        (self.stage / 'Caddyfile').write_bytes(b'new unrelated route')
        self.capture()
        with self.assertRaisesRegex(IngressTransactionError, 'Unrecognized'):
            self.run_transaction()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
