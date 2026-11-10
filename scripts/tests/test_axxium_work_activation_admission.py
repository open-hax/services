"""Exercise the release boundary and preserved mutation exception for Work activation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_activation_admission as work

ROOT = Path(__file__).resolve().parents[2]


class WorkActivationAdmissionTest(unittest.TestCase):
    """Verify that transport depends on the exact reviewed scope and evidence."""

    def setUp(self):
        self.plan_bytes = (ROOT / work.PLAN).read_bytes()
        self.plan = work.strict_json(self.plan_bytes)
        self.receipt_bytes = (ROOT / work.REHEARSAL).read_bytes()

    def test_reviewed_decision_and_rehearsal(self):
        work.verify_decision(self.plan)
        work.verify_rehearsal(self.plan, self.receipt_bytes)

    def test_decision_rejects_substituted_release_or_scope(self):
        for field, value in (
            ("source_sha", "a" * 40),
            ("artifact_id", 13791652350),
            ("stage_deploy_job_id", 130041327019),
            ("origin", "https://axxium.promethean.rest"),
            ("exposure", "public"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(work.Refusal, "fenced Work pilot"):
                work.verify_decision({**self.plan, field: value})

    def test_mutation_decision_preserves_both_ordinary_evidence_gates(self):
        for gate in ("integration", "e2e"):
            for field, value in (("status", "failed"), ("source_sha", "a" * 40), ("timed_out", True)):
                plan = deepcopy(self.plan)
                plan[gate][field] = value
                with self.subTest(gate=gate, field=field), self.assertRaisesRegex(work.Refusal, f"{gate} evidence"):
                    work.verify_decision(plan)

    def test_failed_campaign_is_predecessor_evidence(self):
        plan = deepcopy(self.plan)
        plan["historical_campaign"]["source_sha"] = work.SOURCE
        with self.assertRaisesRegex(work.Refusal, "predecessor mutation results"):
            work.verify_decision(plan)
        plan = deepcopy(self.plan)
        plan["owner_decision"]["mutation_campaign_passed"] = True
        with self.assertRaisesRegex(work.Refusal, "mutation-only decision"):
            work.verify_decision(plan)

    def test_rehearsal_rejects_changed_image_or_receipt(self):
        with self.assertRaisesRegex(work.Refusal, "not bound"):
            work.verify_rehearsal({**self.plan, "image_config_digest": "sha256:" + "1" * 64}, self.receipt_bytes)
        with self.assertRaisesRegex(work.Refusal, "differs from reviewed receipt"):
            work.verify_rehearsal(self.plan, self.receipt_bytes + b"\n")

    def test_unaccepted_plan_denies_before_native_stage_or_archive(self):
        with (patch.object(work, "main_ancestor"), patch.object(work, "native_file", return_value=b"{}"),
              patch.object(work, "verify_native_stage") as stage, patch.object(work, "verify_archive") as archive):
            with self.assertRaisesRegex(work.Refusal, "accepted Services controller"):
                work.admit(self.plan_bytes, "a" * 40, Path("does-not-exist"))
            stage.assert_not_called()
            archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
