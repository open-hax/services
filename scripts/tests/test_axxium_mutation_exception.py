"""Axxium production admission does not use mutation batches.

All receipt dictionaries below are invented test fixtures, not release evidence.
The shared production_admission mutation rule stays in force for its other callers.
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
from environment_policy import production_admission

PLAN = (ROOT / "digitalocean/services/axxium/minimal-production-plan.json").read_bytes()
PLAN_OBJECT = json.loads(PLAN)
SOURCE = PLAN_OBJECT["source_sha"]
CONTROLLER = "b" * 40
CANDIDATE_PROOF = json.loads(
    (ROOT / f"digitalocean/qualifications/axxium/{SOURCE}.json").read_text())


def evidence():
    receipt = {"source_sha": SOURCE, "status": "passed", "assertions": 1}
    return {"repository": q.REPOSITORY, "source_sha": SOURCE,
            "integration": deepcopy(receipt), "e2e": deepcopy(receipt),
            "mutation_batches": [], "mutation_campaign_passed": False}


class AxxiumReceiptAdmissionTests(unittest.TestCase):
    def admit(self, proof, plan_bytes=PLAN, *, stage=None, archive_check=None):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "dummy.zip"
            archive.write_bytes(b"x")
            def native_file(repository, path, revision, *args):
                if path == q.PLAN_PATH:
                    return plan_bytes
                if path == f"digitalocean/qualifications/axxium/{json.loads(plan_bytes)['source_sha']}.json":
                    return json.dumps(proof).encode()
                raise q.Refusal("immutable source qualification unavailable")
            stage_mock = stage if stage is not None else patch.object(
                q, "verify_native_stage", return_value={"size_in_bytes": 1})
            archive_mock = archive_check if archive_check is not None else patch.object(q, "verify_archive")
            with patch.object(q, "main_ancestor"), patch.object(q, "native_file", side_effect=native_file), \
                 stage_mock, archive_mock:
                return q.admit(plan_bytes, CONTROLLER, archive)

    def test_shared_policy_still_requires_four_mutation_batches(self):
        receipt = {"source_sha": SOURCE, "status": "passed", "assertions": 1}
        verdict = production_admission(SOURCE, receipt, deepcopy(receipt), [])
        self.assertEqual({"admitted": False, "reason": "four completed mutation batches are required"}, verdict)
        self.assertFalse(production_admission(
            CANDIDATE_PROOF["source_sha"], CANDIDATE_PROOF["integration"], CANDIDATE_PROOF["e2e"],
            CANDIDATE_PROOF["mutation_batches"])["admitted"])

    def test_empty_absent_or_failed_mutation_evidence_does_not_block(self):
        cases = {
            "empty": evidence(),
            "absent": evidence(),
            "campaign_false": evidence(),
            "survivor": evidence(),
            "committed_candidate": deepcopy(CANDIDATE_PROOF),
        }
        del cases["absent"]["mutation_batches"]
        cases["campaign_false"]["mutation_campaign_passed"] = False
        cases["campaign_false"]["mutation_exception"] = {"mutation_campaign_passed": False, "status": "approved"}
        cases["survivor"]["mutation_batches"] = [{
            "source_sha": SOURCE, "status": "passed", "baseline": "passed", "batch_index": 0,
            "mutants": [{"fingerprint": "synthetic-0-0", "status": "survived", "phase": "test"}]}]
        self.assertEqual(cases["committed_candidate"]["mutation_batches"], [])
        self.assertIs(cases["committed_candidate"]["mutation_exception"]["mutation_campaign_passed"], False)
        for name, proof in cases.items():
            with self.subTest(name=name):
                result = self.admit(proof)
                self.assertTrue(result["admitted"])
                self.assertEqual(SOURCE, result["source_sha"])
                self.assertNotIn("mutationCampaignPassed", result)
                self.assertNotIn("qualification", result)
                self.assertNotIn("waivedMutationVerdict", result)

    def test_other_source_is_not_blocked_by_the_retired_one_sha_waiver(self):
        plan = deepcopy(PLAN_OBJECT)
        plan["source_sha"] = "c" * 40
        plan_bytes = json.dumps(plan).encode()
        proof = evidence()
        proof["source_sha"] = plan["source_sha"]
        proof["integration"]["source_sha"] = plan["source_sha"]
        proof["e2e"]["source_sha"] = plan["source_sha"]
        result = self.admit(proof, plan_bytes)
        self.assertTrue(result["admitted"])
        self.assertEqual(plan["source_sha"], result["source_sha"])
        self.assertNotIn("qualification", result)

    def test_receipt_failures_still_refuse(self):
        for name, field, value in [("integration", "status", "failed"),
                                   ("e2e", "source_sha", "c" * 40),
                                   ("e2e", "assertions", 0),
                                   ("integration", "timed_out", True),
                                   ("e2e", "warnings", ["warning"]),
                                   ("integration", "assertions", True)]:
            with self.subTest(name=name, field=field):
                proof = evidence()
                proof[name][field] = value
                with self.assertRaisesRegex(q.Refusal, name):
                    self.admit(proof)
        for label, mutate in [("missing_integration", lambda proof: proof.pop("integration")),
                              ("missing_e2e", lambda proof: proof.pop("e2e")),
                              ("wrong_repository", lambda proof: proof.update(repository="open-hax/knoxx"))]:
            with self.subTest(label=label):
                proof = evidence()
                mutate(proof)
                with self.assertRaises(q.Refusal):
                    self.admit(proof)

    def test_unrelated_policy_denial_is_not_admitted(self):
        with patch.object(q, "production_admission", return_value={"admitted": False, "reason": "unrelated gate refused"}):
            with self.assertRaisesRegex(q.Refusal, "unrelated gate refused"):
                self.admit(evidence())

    def test_custody_failures_still_refuse(self):
        proof = evidence()
        with self.assertRaisesRegex(q.Refusal, "native staging artifact custody mismatch"):
            self.admit(proof, stage=patch.object(
                q, "verify_native_stage", side_effect=q.Refusal("native staging artifact custody mismatch")))
        with self.assertRaisesRegex(q.Refusal, "archive differs from native size"):
            self.admit(proof, stage=patch.object(q, "verify_native_stage", return_value={"size_in_bytes": 99}))
        with self.assertRaisesRegex(q.Refusal, "OCI image/source reference mismatch"):
            self.admit(proof, archive_check=patch.object(
                q, "verify_archive", side_effect=q.Refusal("OCI image/source reference mismatch")))
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "dummy.zip"
            archive.write_bytes(b"x")

            def native_file(repository, path, revision, *args):
                if path == q.PLAN_PATH:
                    return PLAN
                raise q.Refusal("qualification file missing")

            with patch.object(q, "main_ancestor"), patch.object(q, "native_file", side_effect=native_file), \
                 patch.object(q, "verify_native_stage", return_value={"size_in_bytes": 1}), \
                 patch.object(q, "verify_archive"):
                with self.assertRaisesRegex(q.Refusal, "immutable source qualification unavailable"):
                    q.admit(PLAN, CONTROLLER, archive)


if __name__ == "__main__":
    unittest.main()
