"""Exercise the exact scope and evidence that a mutation override must preserve."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_dark_admission as work


class WorkDarkAdmissionTest(unittest.TestCase):
    """Changing nonmutation evidence or scope must fail before host transport."""

    def setUp(self):
        """Load the reviewed release shape for targeted counterexamples."""
        self.plan = work.strict_json((Path(__file__).resolve().parents[2] / work.PLAN).read_bytes())

    def test_only_exact_dark_release_can_use_the_decision(self):
        """Reject substituted artifact, source, receiver and public exposure."""
        work.verify_decision(self.plan)
        for field, value in (
            ("artifact_id", 13731817266), ("source_sha", "a" * 40),
            ("stage_deploy_job_id", 129604052832), ("exposure", "public"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(work.Refusal, "dark Work release"):
                work.verify_decision({**self.plan, field: value})

    def test_preserves_integration_and_e2e(self):
        """A failed, stale or timed-out ordinary receipt is not covered by the override."""
        for kind in ("integration", "e2e"):
            for field, value in (("status", "failed"), ("source_sha", "b" * 40), ("timed_out", True)):
                plan = deepcopy(self.plan)
                plan[kind][field] = value
                with self.subTest(kind=kind, field=field), self.assertRaisesRegex(work.Refusal, f"{kind} evidence"):
                    work.verify_decision(plan)

    def test_does_not_claim_campaign_passed(self):
        """A successful mutation campaign must never be synthesized."""
        for field, value in (("mutation_campaign_passed", True), ("qualified_batches", 4)):
            plan = deepcopy(self.plan)
            plan["owner_decision"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(work.Refusal, "negative campaign"):
                work.verify_decision(plan)

    def test_refuses_unaccepted_plan_before_archive_access(self):
        """Identical-looking local input has no authority before Services merge."""
        with (patch.object(work, "main_ancestor"), patch.object(work, "native_file", return_value=b"{}"),
              patch.object(work, "verify_archive") as archive):
            with self.assertRaisesRegex(work.Refusal, "accepted Services controller"):
                work.admit((Path(__file__).resolve().parents[2] / work.PLAN).read_bytes(),
                           "a" * 40, Path("does-not-exist"))
            archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
