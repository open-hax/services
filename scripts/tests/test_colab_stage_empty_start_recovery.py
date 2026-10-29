"""Only the exact measured empty79903 start can release its hold; preserve bytes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recover_colab_stage_empty_start as m
import colab_transport_stage as stage

ROOT = Path(__file__).resolve().parents[2]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime, self.state = self.root / 'runtime', self.root / 'state'
        for path in (self.runtime, self.state):
            path.mkdir(mode=0o700)
        self.write(self.runtime / '.lock', b'')
        self.journal = {'schema': 'colab-stage-pending/v1', 'controller_sha': m.FAILED_CONTROLLER,
                        'source_sha': m.SOURCE, 'image_reference': m.IMAGE}
        self.raw = json.dumps(self.journal, indent=2).encode()
        self.selector = ('COLAB_MCP_IMAGE=' + m.IMAGE + '\n').encode()
        self.write(self.runtime / '.pending', self.raw)
        self.write(self.runtime / 'environment', self.selector)
        client = {'issuer': stage.ISSUER, 'client_id': 'knoxx-colab-transport-staging', 'client_secret': 's' * 48,
                  'allowed_subjects': [stage.ACTOR], 'allowed_client_redirect_uris': ['http://127.0.0.1:18776/callback']}
        self.write(self.state / 'axxium-client.json', json.dumps(client).encode())
        self.write(self.state / 'fixture-token', b't' * 48)
        (self.state / 'oauth').mkdir(mode=0o700)
        controller = self.runtime / 'controllers' / m.FAILED_CONTROLLER / 'digitalocean/services/colab-mcp'
        controller.mkdir(parents=True)
        self.write(controller / 'transport-stage-plan.json',
                   (ROOT / 'digitalocean/services/colab-mcp/transport-stage-plan.json').read_bytes())
        old = (ROOT / 'digitalocean/services/colab-mcp/compose.transport-stage.yaml').read_bytes().replace(
            b'["/tmp:size=32m,mode=1777"]', b'[/tmp:size=32m,mode=1777]')
        self.write(controller / 'compose.transport-stage.yaml', old)
        self.patches = [patch.object(m, 'RUNTIME', self.runtime), patch.object(m, 'STATE', self.state),
                        patch.object(m, 'OWNER_UID', os.getuid()), patch.object(stage, 'OWNER_UID', os.getuid()),
                        patch.object(m, 'require_identity'), patch.object(m, 'run', return_value=b'')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def write(self, path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)

    def test_exact_empty_claim_archives_selector_first_pending_last_and_preserves_private_files(self):
        saved = {p: p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        order = []
        real = m.archive_file
        def archive(src, dst, raw):
            order.append(src.name)
            if src.name == 'environment':
                self.assertTrue((self.runtime / '.pending').exists())
            return real(src, dst, raw)
        with patch.object(m, 'archive_file', side_effect=archive):
            result = m.recover()
        self.assertTrue(result['recovered'])
        self.assertEqual(order, ['environment', '.pending'])
        self.assertFalse((self.runtime / '.pending').exists())
        self.assertFalse((self.runtime / 'environment').exists())
        self.assertEqual((self.runtime / ('failed-empty-start-' + m.FAILED_CONTROLLER + '.environment')).read_bytes(), self.selector)
        self.assertEqual((self.runtime / ('failed-empty-start-' + m.FAILED_CONTROLLER + '.pending.json')).read_bytes(), self.raw)
        self.assertEqual({p: p.read_bytes() for p in saved}, saved)
        self.assertFalse(result['deployment_retried'])

    def test_wrong_controller_plan_selector_or_historical_archive_refuses(self):
        for key, value in [('controller_sha', 'a' * 40), ('source_sha', 'b' * 40), ('image_reference', 'image:latest')]:
            with self.subTest(key=key):
                self.write(self.runtime / '.pending', json.dumps({**self.journal, key: value}).encode())
                with self.assertRaises(Exception):
                    m.recover()
                self.assertTrue((self.runtime / 'environment').exists())
        self.write(self.runtime / '.pending', self.raw)
        self.write(self.runtime / ('failed-empty-start-' + m.FAILED_CONTROLLER + '.environment'), b'prior-evidence')
        with self.assertRaises(Exception):
            m.recover()
        self.assertEqual((self.runtime / ('failed-empty-start-' + m.FAILED_CONTROLLER + '.environment')).read_bytes(), b'prior-evidence')

    def test_any_actual_container_or_oauth_state_or_healthy_receipt_preserves_hold(self):
        with patch.object(m, 'run', return_value=b'actual-container-id\n'):
            with self.assertRaises(Exception):
                m.recover()
        self.write(self.state / 'oauth' / 'existing-session', b'owned-state')
        with self.assertRaises(Exception):
            m.recover()
        (self.state / 'oauth' / 'existing-session').unlink()
        self.write(self.runtime / 'stage-receipt.json', b'{}')
        with self.assertRaises(Exception):
            m.recover()
        self.assertEqual((self.runtime / '.pending').read_bytes(), self.raw)

    def test_selector_archive_failure_keeps_pending_and_never_overwrites(self):
        with patch.object(m.os, 'link', side_effect=OSError('uncertain link')):
            with self.assertRaises(Exception):
                m.recover()
        self.assertEqual((self.runtime / '.pending').read_bytes(), self.raw)
        self.assertEqual((self.runtime / 'environment').read_bytes(), self.selector)

    def test_new_oauth_state_after_selector_archive_retains_pending(self):
        original = m.archive_file
        def archive(src, dst, raw):
            original(src, dst, raw)
            if src.name == 'environment':
                self.write(self.state / 'oauth' / 'late-state', b'preserve')
        with patch.object(m, 'archive_file', side_effect=archive):
            with self.assertRaises(Exception):
                m.recover()
        self.assertEqual((self.runtime / '.pending').read_bytes(), self.raw)

    def test_recovery_never_becomes_an_idempotent_hold_clearer(self):
        m.recover()
        with self.assertRaises(Exception):
            m.recover()


if __name__ == '__main__':
    unittest.main()
