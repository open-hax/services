import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from caddy_bundle_transaction import BundleItem, apply_bundle_under_lease
from identity_ingress_transaction import IngressTransactionError
from ingress_pending import refuse_pending
import website_ingress_writer


class Lease:
    def recheck(self):
        pass


class PendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journals = self.root / 'journals'
        self.journals.mkdir(mode=0o700)
        self.guard = self.root / 'bootstrap-pending'

    def test_interrupted_real_bundle_blocks_website_dispatch_before_docker(self):
        target_dir = self.root / 'live'
        target_dir.mkdir(mode=0o700)
        target = target_dir / 'Caddyfile'
        target.write_bytes(b'before')
        target.chmod(0o600)
        journal = self.journals / 'run-1'
        journal.mkdir(mode=0o700)
        item = BundleItem(target, b'partly-installed', hashlib.sha256(b'before').hexdigest())
        def interrupt():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            apply_bundle_under_lease(Lease(), [item], journal, interrupt, lambda: None, owner=os.getuid())
        self.assertEqual(target.read_bytes(), b'partly-installed')
        self.assertTrue((journal / 'pending').exists())
        with patch.object(website_ingress_writer, 'read_selection', return_value=('caddy', 'caddy-caddy-1')), \
             patch.object(website_ingress_writer, 'docker') as docker, \
             patch.object(website_ingress_writer, 'run_under_lease') as writer:
            with self.assertRaisesRegex(IngressTransactionError, 'requires recovery'):
                website_ingress_writer.dispatch_under_lease(Lease(), 'a'*64, runtime=self.root,
                    journals=self.journals, owner=os.getuid(), bootstrap_guard=self.guard)
            docker.assert_not_called()
            writer.assert_not_called()

    def test_only_matching_safe_terminal_marker_opens_pending_journal(self):
        journal = self.journals / 'run'
        journal.mkdir(mode=0o700)
        (journal / 'pending').write_bytes(b'pending\n')
        (journal / 'result').write_bytes(b'applied-and-verified\n')
        (journal / 'result').chmod(0o600)
        # A fully written but unpublished staging marker never opens the gate.
        (journal / 'settlement-prepared').write_bytes(b'applied-and-verified\n')
        (journal / 'settlement-prepared').chmod(0o600)
        with self.assertRaisesRegex(IngressTransactionError, 'requires recovery'):
            refuse_pending(self.journals, os.getuid(), bootstrap_guard=self.guard)
        (journal / 'settled').write_bytes(b'restored-and-verified\n')
        (journal / 'settled').chmod(0o600)
        with self.assertRaisesRegex(IngressTransactionError, 'requires recovery'):
            refuse_pending(self.journals, os.getuid(), bootstrap_guard=self.guard)
        (journal / 'settled').write_bytes(b'applied-and-verified\n')
        refuse_pending(self.journals, os.getuid(), bootstrap_guard=self.guard)
        (journal / 'settled').chmod(0o644)
        with self.assertRaisesRegex(IngressTransactionError, 'requires recovery'):
            refuse_pending(self.journals, os.getuid(), bootstrap_guard=self.guard)

    def test_bootstrap_pending_blocks_both_bundle_and_website_guards(self):
        self.guard.write_text('pending')
        with self.assertRaisesRegex(IngressTransactionError, '[Bb]ootstrap'):
            refuse_pending(self.journals, os.getuid(), bootstrap_guard=self.guard)
        with patch.object(website_ingress_writer, 'read_selection', return_value=('caddy-identity', 'caddy-identity-caddy-1')), \
             patch.object(website_ingress_writer, 'run_under_lease') as writer:
            with self.assertRaisesRegex(IngressTransactionError, '[Bb]ootstrap'):
                website_ingress_writer.dispatch_under_lease(Lease(), 'a'*64, runtime=self.root,
                    journals=self.journals, owner=os.getuid(), bootstrap_guard=self.guard)
            writer.assert_not_called()

    def test_website_dispatch_can_validate_its_own_guarded_transaction(self):
        runtime, managed = self.root / '1-1', self.root / 'managed'
        runtime.mkdir(mode=0o700)
        managed.mkdir(mode=0o700)
        (runtime / 'website-staging.caddy.candidate').write_bytes(b'new website')
        (runtime / 'website-staging.caddy.candidate').chmod(0o600)
        target = managed / 'website-staging.caddy'
        target.write_bytes(b'old website')
        target.chmod(0o600)
        def docker(args):
            if args[:2] == ['inspect', '--format'] and args[2] == '{{json .Mounts}}':
                return json.dumps([{'Source': str(managed), 'Destination': '/etc/caddy/managed', 'RW': False}]).encode()
            if args[:2] == ['inspect', '--format']:
                return json.dumps(['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile']).encode()
            if 'cat' in args:
                return b'import /etc/caddy/managed/*.caddy\n' if args[-1] == '/etc/caddy/Caddyfile' else target.read_bytes()
            return b'ok'
        real_run = website_ingress_writer.run_under_lease
        def run(lease, expected, **kwargs):
            return real_run(lease, expected, managed=managed, **kwargs)
        with patch.object(website_ingress_writer, 'read_selection', return_value=('caddy', 'caddy-caddy-1')), \
             patch.object(website_ingress_writer, 'docker', side_effect=docker), \
             patch.object(website_ingress_writer, 'run_under_lease', side_effect=run):
            result = website_ingress_writer.dispatch_under_lease(Lease(), hashlib.sha256(b'new website').hexdigest(),
                runtime=runtime, journals=self.journals, owner=os.getuid(), bootstrap_guard=self.guard)
        self.assertEqual(result, hashlib.sha256(b'new website').hexdigest())
        self.assertEqual(target.read_bytes(), b'new website')
        self.assertTrue((self.journals / 'website-1-1/settled').exists())
