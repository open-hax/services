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
            return [copy.deepcopy(self.label)]
        if path.startswith("repos/open-hax/knoxx/pulls/1/files?"):
            return copy.deepcopy(self.files)
        if path.startswith("repos/open-hax/knoxx/pulls?state=open"):
            return []
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
