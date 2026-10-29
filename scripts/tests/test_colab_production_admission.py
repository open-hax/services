"""Synthetic native fixtures test admission; none are qualification evidence."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_production_admission as m

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'source_sha': 'a' * 40, 'source_pr': 21, 'publisher_run_id': 101,
            'publisher_attempt': 1, 'publisher_controller_sha': 'b' * 40, 'artifact_id': 102,
            'artifact_digest': 'sha256:' + '1' * 64, 'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:' + '2' * 64,
            'image_id': 'sha256:' + '3' * 64, 'runtime_image_id': 'sha256:' + '2' * 64,
            'license_sha256': m.LICENSE, 'tests': 182}
        self.stage = {'controller_sha': 'c' * 40, 'run_id': 201, 'attempt': 1, 'artifact_id': 202,
            'artifact_digest': 'sha256:' + '4' * 64, 'compose_sha256': '5' * 64, 'plan_sha256': '6' * 64}
        self.receipt = {'schema': 'colab-transport-stage-receipt/v1', 'phase': 'staging', 'health': 'success',
            'source_repository': m.REPOSITORY, 'source_sha': self.candidate['source_sha'],
            'image_reference': self.candidate['image_reference'], 'image_id': self.candidate['image_id'],
            'runtime_image_id': self.candidate['runtime_image_id'], 'manifest_config_binding_verified': True,
            'services_sha': self.stage['controller_sha'], 'compose_project': 'colab-mcp-transport-staging',
            'compose_sha256': self.stage['compose_sha256'], 'plan_sha256': self.stage['plan_sha256'],
            'synthetic_actor': True, 'synthetic_upstreams': True, 'human_login_verified': False,
            'oauth_flow_verified': True, 'gateway_forwarding_verified': True, 'session_revoked': True,
            'google_execution_verified': False, 'production_qualified': False,
            'unrelated_runtime_preserved': True, 'assertions': 165, 'completed_at': '2026-10-28T23:00:00Z'}
        self.rebuild_artifact()
        source = self.candidate['source_sha']
        self.proof = {'repository': m.REPOSITORY, 'source_sha': source,
            'integration': {'source_sha': source, 'status': 'passed', 'assertions': 490, 'warnings': []},
            'e2e': {'source_sha': source, 'status': 'passed', 'assertions': 148, 'warnings': []},
            'mutation_batches': [{'source_sha': source, 'status': 'passed', 'baseline': 'passed',
                'warnings': [], 'batch_index': index, 'mutants': [
                    {'fingerprint': f'synthetic-{index}-{number}', 'status': 'killed', 'phase': 'test', 'warnings': []}
                    for number in range(250)]} for index in range(4)]}

    def rebuild_artifact(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('stage-receipt.json', json.dumps(self.receipt))
        self.stage_bytes = stream.getvalue()
        self.stage['artifact_digest'] = 'sha256:' + hashlib.sha256(self.stage_bytes).hexdigest()
        self.artifact = {'id': self.stage['artifact_id'], 'expired': False,
            'name': 'colab-transport-stage-201-1', 'workflow_run': {'id': 201, 'head_sha': self.stage['controller_sha']},
            'digest': self.stage['artifact_digest'], 'size_in_bytes': len(self.stage_bytes),
            'expires_at': '2026-11-02T00:00:00Z'}

    def plan(self):
        return {'schema': 'colab-qualified-production/v1', 'host': 'knoxx.promethean.rest',
            'candidate': copy.deepcopy(self.candidate), 'stage': copy.deepcopy(self.stage), 'predecessor': m.PREDECESSOR}

    def api(self, path):
        if path == 'repos/open-hax/services/actions/runs/201':
            return {'id': 201, 'run_attempt': 1, 'path': m.WORKFLOW, 'head_sha': self.stage['controller_sha'],
                'head_branch': 'main', 'event': 'workflow_dispatch', 'repository': {'full_name': m.SERVICES},
                'status': 'completed', 'conclusion': 'success'}
        if path == 'repos/open-hax/services/actions/artifacts/202':
            return self.artifact
        self.fail('unexpected native request')

    def native(self, repository, path, revision):
        self.assertEqual((repository, revision), (m.SERVICES, 'd' * 40))
        if path == m.PLAN:
            return json.dumps(self.plan()).encode()
        self.assertEqual(path, 'digitalocean/qualifications/colab-mcp/' + 'a' * 40 + '.json')
        return json.dumps(self.proof).encode()

    def run_admission(self, **kwargs):
        with patch.object(m, 'CANDIDATE', self.candidate), patch.object(m, 'STAGE', self.stage), \
             patch.object(m, 'api', side_effect=self.api), patch.object(m, 'native_file', side_effect=self.native), \
             patch.object(m, 'main_ancestor') as ancestry, \
             patch.object(m, 'verify_publisher', return_value={'expires_at': '2026-11-02T00:00:00Z'}) as publisher:
            result = m.admit(json.dumps(self.plan()).encode(), 'd' * 40, b'fake-publisher-is-independently-verified',
                             kwargs.get('stage_bytes', self.stage_bytes), now=NOW)
            self.assertEqual(publisher.call_count, 2)
            self.assertEqual(ancestry.call_count, 4)
            return result

    def test_unresolved_shipped_plan_refuses_before_any_native_or_host_capability(self):
        raw = (ROOT / m.PLAN).read_bytes()
        with patch.object(m, 'api') as native, patch.object(m, 'main_ancestor') as ancestry:
            with self.assertRaisesRegex(ValueError, 'not pinned'):
                m.admit(raw, 'd' * 40, b'', b'')
            native.assert_not_called()
            ancestry.assert_not_called()

    def test_exact_native_publication_stage_and_canonical_qualifications_are_required(self):
        result = self.run_admission()
        self.assertTrue(result['admitted'])
        self.assertEqual(result['evaluated_mutations'], 1000)
        self.assertFalse(result['human_login_verified'])
        self.assertFalse(result['google_execution_verified'])

    def test_unpinned_alternative_image_or_predecessor_cannot_be_selected(self):
        for field in ('candidate', 'stage', 'predecessor'):
            with self.subTest(field=field):
                plan = self.plan()
                plan[field] = {}
                with patch.object(m, 'CANDIDATE', self.candidate), patch.object(m, 'STAGE', self.stage):
                    with self.assertRaises(ValueError):
                        m.validate_plan(plan)

    def test_stage_of_older_warning_bearing_source_is_not_future_candidate_proof(self):
        self.receipt['source_sha'] = '19e1adadb1b24eacca993de92f6370b400cf3d33'
        self.rebuild_artifact()
        with self.assertRaisesRegex(ValueError, 'exact candidate'):
            self.run_admission()

    def test_stale_or_tampered_stage_artifact_is_refused(self):
        for field, value in [('expired', True), ('expires_at', '2026-10-28T00:00:00Z'),
                             ('digest', 'sha256:' + '0' * 64), ('size_in_bytes', 1),
                             ('workflow_run', {'id': 201, 'head_sha': '0' * 40})]:
            with self.subTest(field=field):
                original = self.artifact.copy()
                self.artifact[field] = value
                with self.assertRaises(ValueError):
                    self.run_admission()
                self.artifact = original

    def test_exact_manifest_config_and_stage_behavior_cannot_be_replaced_by_health(self):
        for field, value in [('image_id', 'sha256:' + '0' * 64), ('runtime_image_id', 'sha256:' + '0' * 64),
                             ('oauth_flow_verified', False), ('session_revoked', False),
                             ('human_login_verified', True), ('assertions', 0), ('assertions', True)]:
            with self.subTest(field=field, value=value):
                original = self.receipt.copy()
                self.receipt[field] = value
                self.rebuild_artifact()
                with self.assertRaises(ValueError):
                    self.run_admission()
                self.receipt = original
                self.rebuild_artifact()

    def test_ordinary_qualification_denies_warning_timeout_survivor_duplicate_or_short_batch(self):
        original = copy.deepcopy(self.proof)
        for fault in ('warning', 'timeout', 'survivor', 'duplicate', 'short_batch', 'wrong_source', 'zero_assertions'):
            with self.subTest(fault=fault):
                self.proof = copy.deepcopy(original)
                if fault == 'warning': self.proof['integration']['warnings'] = ['warning']
                if fault == 'timeout': self.proof['e2e']['timed_out'] = True
                if fault == 'survivor': self.proof['mutation_batches'][0]['mutants'][0]['status'] = 'survived'
                if fault == 'duplicate': self.proof['mutation_batches'][1]['mutants'][0]['fingerprint'] = 'synthetic-0-0'
                if fault == 'short_batch': self.proof['mutation_batches'][0]['mutants'].pop()
                if fault == 'wrong_source': self.proof['source_sha'] = '0' * 40
                if fault == 'zero_assertions': self.proof['e2e']['assertions'] = 0
                with self.assertRaises(ValueError): self.run_admission()

    def test_missing_immutable_qualification_is_not_a_stage_waiver(self):
        self.proof = {}
        with self.assertRaises(ValueError): self.run_admission()

    def test_publisher_verifier_is_reused_and_expiration_is_enforced(self):
        with patch.object(m, 'CANDIDATE', self.candidate), patch.object(m, 'STAGE', self.stage), \
             patch.object(m, 'native_file', side_effect=self.native), patch.object(m, 'main_ancestor'), \
             patch.object(m, 'verify_publisher', return_value={'expires_at': '2026-10-28T00:00:00Z'}) as verifier:
            with self.assertRaisesRegex(ValueError, 'publisher artifact expired'):
                m.admit(json.dumps(self.plan()).encode(), 'd' * 40, b'publisher', self.stage_bytes, now=NOW)
            verifier.assert_called_once_with(self.candidate, b'publisher')


if __name__ == '__main__':
    unittest.main()
