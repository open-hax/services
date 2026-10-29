import hashlib
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

    def test_bootstrap_pending_blocks_both_bundle_and_website_guards(self):
        self.guard.write_text('pending')
        with self.assertRaisesRegex(IngressTransactionError, 'bootstrap'):
            refuse_pending(self.journals, os.getuid(), bootstrap_guard=self.guard)
        with patch.object(website_ingress_writer, 'read_selection', return_value=('caddy-identity', 'caddy-identity-caddy-1')), \
             patch.object(website_ingress_writer, 'run_under_lease') as writer:
            with self.assertRaisesRegex(IngressTransactionError, 'bootstrap'):
                website_ingress_writer.dispatch_under_lease(Lease(), 'a'*64, runtime=self.root,
                    journals=self.journals, owner=os.getuid(), bootstrap_guard=self.guard)
            writer.assert_not_called()
