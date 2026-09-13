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
                     labeler_is_code_owner=True, head_sha=SHA, labeled_at=NOW.isoformat()), **overrides)

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
    def test_two_hours_exactly_is_still_held(self):
        other = candidate(number=2, labeled_at=(NOW-timedelta(hours=2)).isoformat())
        self.assertFalse(p.testing_admission(candidate(), [other], now=NOW)["admitted"])
        other["labeled_at"]=(NOW-timedelta(hours=2, seconds=1)).isoformat()
        self.assertTrue(p.testing_admission(candidate(), [other], now=NOW)["admitted"])
    def test_any_open_testing_claim_blocks_and_relabel_resets(self):
        other=candidate(number=2, base="other", labeled_at=NOW.isoformat())
        self.assertEqual(p.testing_admission(candidate(), [other], now=NOW)["blocking_prs"], [2])
        other["state"]="closed"
        self.assertTrue(p.testing_admission(candidate(), [other], now=NOW)["admitted"])
    def test_missing_or_future_timeline_evidence_fails_closed(self):
        for time in ["", "not-a-date", (NOW+timedelta(seconds=1)).isoformat()]:
            self.assertFalse(p.testing_admission(candidate(labeled_at=time), [], now=NOW)["admitted"])
        self.assertFalse(p.testing_admission(candidate(), [candidate(number=2, labeled_at="")], now=NOW)["admitted"])
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

if __name__ == "__main__": unittest.main()
