"""Synthetic native publication fixtures do not constitute a new Stage run."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_stage_upgrade_admission as admission
import colab_transport_stage_upgrade as upgrade
from colab_stage_admission import LICENSE


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'source_sha': 'a' * 40, 'source_pr': 15, 'publisher_run_id': 1, 'publisher_attempt': 1,
            'publisher_controller_sha': 'a' * 40, 'artifact_id': 2, 'artifact_digest': 'sha256:' + '1' * 64,
            'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:' + '2' * 64,
            'image_id': 'sha256:' + '3' * 64, 'runtime_image_id': 'sha256:' + '2' * 64,
            'license_sha256': LICENSE, 'tests': 182}
        self.plan = {'schema': 'colab-transport-stage-upgrade/v1', 'candidate': self.candidate, 'predecessor': upgrade.OLD}

    def test_unresolved_candidate_refuses_before_native_or_host_capability(self):
        with patch.object(admission, 'main_ancestor') as ancestry, patch.object(admission, 'verify_publisher') as publisher:
            with self.assertRaises(upgrade.Refused): admission.admit(json.dumps({**self.plan, 'candidate': None}).encode(), 'b' * 40, b'')
            ancestry.assert_not_called()
            publisher.assert_not_called()

    def test_fixed_new_source_calls_shared_native_publisher_and_immutable_controller(self):
        raw = json.dumps(self.plan).encode()
        with patch.object(upgrade, 'CANDIDATE', self.candidate), patch.object(admission, 'main_ancestor') as ancestry, \
             patch.object(admission, 'native_file', return_value=raw), \
             patch.object(admission, 'verify_publisher', return_value={'expires_at': '2026-11-01T00:00:00Z'}) as publisher:
            result = admission.admit(raw, 'b' * 40, b'publisher', now=datetime(2026, 10, 29, tzinfo=timezone.utc))
            ancestry.assert_called_once_with('open-hax/services', 'b' * 40)
            publisher.assert_called_once_with(self.candidate, b'publisher', current_main=True)
            self.assertTrue(result['admitted'])
            self.assertFalse(result['production_qualified'])
            self.assertFalse(result['google_execution_verified'])

    def test_changed_predecessor_or_reuse_of_19e_source_or_mutable_image_refuses(self):
        for key, value in [('source_sha', upgrade.OLD['source_sha']), ('image_reference', 'ghcr.io/open-hax/cloud-cuckoo:main'),
                           ('runtime_image_id', 'sha256:' + '0' * 64), ('license_sha256', '0' * 64)]:
            candidate = {**self.candidate, key: value}
            with self.subTest(key=key), patch.object(upgrade, 'CANDIDATE', candidate), self.assertRaises(upgrade.Refused):
                upgrade.validate_plan({**self.plan, 'candidate': candidate})
        with patch.object(upgrade, 'CANDIDATE', self.candidate), self.assertRaises(upgrade.Refused):
            upgrade.validate_plan({**self.plan, 'predecessor': {**upgrade.OLD, 'services_sha': '0' * 40}})

    def test_native_expiration_or_controller_byte_drift_cannot_become_admission(self):
        raw = json.dumps(self.plan).encode()
        for native, expires in [(b'changed', '2026-11-01T00:00:00Z'), (raw, '2026-10-28T00:00:00Z')]:
            with self.subTest(native=native == raw), patch.object(upgrade, 'CANDIDATE', self.candidate), \
                 patch.object(admission, 'main_ancestor'), patch.object(admission, 'native_file', return_value=native), \
                 patch.object(admission, 'verify_publisher', return_value={'expires_at': expires}), self.assertRaises(ValueError):
                admission.admit(raw, 'b' * 40, b'publisher', now=datetime(2026, 10, 29, tzinfo=timezone.utc))


if __name__ == '__main__': unittest.main()
