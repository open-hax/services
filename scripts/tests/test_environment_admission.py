"""Exercise the actual native collector with complete, local GitHub responses."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("github_environment_admission", SCRIPTS / "github_environment_admission.py")
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)
NOW = datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
OWNER = {"login": "owner", "id": 101, "type": "User"}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class NativeFixture:
    def __init__(self):
        self.pr = {"number": 1, "state": "open", "base": {"ref": "main", "sha": "b" * 40},
                   "head": {"sha": HEAD}, "user": OWNER, "labels": [{"name": "testing"}]}
        self.label = {"id": 201, "event": "labeled", "actor": OWNER,
                      "label": {"name": "testing"}, "created_at": (NOW - timedelta(seconds=5)).isoformat()}
        self.review = {"id": 301, "user": OWNER, "state": "COMMENTED", "commit_id": HEAD,
                       "body": f"Authorize testing deployment of {HEAD}.",
                       "submitted_at": (NOW - timedelta(seconds=10)).isoformat()}
        self.rules = "* @owner\n"
        self.files = [{"filename": "docs/readme.md"}]
        self.head_after = None
        self.pr_reads = 0
        self.fail_reviews = False
        self.calls = []
        self.label_after = None
        self.label_reads = 0
        self.others = []
        self.others_after = None
        self.other_labels = {}
        self.other_labels_after = {}
        self.claim_reads = 0
        self.fail_final_claims = False
        self.fail_final_timeline = False

    def api(self, path):
        self.calls.append(path)
        if path == "repos/open-hax/knoxx/pulls/1":
            self.pr_reads += 1
            result = copy.deepcopy(self.pr)
            if self.head_after and self.pr_reads > 1:
                result["head"]["sha"] = self.head_after
            return result
        if path.startswith("repos/open-hax/knoxx/pulls/1/reviews?"):
            if self.fail_reviews:
                raise RuntimeError("review evidence unavailable")
            return [copy.deepcopy(self.review)]
        if path.startswith("repos/open-hax/knoxx/issues/1/timeline?"):
            self.label_reads += 1
            if self.fail_final_timeline and self.label_reads > 1:
                raise RuntimeError("final timeline unavailable")
            label = self.label_after if self.label_reads > 1 and self.label_after is not None else self.label
            return [copy.deepcopy(label)]
        if path.startswith("repos/open-hax/knoxx/issues/") and "/timeline?" in path:
            number = int(path.split("/")[4])
            if self.fail_final_timeline and self.claim_reads > 1:
                raise RuntimeError("final timeline unavailable")
            label = (self.other_labels_after.get(number, self.other_labels[number])
                     if self.claim_reads > 1 else self.other_labels[number])
            return [copy.deepcopy(label)]
        if path.startswith("repos/open-hax/knoxx/pulls/1/files?"):
            return copy.deepcopy(self.files)
        if path.startswith("repos/open-hax/knoxx/pulls?state=open"):
            self.claim_reads += 1
            if self.fail_final_claims and self.claim_reads > 1:
                raise RuntimeError("final competing claims unavailable")
            rows = self.others_after if self.claim_reads > 1 and self.others_after is not None else self.others
            return copy.deepcopy(rows)
        if path.startswith("repos/open-hax/knoxx/contents/.github/CODEOWNERS?ref="):
            return {"content": base64.b64encode(self.rules.encode()).decode()}
        raise AssertionError(f"Unexpected native request: {path}")

    def admit(self):
        with patch.object(admission, "api", self.api), patch.object(admission, "datetime", Clock):
            return admission.admit("open-hax/knoxx", 1, "testing")


class NativeTestingAdmissionTests(unittest.TestCase):
    def test_current_owner_author_can_explicitly_authorize_without_self_approval(self):
        fixture = NativeFixture()
        result = fixture.admit()
        self.assertTrue(result["admitted"], result)
        self.assertEqual(HEAD, result["source_sha"])
        self.assertEqual(301, result["testing_authorization"]["review_id"])
        self.assertEqual(HEAD, result["testing_authorization"]["head_sha"])
        self.assertEqual(101, result["testing_authorization"]["user_id"])
        self.assertEqual("COMMENTED", fixture.review["state"])
        self.assertGreaterEqual(fixture.pr_reads, 2)

    def test_old_owner_label_and_relabel_cannot_authorize_new_head(self):
        for relabel in (False, True):
            with self.subTest(relabel=relabel):
                fixture = NativeFixture()
                fixture.pr["head"]["sha"] = "c" * 40
                if relabel:
                    fixture.label["created_at"] = NOW.isoformat()
                self.assertFalse(fixture.admit()["admitted"])

    def test_wrong_actor_body_state_identity_or_commit_refuses(self):
        cases = [
            {"user": {**OWNER, "login": "other", "id": 102}},
            {"user": {**OWNER, "id": 102}},
            {"user": {**OWNER, "type": "Bot"}},
            {"body": f"> Authorize testing deployment of {HEAD}."},
            {"body": f"Authorize testing deployment of {HEAD}.\nextra"},
            {"body": f"Authorize testing deployment of {'b' * 40}."},
            {"state": "APPROVED"}, {"state": "PENDING"}, {"state": "DISMISSED"},
            {"id": None}, {"commit_id": "main"}, {"commit_id": "b" * 40},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                fixture = NativeFixture()
                fixture.review.update(changes)
                self.assertFalse(fixture.admit()["admitted"])

    def test_authorization_requires_native_submitted_before_label_chronology(self):
        for submitted in (None, "bad", (NOW + timedelta(seconds=1)).isoformat(),
                          (NOW - timedelta(seconds=1)).isoformat()):
            with self.subTest(submitted=submitted):
                fixture = NativeFixture()
                fixture.review["submitted_at"] = submitted
                self.assertFalse(fixture.admit()["admitted"])

    def test_owner_scope_and_head_drift_are_rechecked(self):
        fixture = NativeFixture()
        fixture.rules = "* @other\n"
        self.assertFalse(fixture.admit()["admitted"])
        fixture = NativeFixture()
        fixture.head_after = "c" * 40
        self.assertFalse(fixture.admit()["admitted"])

    def test_unavailable_review_evidence_never_admits(self):
        fixture = NativeFixture()
        fixture.fail_reviews = True
        with self.assertRaises(RuntimeError):
            fixture.admit()

    def competitor(self, fixture, number=2, age=7201):
        row = {"number": number, "state": "open", "labels": [{"name": "testing"}]}
        label = {**copy.deepcopy(fixture.label), "id": 200 + number,
                 "created_at": (NOW - timedelta(seconds=age)).isoformat()}
        fixture.other_labels[number] = label
        return row

    def test_new_competing_claim_during_collection_refuses(self):
        fixture = NativeFixture()
        fixture.others_after = [self.competitor(fixture, age=1)]
        self.assertFalse(fixture.admit()["admitted"])

    def test_competing_relabel_or_same_second_replacement_refuses(self):
        for same_second in (False, True):
            with self.subTest(same_second=same_second):
                fixture = NativeFixture()
                fixture.others = [self.competitor(fixture)]
                fixture.other_labels_after[2] = {**fixture.other_labels[2], "id": 902}
                if not same_second:
                    fixture.other_labels_after[2]["created_at"] = NOW.isoformat()
                self.assertFalse(fixture.admit()["admitted"])

    def test_candidate_selected_label_or_actor_drift_refuses(self):
        for changes in ({"id": 901}, {"created_at": NOW.isoformat()},
                        {"actor": {**OWNER, "id": 102, "login": "other"}}):
            with self.subTest(changes=changes):
                fixture = NativeFixture()
                fixture.label_after = {**copy.deepcopy(fixture.label), **changes}
                self.assertFalse(fixture.admit()["admitted"])

    def test_unchanged_expired_claims_and_reordered_rows_stay_admitted(self):
        fixture = NativeFixture()
        fixture.others = [self.competitor(fixture, number=2), self.competitor(fixture, number=3)]
        fixture.others_after = list(reversed(fixture.others))
        self.assertTrue(fixture.admit()["admitted"])

    def test_initial_fresh_competitor_keeps_its_existing_refusal(self):
        fixture = NativeFixture()
        fixture.others = [self.competitor(fixture, age=1)]
        result = fixture.admit()
        self.assertFalse(result["admitted"])
        self.assertEqual([2], result["blocking_prs"])

    def test_unavailable_final_listing_or_timeline_never_admits(self):
        for failure in ("claims", "competitor-timeline", "candidate-timeline"):
            with self.subTest(failure=failure):
                fixture = NativeFixture()
                if failure != "candidate-timeline":
                    fixture.others = [self.competitor(fixture)]
                fixture.fail_final_claims = failure == "claims"
                fixture.fail_final_timeline = failure != "claims"
                with self.assertRaises(RuntimeError):
                    fixture.admit()

    def test_missing_or_malformed_selected_event_evidence_refuses(self):
        for candidate in (False, True):
            for changes in ({"id": None}, {"id": True}, {"id": "201"},
                            {"created_at": None}, {"created_at": "bad"}):
                with self.subTest(candidate=candidate, changes=changes):
                    fixture = NativeFixture()
                    if candidate:
                        fixture.label.update(changes)
                    else:
                        fixture.others = [self.competitor(fixture)]
                        fixture.other_labels[2].update(changes)
                    self.assertFalse(fixture.admit()["admitted"])


class NativeCodeownersTests(unittest.TestCase):
    def owns(self, pattern, path, login="owner", prefix=""):
        fixture = NativeFixture()
        fixture.rules = prefix + pattern + " @owner\n"
        with patch.object(admission, "api", fixture.api):
            return admission.owns_files("open-hax/knoxx", login, [path])

    def test_star_leaf_does_not_authorize_descendant(self):
        self.assertTrue(self.owns("docs/*", "docs/a.md"))
        self.assertFalse(self.owns("docs/*", "docs/a/b.md"))

    def test_trailing_double_star_matches_direct_and_deep_files(self):
        for name in ("docs/a.md", "docs/a/b.md"):
            self.assertTrue(self.owns("docs/**", name), name)

    def test_last_matching_secure_rule_cannot_fall_back_to_broad_owner(self):
        fixture = NativeFixture()
        fixture.rules = "* @owner\n/secure/** @security\n"
        with patch.object(admission, "api", fixture.api):
            self.assertFalse(admission.owns_files("open-hax/knoxx", "owner", ["secure/x"]))
            self.assertTrue(admission.owns_files("open-hax/knoxx", "security", ["secure/x"]))

    def test_anchoring_directory_middle_double_star_and_literals(self):
        for pattern, name, expected in [("/docs/*", "nested/docs/a", False),
                                        ("*.md", "nested/a.md", True),
                                        ("/a.md", "nested/a.md", False),
                                        ("docs/", "docs/a/b", True),
                                        ("a/**/b", "a/b", True),
                                        ("a/**/b", "a/x/y/b", True),
                                        ("a+(b).md", "a+(b).md", True),
                                        ("a+(b).md", "ab.md", False)]:
            with self.subTest(pattern=pattern, name=name):
                self.assertEqual(expected, self.owns(pattern, name))

    def test_unsupported_patterns_fail_closed(self):
        for pattern in ("!docs", "docs/[ab]", "docs\\ name"):
            self.assertFalse(self.owns(pattern, "docs/a"))


if __name__ == "__main__":
    unittest.main()
