"""Offline synthetic tests for an uninstalled candidate-specific draft.

All receipt dictionaries below are invented test fixtures, not release evidence.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import axxium_production_qualification as q

PLAN = (ROOT / "digitalocean/services/axxium/minimal-production-plan.json").read_bytes()
PLAN_OBJECT = json.loads(PLAN)
SOURCE = PLAN_OBJECT["source_sha"]
CONTROLLER = "b" * 40


def evidence():
    receipt = {"source_sha": SOURCE, "status": "passed", "assertions": 1}
    return {"repository": q.REPOSITORY, "source_sha": SOURCE,
            "integration": deepcopy(receipt), "e2e": deepcopy(receipt),
            "mutation_batches": [],
            "mutation_exception": {
                "schema": "axxium-mutation-exception/v1", "status": "approved",
                "scope": "mutation-only", "source_sha": SOURCE,
                "plan_sha256": q.WAIVER_PLAN_SHA256,
                "image_id": PLAN_OBJECT["image_id"],
                "stage_run_id": PLAN_OBJECT["stage_run_id"],
                "artifact_sha256": PLAN_OBJECT["artifact_sha256"],
                "mutation_campaign_passed": False,
                "owner_decision_reference": "synthetic-test-only"}}


class WaiverTests(unittest.TestCase):
    def admit(self, proof, plan_bytes=PLAN):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "dummy.zip"
            archive.write_bytes(b"x")
            def native_file(repository, path, revision, *args):
                return plan_bytes if path == q.PLAN_PATH else json.dumps(proof).encode()
            with patch.object(q, "main_ancestor"), patch.object(q, "native_file", side_effect=native_file), \
                 patch.object(q, "verify_native_stage", return_value={"size_in_bytes": 1}), \
                 patch.object(q, "verify_archive"):
                return q.admit(plan_bytes, CONTROLLER, archive)

    def test_candidate_waiver_is_truthful(self):
        result = self.admit(evidence())
        self.assertEqual("owner-decision", result["qualification"])
        self.assertIs(result["mutationCampaignPassed"], False)
        self.assertEqual({"admitted": False, "reason": "four completed mutation batches are required"},
                         result["waivedMutationVerdict"])

    def test_present_mutation_evidence_is_not_silently_waived(self):
        batch = {"source_sha": SOURCE, "status": "passed", "baseline": "passed", "batch_index": 0,
                 "mutants": [{"fingerprint": f"synthetic-0-{i}", "status": "killed", "phase": "test"}
                             for i in range(250)]}
        batch["mutants"][0]["status"] = "survived"
        four_batches = [deepcopy(batch) for _ in range(4)]
        for index, item in enumerate(four_batches):
            item["batch_index"] = index
        for batches, reason in [([batch], "explicitly empty batch list"),
                                (four_batches, "survived, duplicate, invalid or unevaluated mutation"),
                                (None, "explicitly empty batch list")]:
            with self.subTest(batch_count=len(batches) if batches is not None else None):
                proof = evidence()
                proof["mutation_batches"] = batches
                with self.assertRaisesRegex(q.Refusal, reason):
                    self.admit(proof)

    def test_unrelated_denial_is_not_waived(self):
        with patch.object(q, "production_admission", return_value={"admitted": False, "reason": "unrelated gate refused"}):
            with self.assertRaisesRegex(q.Refusal, "unrelated gate refused"):
                self.admit(evidence())

    def test_bad_receipts_never_bypass(self):
        for name, field, value in [("integration", "status", "failed"),
                                   ("e2e", "source_sha", "c" * 40),
                                   ("e2e", "assertions", 0),
                                   ("integration", "timed_out", True),
                                   ("e2e", "warnings", ["warning"])]:
            with self.subTest(name=name, field=field):
                proof = evidence()
                proof[name][field] = value
                with self.assertRaises(q.Refusal):
                    self.admit(proof)

    def test_invalid_waiver_never_bypasses(self):
        for field, value in [("status", "draft"), ("scope", "all-gates"),
                             ("source_sha", "c" * 40), ("plan_sha256", "f" * 64),
                             ("image_id", "sha256:" + "f" * 64),
                             ("stage_run_id", 0), ("artifact_sha256", "f" * 64),
                             ("mutation_campaign_passed", True),
                             ("owner_decision_reference", "")]:
            with self.subTest(field=field):
                proof = evidence()
                proof["mutation_exception"][field] = value
                with self.assertRaises(q.Refusal):
                    self.admit(proof)

    def test_other_plan_or_policy_refuses(self):
        with self.assertRaises(q.Refusal):
            self.admit(evidence(), PLAN + b" ")
        with patch.object(q, "WAIVER_POLICY_SHA256", "f" * 64):
            with self.assertRaises(q.Refusal):
                self.admit(evidence())

    def test_normal_policy_without_exception_is_unchanged(self):
        proof = evidence()
        del proof["mutation_exception"]
        proof["mutation_batches"] = [
            {"source_sha": SOURCE, "status": "passed", "baseline": "passed", "batch_index": n,
             "mutants": [{"fingerprint": f"synthetic-{n}-{i}", "status": "killed", "phase": "test"}
                         for i in range(250)]} for n in range(4)]
        result = self.admit(proof)
        self.assertTrue(result["admitted"])
        self.assertNotIn("mutationCampaignPassed", result)
        proof["mutation_batches"][0]["mutants"][0]["status"] = "survived"
        with self.assertRaises(q.Refusal):
            self.admit(proof)


if __name__ == "__main__":
    unittest.main()
