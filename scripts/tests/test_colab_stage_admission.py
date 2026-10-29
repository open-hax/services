"""Native publisher custody is staging evidence, never production qualification."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_stage_admission as m


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'schema': 'colab-transport-stage/v1', 'source_sha': m.SOURCE, 'source_pr': 13,
                     'publisher_run_id': 50, 'publisher_attempt': 1, 'publisher_controller_sha': m.SOURCE,
                     'artifact_id': 70, 'artifact_digest': 'sha256:' + '1' * 64,
                     'image_reference': 'ghcr.io/open-hax/cloud-cuckoo@sha256:' + '2' * 64,
                     'image_id': 'sha256:' + '3' * 64, 'license_sha256': m.LICENSE, 'tests': 136}
        self.receipt = {'schema': 'cloud-cuckoo-image/v1', 'repository': m.REPOSITORY,
                        'source_sha': m.SOURCE, 'source_pr': 13, 'workflow_sha': m.SOURCE,
                        'run_id': 50, 'run_attempt': 1, 'image_reference': self.plan['image_reference'],
                        'image_id': self.plan['image_id'], 'image_digest': 'sha256:' + '2' * 64,
                        'image_provenance_verified': True, 'metadata_verified': True,
                        'license_verified': True, 'license_sha256': m.LICENSE,
                        'license_path': '/usr/share/licenses/cloud-cuckoo/LICENSE', 'pending_smoke': 'passed',
                        'package_visibility': 'public', 'source_repository_visibility': 'public',
                        'publication_target': 'public', 'anonymous_digest_pull_verified': True,
                        'public_release_complete': True,
                        'tests': {'tests': 136, 'failures': 0, 'errors': 0, 'skipped': 0}}
        self.run = {'id': 50, 'run_attempt': 1, 'event': 'workflow_dispatch', 'head_branch': 'main',
                    'head_sha': m.SOURCE, 'path': m.WORKFLOW, 'status': 'completed', 'conclusion': 'success',
                    'repository': {'full_name': m.REPOSITORY}}
        self.pr = {'merged': True, 'number': 13, 'state': 'closed', 'merge_commit_sha': m.SOURCE,
                   'base': {'ref': 'main', 'repo': {'full_name': m.REPOSITORY}}}
        self.artifact = {'id': 70, 'expired': False, 'name': 'cloud-cuckoo-release-50-1',
                         'workflow_run': {'id': 50, 'head_sha': m.SOURCE}}
        self.current_main = m.SOURCE

    def encoded(self, *, extra=False):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('release-receipt.json', json.dumps(self.receipt))
            if extra:
                archive.writestr('untrusted-script.py', 'raise RuntimeError()')
        raw = output.getvalue()
        self.plan['artifact_digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
        self.artifact.update(digest=self.plan['artifact_digest'], size_in_bytes=len(raw))
        return raw

    def admit(self, *, raw=None):
        raw = self.encoded() if raw is None else raw
        plan_bytes = json.dumps(self.plan).encode()
        responses = {f'repos/{m.REPOSITORY}/commits/main': {'sha': self.current_main},
                     f'repos/{m.REPOSITORY}/pulls/13': self.pr,
                     f'repos/{m.REPOSITORY}/actions/runs/50': self.run,
                     f'repos/{m.REPOSITORY}/actions/artifacts/70': self.artifact}
        with patch.object(m, 'api', side_effect=lambda path: responses[path]), \
                patch.object(m, 'native_file', return_value=plan_bytes), \
                patch.object(m, 'main_ancestor') as ancestor:
            result = m.admit(plan_bytes, '4' * 40, raw)
        self.assertEqual(ancestor.call_count, 3)
        return result

    def test_real_custody_and_explicit_synthetic_stage_boundary(self):
        result = self.admit()
        self.assertTrue(result['admitted'])
        self.assertTrue(result['synthetic_upstreams'])
        self.assertFalse(result['production_qualified'])
        self.assertFalse(result['google_execution_verified'])
        self.assertEqual(result['image_reference'], self.plan['image_reference'])

    def test_plan_refuses_unowned_source_tag_or_extra_fields(self):
        for key, value in [('source_sha', 'a' * 40), ('source_pr', True), ('license_sha256', 'b' * 64),
                           ('image_reference', 'ghcr.io/open-hax/cloud-cuckoo:latest'),
                           ('artifact_id', True), ('tests', 0), ('actor_override', 'synthetic')]:
            with self.subTest(key=key):
                broken = {**self.plan, key: value}
                with self.assertRaises(Exception):
                    m.validate_plan(broken)

    def test_receipt_source_image_license_or_public_proof_drift_denies(self):
        for key, value in [('source_sha', 'a' * 40), ('workflow_sha', 'b' * 40), ('image_id', 'sha256:' + 'c' * 64),
                           ('run_id', 51), ('source_pr', 12), ('package_visibility', 'private'),
                           ('metadata_verified', False), ('anonymous_digest_pull_verified', False),
                           ('public_release_complete', False), ('license_verified', False),
                           ('license_sha256', 'e' * 64), ('pending_smoke', 'failed')]:
            with self.subTest(key=key):
                self.setUp()
                self.receipt[key] = value
                with self.assertRaises(Exception):
                    self.admit()

    def test_failures_errors_or_skips_cannot_be_called_passing_tests(self):
        for key in ('failures', 'errors', 'skipped'):
            with self.subTest(key=key):
                self.setUp()
                self.receipt['tests'][key] = 1
                with self.assertRaises(Exception):
                    self.admit()

    def test_native_run_identity_is_required_not_just_success_text(self):
        for key, value in [('id', 51), ('run_attempt', 2), ('event', 'push'), ('head_branch', 'topic'),
                           ('head_sha', 'c' * 40), ('path', '.github/workflows/other.yml'),
                           ('conclusion', 'failure'), ('status', 'in_progress')]:
            with self.subTest(key=key):
                self.setUp()
                self.run[key] = value
                with self.assertRaises(Exception):
                    self.admit()

    def test_native_artifact_expiry_name_and_run_are_required(self):
        for key, value in [('id', 71), ('expired', True), ('name', 'pasted-receipt'),
                           ('workflow_run', {'id': 51, 'head_sha': m.SOURCE})]:
            with self.subTest(key=key):
                self.setUp()
                self.artifact[key] = value
                with self.assertRaises(Exception):
                    self.admit()

    def test_zip_bytes_must_match_native_digest(self):
        raw = self.encoded()
        with self.assertRaises(Exception):
            self.admit(raw=raw + b'changed')

    def test_extra_zip_members_are_never_extracted_or_run(self):
        raw = self.encoded(extra=True)
        with self.assertRaises(Exception):
            self.admit(raw=raw)

    def test_staging_must_still_be_current_source_main(self):
        self.current_main = 'f' * 40
        with self.assertRaises(Exception):
            self.admit()

    def test_source_must_be_the_exact_merged_main_pr(self):
        for change in ({'merged': False}, {'state': 'open'}, {'merge_commit_sha': 'a' * 40},
                       {'base': {'ref': 'main', 'repo': {'full_name': 'other/cloud-cuckoo'}}}):
            with self.subTest(change=change):
                self.setUp()
                self.pr.update(change)
                with self.assertRaises(Exception):
                    self.admit()

    def test_checked_controller_plan_bytes_cannot_differ(self):
        raw = self.encoded()
        with patch.object(m, 'main_ancestor'), patch.object(m, 'native_file', return_value=b'{}'):
            with self.assertRaises(Exception):
                m.admit(json.dumps(self.plan).encode(), '4' * 40, raw)


if __name__ == '__main__':
    unittest.main()
