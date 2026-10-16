"""Exercise the actual native collector with complete, local GitHub responses."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

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
                   "head": {"sha": HEAD}, "user": OWNER, "labels": [{"name": "testing"}],
                   "changed_files": 1}
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
        self.user_permission = "write"
        self.team_membership = {"state": "active"}
        self.team_permissions = {"permissions": {"push": True}}
        self.permission_failure = None
        self.accepts = []

    def api(self, path, *, accept="application/vnd.github+json"):
        self.calls.append(path)
        self.accepts.append((path, accept))
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
            query = parse_qs(urlsplit(path).query)
            start = (int(query["page"][0])-1)*int(query["per_page"][0])
            return copy.deepcopy(self.files[start:start+int(query["per_page"][0])])
        if path.startswith("repos/open-hax/knoxx/pulls?state=open"):
            self.claim_reads += 1
            if self.fail_final_claims and self.claim_reads > 1:
                raise RuntimeError("final competing claims unavailable")
            rows = self.others_after if self.claim_reads > 1 and self.others_after is not None else self.others
            return copy.deepcopy(rows)
        if path.startswith("repos/open-hax/knoxx/contents/.github/CODEOWNERS?ref="):
            return {"content": base64.b64encode(self.rules.encode()).decode()}
        if path.startswith("repos/open-hax/knoxx/collaborators/") and path.endswith("/permission"):
            if self.permission_failure:
                raise self.permission_failure
            return {"permission": self.user_permission}
        if path == "orgs/example/teams/writers/memberships/owner":
            return copy.deepcopy(self.team_membership)
        if path == "orgs/example/teams/writers/repos/open-hax/knoxx":
            if self.permission_failure:
                raise self.permission_failure
            return copy.deepcopy(self.team_permissions)
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

    def test_initial_earlier_fresh_competitor_keeps_its_existing_refusal(self):
        fixture = NativeFixture()
        fixture.others = [self.competitor(fixture, age=10)]
        result = fixture.admit()
        self.assertFalse(result["admitted"])
        self.assertEqual([2], result["blocking_prs"])

    def test_stable_later_competing_claim_does_not_revoke_the_existing_lease(self):
        fixture = NativeFixture()
        fixture.others = [self.competitor(fixture, age=1)]
        self.assertTrue(fixture.admit()["admitted"])

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

    def test_expired_candidate_refuses(self):
        fixture = NativeFixture()
        fixture.label["created_at"] = (NOW-timedelta(hours=2, seconds=1)).isoformat()
        fixture.review["submitted_at"] = (NOW-timedelta(hours=2, seconds=2)).isoformat()
        self.assertFalse(fixture.admit()["admitted"])

    def test_expiry_during_native_collection_refuses(self):
        fixture = NativeFixture()
        fixture.label["created_at"] = (NOW-timedelta(hours=2)).isoformat()
        fixture.review["submitted_at"] = (NOW-timedelta(hours=2, seconds=1)).isoformat()
        class ExpiringClock(datetime):
            reads = 0
            @classmethod
            def now(cls, tz=None):
                cls.reads += 1
                return NOW if cls.reads == 1 else NOW+timedelta(microseconds=1)
        with patch.object(admission, "api", fixture.api), patch.object(admission, "datetime", ExpiringClock):
            self.assertFalse(admission.admit("open-hax/knoxx", 1, "testing")["admitted"])

    def test_truncated_3000_file_listing_refuses_before_ownership(self):
        fixture = NativeFixture()
        fixture.pr["changed_files"] = 3001
        fixture.files = [{"filename": f"docs/{index}.md"} for index in range(3000)]
        result = fixture.admit()
        self.assertFalse(result["admitted"], result)
        self.assertFalse(any("CODEOWNERS" in path for path in fixture.calls))
        self.assertEqual(31, sum("/files?" in path for path in fixture.calls))

    def test_changed_file_count_must_be_native_and_complete(self):
        for count in (None, True, "1", 0, 2):
            with self.subTest(count=count):
                fixture = NativeFixture()
                fixture.pr["changed_files"] = count
                self.assertFalse(fixture.admit()["admitted"])
        fixture = NativeFixture()
        fixture.pr.pop("changed_files")
        self.assertFalse(fixture.admit()["admitted"])

    def test_duplicate_file_rows_cannot_hide_an_unexamined_path(self):
        fixture = NativeFixture()
        fixture.pr["changed_files"] = 2
        fixture.files *= 2
        self.assertFalse(fixture.admit()["admitted"])

    def test_complete_multipage_and_rename_original_path_coverage_are_preserved(self):
        fixture = NativeFixture()
        fixture.files = [{"filename": f"docs/{index}.md"} for index in range(101)]
        fixture.pr["changed_files"] = len(fixture.files)
        self.assertTrue(fixture.admit()["admitted"])
        fixture = NativeFixture()
        fixture.files = [{"filename": "docs/new.md", "previous_filename": "secure/old.md"}]
        self.assertTrue(fixture.admit()["admitted"])
        fixture.rules += "/secure/** @security\n"
        self.assertFalse(fixture.admit()["admitted"])

    def test_current_direct_owner_write_permission_is_required(self):
        for permission in ("write", "admin", "read", "triage", "none", None, True, "custom-writer"):
            with self.subTest(permission=permission):
                fixture = NativeFixture()
                fixture.user_permission = permission
                self.assertEqual(permission in ("write", "admin"), fixture.admit()["admitted"])

    def test_named_team_needs_its_own_repository_write_permission_and_active_member(self):
        for permissions, active, expected in (({"push": True}, True, True),
                ({"push": False, "pull": True}, True, False), ({}, True, False),
                ({"push": "true"}, True, False), ({"push": True}, False, False)):
            with self.subTest(permissions=permissions, active=active):
                fixture = NativeFixture()
                fixture.rules = "* @example/writers\n"
                fixture.team_permissions = {"permissions": permissions}
                fixture.team_membership = {"state": "active" if active else "pending"}
                result = fixture.admit()
                self.assertEqual(expected, result["admitted"])
                if expected:
                    self.assertIn(("orgs/example/teams/writers/repos/open-hax/knoxx",
                        "application/vnd.github.v3.repository+json"), fixture.accepts)

    def test_missing_or_unavailable_repository_permission_never_admits(self):
        for team in (False, True):
            for status in (404, 403):
                with self.subTest(team=team, status=status):
                    fixture = NativeFixture()
                    if team:
                        fixture.rules = "* @example/writers\n"
                    fixture.permission_failure = HTTPError("synthetic", status, "synthetic", {}, None)
                    if status == 404:
                        self.assertFalse(fixture.admit()["admitted"])
                    else:
                        with self.assertRaises(HTTPError):
                            fixture.admit()


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
