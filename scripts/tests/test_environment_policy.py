import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timezone, timedelta

spec = importlib.util.spec_from_file_location("environment_policy", Path(__file__).resolve().parents[1] / "environment_policy.py")
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
NOW = datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc)
SHA = "a" * 40

def candidate(**overrides):
    return dict(dict(number=1, state="open", base="main", testing_label=True,
                     labeler_is_code_owner=True, head_sha=SHA, authorization_sha=SHA,
                     labeled_at=NOW.isoformat()), **overrides)

def batches():
    return [dict(source_sha=SHA, status="passed", baseline="passed", batch_index=b,
                 mutants=[dict(fingerprint=f"source-{b}-{i}", status="killed", phase="test") for i in range(250)]) for b in range(4)]

class EnvironmentPolicyTest(unittest.TestCase):
    def test_empty_slot_and_full_hostnames(self):
        self.assertTrue(p.testing_admission(candidate(), [], now=NOW)["admitted"])
        self.assertEqual(p.hostname("yoga", "axxium"), "yoga.axxium.promethean.rest")
        with self.assertRaises(ValueError): p.hostname("production", "../../shell")
    def test_code_owner_main_and_current_label_required(self):
        for changes in [dict(labeler_is_code_owner=False), dict(base="staging"), dict(state="closed"),
                        dict(testing_label=False), dict(head_sha="main")]:
            self.assertFalse(p.testing_admission(candidate(**changes), [], now=NOW)["admitted"])

    def test_immutable_owner_authorization_is_required_for_current_head(self):
        for authorization in (None, "", "main", "b"*40):
            self.assertFalse(p.testing_admission(candidate(authorization_sha=authorization), [], now=NOW)["admitted"])
        self.assertFalse(p.testing_admission(candidate(head_sha="b"*40), [], now=NOW)["admitted"])
    def test_two_hours_exactly_is_still_held(self):
        other = candidate(number=2, labeled_at=(NOW-timedelta(hours=2)).isoformat())
        self.assertFalse(p.testing_admission(candidate(), [other], now=NOW)["admitted"])
        other["labeled_at"]=(NOW-timedelta(hours=2, seconds=1)).isoformat()
        self.assertTrue(p.testing_admission(candidate(), [other], now=NOW)["admitted"])
    def test_candidate_and_competitor_share_the_closed_two_hour_boundary(self):
        for seconds, active in ((7199.999999, True), (7200, True), (7200.000001, False)):
            with self.subTest(seconds=seconds):
                claim = candidate(labeled_at=(NOW-timedelta(seconds=seconds)).isoformat())
                self.assertEqual(active, p.testing_admission(claim, [], now=NOW)["admitted"])
                self.assertEqual(not active, p.testing_admission(candidate(),
                    [{**claim, "number": 2}], now=NOW)["admitted"])
    def test_expired_candidate_cannot_reclaim_an_empty_or_expired_slot(self):
        old = candidate(labeled_at=(NOW-timedelta(hours=2, seconds=1)).isoformat())
        for others in ([], [{**old, "number": 2}]):
            with self.subTest(others=others):
                self.assertFalse(p.testing_admission(old, others, now=NOW)["admitted"])
        self.assertTrue(p.testing_admission(candidate(), [{**old, "number": 2}], now=NOW)["admitted"])
    def test_any_open_testing_claim_blocks_and_relabel_resets(self):
        other=candidate(number=2, base="other", labeled_at=NOW.isoformat())
        self.assertEqual(p.testing_admission(candidate(), [other], now=NOW)["blocking_prs"], [2])
        other["state"]="closed"
        self.assertTrue(p.testing_admission(candidate(), [other], now=NOW)["admitted"])
    def test_missing_or_future_timeline_evidence_fails_closed(self):
        for time in ["", "not-a-date", (NOW+timedelta(seconds=1)).isoformat()]:
            self.assertFalse(p.testing_admission(candidate(labeled_at=time), [], now=NOW)["admitted"])
        self.assertFalse(p.testing_admission(candidate(), [candidate(number=2, labeled_at="")], now=NOW)["admitted"])
    def test_later_claim_does_not_revoke_the_unexpired_lease_on_recheck(self):
        original = candidate(labeled_at=(NOW-timedelta(minutes=20)).isoformat())
        later = candidate(number=2, labeled_at=(NOW-timedelta(minutes=10)).isoformat())
        self.assertTrue(p.testing_admission(original, [], now=NOW-timedelta(minutes=20))["admitted"])
        self.assertTrue(p.testing_admission(original, [later], now=NOW)["admitted"])
        self.assertEqual(p.testing_admission(later, [original], now=NOW)["blocking_prs"], [1])

    def test_relabeling_moves_a_claim_behind_the_existing_lease(self):
        original = candidate(labeled_at=(NOW-timedelta(minutes=20)).isoformat())
        other = candidate(number=2, labeled_at=(NOW-timedelta(minutes=10)).isoformat())
        relabeled = {**original, "labeled_at": NOW.isoformat()}
        self.assertTrue(p.testing_admission(other, [relabeled], now=NOW)["admitted"])
        self.assertEqual(p.testing_admission(relabeled, [other], now=NOW)["blocking_prs"], [2])

    def test_earlier_lease_never_skips_invalid_or_future_competing_evidence(self):
        original = candidate(labeled_at=(NOW-timedelta(minutes=20)).isoformat())
        values = [None, 0, "", "not-a-date", "2026-09-13T00:50:00",
                  (NOW+timedelta(microseconds=1)).isoformat(),
                  (NOW+timedelta(hours=3)).isoformat()]
        for value in values:
            with self.subTest(value=value):
                other = candidate(number=2, labeled_at=value)
                self.assertFalse(p.testing_admission(original, [other], now=NOW)["admitted"])
        other = candidate(number=2)
        del other["labeled_at"]
        self.assertFalse(p.testing_admission(original, [other], now=NOW)["admitted"])

    def test_simultaneous_claims_refuse_both_candidates(self):
        first = candidate(labeled_at=(NOW-timedelta(minutes=20)).isoformat())
        second = {**first, "number": 2}
        self.assertEqual(p.testing_admission(first, [second], now=NOW)["blocking_prs"], [2])
        self.assertEqual(p.testing_admission(second, [first], now=NOW)["blocking_prs"], [1])
    def test_main_merge_targets_staging_only(self):
        self.assertEqual(p.staging_admission(dict(merged=True, base="main", merge_sha=SHA))["environment"],"staging")
        self.assertFalse(p.staging_admission(dict(merged=False, base="main", merge_sha=SHA))["admitted"])
    def test_production_requires_all_real_evidence(self):
        evidence=dict(source_sha=SHA,status="passed",assertions=1)
        self.assertTrue(p.production_admission(SHA,evidence,evidence,batches())["admitted"])
        self.assertFalse(p.production_admission(SHA,{**evidence,"source_sha":"b"*40},evidence,batches())["admitted"])
        for mutate in [lambda x:x.pop(), lambda x:x[0]["mutants"].pop(),
                       lambda x:x[0]["mutants"][0].update(status="survived"),
                       lambda x:x[0]["mutants"][0].update(phase="compile"),
                       lambda x:x[0].update(baseline="failed"),
                       lambda x:x[1]["mutants"][0].update(fingerprint="source-0-0"),
                       lambda x:x[1].update(batch_index=0)]:
            value=batches();mutate(value)
            self.assertFalse(p.production_admission(SHA,evidence,evidence,value)["admitted"])

    def test_production_refuses_malformed_receipts_and_boolean_counts(self):
        proof = dict(source_sha=SHA, status="passed", assertions=1)
        for value in (None, True, [], {}, "passed"):
            self.assertFalse(p.production_admission(SHA, value, proof, batches())["admitted"])
        for value in (None, True, "1", 1.5, 0):
            self.assertFalse(p.production_admission(SHA, {**proof, "assertions": value}, proof, batches())["admitted"])
        for value in (None, True, {}, "batches"):
            self.assertFalse(p.production_admission(SHA, proof, proof, value)["admitted"])
        for field, value in (("batch_index", True), ("mutants", None)):
            rows = batches(); rows[0][field] = value
            self.assertFalse(p.production_admission(SHA, proof, proof, rows)["admitted"])
        for value in (None, True, " ", [], {}):
            rows = batches(); rows[0]["mutants"][0]["fingerprint"] = value
            self.assertFalse(p.production_admission(SHA, proof, proof, rows)["admitted"])
        rows = batches(); rows[0]["mutants"][0] = None
        self.assertFalse(p.production_admission(SHA, proof, proof, rows)["admitted"])

    def test_production_accepts_explicit_clean_optional_markers_at_every_level(self):
        evidence=dict(source_sha=SHA,status="passed",assertions=1,timed_out=False,warnings=[])
        values=batches()
        for batch in values:
            batch.update(timed_out=False,warnings=[])
            for mutant in batch["mutants"]:
                mutant.update(timed_out=False,warnings=[])
        self.assertTrue(p.production_admission(SHA,evidence,evidence,values)["admitted"])

    def test_production_rejects_timeout_warning_and_malformed_markers_at_every_level(self):
        invalid = [("timed_out", value) for value in (True, 0, 1, None, "false", "", [], {})]
        invalid += [("warnings", value) for value in (["compiler warning"], 0, 1, None, "", False, {})]
        for level in ("integration", "e2e", "batch", "mutant"):
            for field, value in invalid:
                with self.subTest(level=level, field=field, value=value):
                    integration=dict(source_sha=SHA,status="passed",assertions=1)
                    e2e=dict(integration)
                    values=batches()
                    receipt={"integration": integration, "e2e": e2e,
                             "batch": values[0], "mutant": values[0]["mutants"][0]}[level]
                    receipt[field]=value
                    self.assertFalse(p.production_admission(SHA,integration,e2e,values)["admitted"])

if __name__ == "__main__": unittest.main()
