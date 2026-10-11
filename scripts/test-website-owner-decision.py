#!/usr/bin/env python3
"""Exercise exact-candidate promotion without granting real approval."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("candidate", ROOT / "scripts/website-owner-decision.py")
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


class CandidateDecisionTests(unittest.TestCase):
    """Synthetic approvals test admission; the committed record stays pending."""

    def setUp(self):
        self.record = candidate.read_record("6" * 40, directory=ROOT / "test/fixtures/website-decisions")
        self.receipt = {"service": "website", "phase": "staging", "health": "success",
                        "sourceSha": "6" * 40, "image": self.record["image"],
                        "servicesSha": "7" * 40, "nginxSha": "8" * 64,
                        "composeProject": "website-staging", "publicHost": "staging.website.promethean.rest"}

    def test_exact_approved_candidate_and_fresh_stage_are_admitted(self):
        result = candidate.qualify(self.record, "6" * 40, self.record["image"], "7" * 40,
                                   "8" * 64, self.receipt)
        self.assertEqual("owner-decision", result["qualification"])
        self.assertFalse(result["mutationCampaignPassed"])

    def test_pending_or_missing_owner_decision_is_denied(self):
        for state in ("pending", "rejected", "", None):
            with self.subTest(state=state):
                record = copy.deepcopy(self.record)
                record["ownerDecision"]["status"] = state
                with self.assertRaises(ValueError):
                    candidate.qualify(record, "6" * 40, record["image"], "7" * 40, "8" * 64, self.receipt)

    def test_source_digest_or_serving_configuration_drift_is_denied(self):
        for field, value in (("sourceSha", "9" * 40), ("image", self.record["image"].replace("a" * 64, "b" * 64)),
                             ("nginxSha", "9" * 64)):
            with self.subTest(field=field):
                receipt = dict(self.receipt, **{field: value})
                with self.assertRaises(ValueError):
                    candidate.qualify(self.record, "6" * 40, self.record["image"], "7" * 40, "8" * 64, receipt)

    def test_wrong_phase_health_services_source_alias_or_hostname_is_denied(self):
        for field, value in (("phase", "production"), ("health", "starting"), ("servicesSha", "9" * 40),
                             ("composeProject", "website"), ("publicHost", "open-hax.promethean.rest")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    candidate.qualify(self.record, "6" * 40, self.record["image"], "7" * 40, "8" * 64,
                                      dict(self.receipt, **{field: value}))

    def test_unapproved_image_rebuild_or_mutable_source_is_denied(self):
        for source, image in (("main", self.record["image"]),
                              ("6" * 40, self.record["image"].replace("a" * 64, "b" * 64))):
            with self.subTest(source=source, image=image), self.assertRaises(ValueError):
                candidate.select(self.record, source, image)

    def test_committed_real_candidate_preserves_exact_scope_and_decision_evidence(self):
        record = candidate.read_record("6fd48ac5df9a16e414efbbf2b6abe98ad331be78")
        self.assertEqual("6fd48ac5df9a16e414efbbf2b6abe98ad331be78", record["sourceSha"])
        self.assertEqual("ghcr.io/open-hax/website:" + record["sourceSha"] +
                         "@sha256:ef3f2daa46b212cfcd82c2a72492946b0c44c23f1fab9b0fffde35d7199c836f", record["image"])
        self.assertIn(record["ownerDecision"]["status"], ("pending", "approved", "rejected"))
        if record["ownerDecision"]["status"] == "approved":
            self.assertEqual(record["image"], candidate.select(record, record["sourceSha"], record["image"])["image"])
        else:
            with self.assertRaises(ValueError):
                candidate.select(record, record["sourceSha"], record["image"])


if __name__ == "__main__":
    unittest.main()
