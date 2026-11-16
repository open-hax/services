"""Exercise unified mailbox admission with an isolated OCI archive and receipts."""
from datetime import timedelta
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import call, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import axxium_unified_mailbox_admission as admission
from test_axxium_production_qualification import CONTROLLER, NOW, SOURCE, STAGE_CONTROLLER, create_archive, encoded


class UnifiedMailboxAdmissionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.archive = Path(directory.name) / "artifact.zip"
        image = create_archive(self.archive, nested=True)
        self.plan = {"schema": admission.PLAN_SCHEMA, "host": "knoxx.promethean.rest",
                     "source_sha": SOURCE, "source_pr": 83, "image_id": image,
                     "stage_controller_sha": STAGE_CONTROLLER, "stage_run_id": 61,
                     "stage_workflow_path": ".github/workflows/service-environment-staging.yml",
                     "artifact_id": 71, "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        self.plan_bytes = encoded(self.plan)
        self.plan_hash = hashlib.sha256(self.plan_bytes).hexdigest()
        self.receipts = {"schema": admission.QUALIFICATION_SCHEMA, "repository": admission.REPOSITORY,
                         "source_sha": SOURCE, "image_id": image, "plan_sha256": self.plan_hash,
                         "status": "passed", "mutation_batches": [],
                         "mutation_harness": {"status": "retired", "campaign_passed": False}}
        for kind in ("integration", "e2e"):
            self.receipts[kind] = {"kind": kind, "source_sha": SOURCE, "status": "passed", "assertions": 2,
                                   "warnings": [], "timed_out": False, "completed_at": NOW.isoformat(),
                                   "evidence_sha256": "e" * 64}
        self.receipts["e2e"]["image_id"] = image
        self.qualification_path = f"digitalocean/qualifications/axxium/{SOURCE}.json"
        self.artifact = {"size_in_bytes": self.archive.stat().st_size}

    def verify(self, proof):
        return admission.verify_qualification(proof, self.plan, self.plan_hash, NOW)

    def test_reviewed_plan_and_real_archive_with_twice_checked_native_custody(self):
        with patch.object(admission, "main_ancestor") as ancestry, \
                patch.object(admission, "native_file", side_effect=[self.plan_bytes, encoded(self.receipts)]) as files, \
                patch.object(admission, "verify_native_stage", return_value=self.artifact) as native_stage:
            verdict = admission.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)
        self.assertEqual(verdict, {"admitted": True, "qualification": "unified-mailbox", "source_sha": SOURCE,
                                  "plan_sha256": self.plan_hash, "image_id": self.plan["image_id"],
                                  "mutationHarness": "retired", "mutationCampaignPassed": False})
        self.assertEqual(files.call_args_list,
                         [call(admission.SERVICES, admission.PLAN_PATH, CONTROLLER, admission.MAX_PLAN),
                          call(admission.SERVICES, self.qualification_path, CONTROLLER)])
        self.assertEqual(ancestry.call_args_list, [call(admission.SERVICES, CONTROLLER)] * 2)
        self.assertEqual(native_stage.call_args_list, [call(self.plan, NOW)] * 2)

    def test_requires_exact_plan_schema_and_validation_of_native_plan_bytes(self):
        legacy = encoded({**self.plan, "schema": "axxium-minimal-production/v1"})
        with self.assertRaisesRegex(admission.Refusal, "unsupported unified mailbox"):
            admission.admit(legacy, CONTROLLER, self.archive, now=NOW)
        with patch.object(admission, "main_ancestor"), \
                patch.object(admission, "native_file", return_value=self.plan_bytes + b"\n"), \
                patch.object(admission, "verify_native_stage") as stage:
            with self.assertRaisesRegex(admission.Refusal, "plan differs from accepted immutable controller"):
                admission.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)
        stage.assert_not_called()

    def test_missing_qualification_refuses_even_with_a_valid_archive(self):
        with patch.object(admission, "main_ancestor"), \
                patch.object(admission, "native_file", side_effect=[self.plan_bytes, OSError()]), \
                patch.object(admission, "verify_native_stage") as stage:
            with self.assertRaisesRegex(admission.Refusal, "immutable unified mailbox source qualification unavailable"):
                admission.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)
        stage.assert_not_called()

    def test_proof_is_bound_to_the_exact_image_source_and_plan(self):
        for field in ("source_sha", "image_id", "plan_sha256", "schema", "repository", "status"):
            with self.subTest(field=field), self.assertRaisesRegex(admission.Refusal, "does not bind the candidate"):
                self.verify({**self.receipts, field: "other"})
        with self.assertRaisesRegex(admission.Refusal, "rehearsal image differs"):
            self.verify({**self.receipts, "e2e": {**self.receipts["e2e"], "image_id": "other"}})

    def test_receipts_explicitly_bind_successful_clean_execution(self):
        for kind in ("integration", "e2e"):
            original = self.receipts[kind]
            for field, value in (("kind", "stage"), ("source_sha", "wrong"), ("status", "failed"),
                                 ("assertions", 0), ("assertions", True), ("warnings", ["unexpected warning"]),
                                 ("timed_out", 0), ("timed_out", True), ("evidence_sha256", "not a digest"),
                                 ("completed_at", "2026-10-29"),
                                 ("completed_at", (NOW + timedelta(seconds=1)).isoformat())):
                with self.subTest(kind=kind, field=field, value=value), self.assertRaisesRegex(admission.Refusal, kind):
                    self.verify({**self.receipts, kind: {**original, field: value}})
            for field in original:
                with self.subTest(kind=kind, missing=field), self.assertRaises(admission.Refusal):
                    self.verify({**self.receipts, kind: {key: value for key, value in original.items() if key != field}})

    def test_retired_harness_cannot_be_claimed_as_success_or_erase_existing_results(self):
        for changed in ({"mutation_batches": [{"status": "failed"}]}, {"mutation_batches": None},
                        {"mutation_harness": {"status": "retired", "campaign_passed": True}},
                        {"mutation_harness": {"status": "retired", "campaign_passed": 0}},
                        {"mutation_harness": {"status": "waived", "campaign_passed": False}}):
            with self.subTest(changed=changed), self.assertRaisesRegex(admission.Refusal, "retired mutation harness"):
                self.verify({**self.receipts, **changed})

    def test_final_native_custody_refusal_blocks_an_inspected_archive(self):
        refusal = admission.Refusal("expired artifact fixture")
        with patch.object(admission, "main_ancestor"), \
                patch.object(admission, "native_file", side_effect=[self.plan_bytes, encoded(self.receipts)]), \
                patch.object(admission, "verify_native_stage", side_effect=[self.artifact, refusal]):
            with self.assertRaisesRegex(admission.Refusal, "expired artifact fixture"):
                admission.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)

    def test_archive_digest_tamper_is_refused(self):
        self.archive.write_bytes(b"x" * self.archive.stat().st_size)
        with patch.object(admission, "main_ancestor"), \
                patch.object(admission, "native_file", side_effect=[self.plan_bytes, encoded(self.receipts)]), \
                patch.object(admission, "verify_native_stage", return_value=self.artifact):
            with self.assertRaisesRegex(admission.Refusal, "archive differs from native digest"):
                admission.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)


if __name__ == "__main__":
    unittest.main()
