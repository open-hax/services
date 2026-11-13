"""Check native custody and the mutation-only decision for Work reopen."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_reopen_admission as work

ROOT = Path(__file__).resolve().parents[2]


class WorkReopenAdmissionTests(unittest.TestCase):
    def test_decision_is_limited_to_this_pilot_and_preserves_failure(self):
        plan = work.strict_json((ROOT / "digitalocean/services/axxium-work-pilot/activation-release-plan.json").read_bytes())
        plan.update(source_sha=work.SOURCE, source_pr=work.SOURCE_PR, lane=work.LANE,
                    stage_run_id=work.STAGE_RUN, stage_controller_sha=work.STAGE_CONTROLLER,
                    stage_deploy_job_id=work.STAGE_DEPLOY, artifact_id=work.ARTIFACT,
                    post_merge_ci_run_id=work.CI_RUN, post_merge_codeql_run_id=work.CODEQL_RUN)
        plan["owner_decision"]["source_sha"] = work.SOURCE
        plan["owner_decision"]["artifact_id"] = work.ARTIFACT
        plan["integration"]["source_sha"] = work.SOURCE
        plan["e2e"]["source_sha"] = work.SOURCE
        plan["local_rehearsal"] = {"status": "passed", "log_sha256": "a" * 64, "scope": "fixture"}
        work.verify_decision(plan)
        for section, key, value in (
            (None, "source_sha", "a" * 40),
            (None, "stage_run_id", work.STAGE_RUN + 1),
            (None, "stage_deploy_job_id", work.STAGE_DEPLOY + 1),
            (None, "artifact_id", work.ARTIFACT + 1),
            (None, "post_merge_ci_run_id", work.CI_RUN + 1),
            (None, "exposure", "public"),
            ("owner_decision", "artifact_id", 1),
            ("owner_decision", "mutation_campaign_passed", True),
            ("historical_campaign", "qualified_batches", 4),
            ("integration", "status", "failed"),
            ("e2e", "assertions", 0),
            ("local_rehearsal", "status", "failed"),
        ):
            with self.subTest(section=section, key=key):
                changed = deepcopy(plan)
                (changed[section] if section else changed)[key] = value
                with self.assertRaises(work.Refusal):
                    work.verify_decision(changed)

    def test_promotion_requires_the_reviewed_merge_parents_and_tree(self):
        repo = {"full_name": work.REPOSITORY}
        pr = {"number": work.SOURCE_PR, "merged": True, "state": "closed",
              "head": {"sha": work.PR_HEAD, "repo": repo}, "base": {"ref": "main", "repo": repo},
              "merge_commit_sha": work.SOURCE}
        commit = {"sha": work.SOURCE, "parents": [{"sha": work.PR_BASE}, {"sha": work.PR_HEAD}],
                  "commit": {"tree": {"sha": work.TESTED_TREE}}}
        with patch.object(work, "api", side_effect=[pr, commit]):
            work.verify_promotion()
        with patch.object(work, "api", side_effect=[pr, {**commit, "parents": list(reversed(commit["parents"]))}]):
            with self.assertRaisesRegex(work.Refusal, "reviewed candidate tree"):
                work.verify_promotion()
        with patch.object(work, "api", side_effect=[pr, {**commit, "commit": {"tree": {"sha": "a" * 40}}}]):
            with self.assertRaisesRegex(work.Refusal, "reviewed candidate tree"):
                work.verify_promotion()

    def test_source_run_requires_every_job_on_the_successful_native_attempt(self):
        path = ".github/workflows/ci.yml"
        repo = {"full_name": work.REPOSITORY}
        run = {"id": work.CI_RUN, "run_attempt": 2, "event": "push", "path": path,
               "repository": repo, "head_repository": repo, "head_branch": "main", "head_sha": work.SOURCE,
               "status": "completed", "conclusion": "success"}
        jobs = {"total_count": 2, "jobs": [
            {"name": name, "run_id": work.CI_RUN, "run_attempt": 2, "head_sha": work.SOURCE,
             "status": "completed", "conclusion": "success"}
            for name in ("build", "deployment-boundary")
        ]}
        expected = {"build", "deployment-boundary"}
        with patch.object(work, "api", side_effect=[run, jobs]):
            work.verify_source_run(work.CI_RUN, path, "push", expected)
        for target, field, value in (
            ("run", "conclusion", "failure"), ("run", "head_sha", work.PR_HEAD),
            ("job", "run_attempt", 1), ("job", "conclusion", "skipped"), ("job", "name", "deploy"),
        ):
            with self.subTest(target=target, field=field):
                changed_run, changed_jobs = deepcopy(run), deepcopy(jobs)
                (changed_run if target == "run" else changed_jobs["jobs"][0])[field] = value
                with patch.object(work, "api", side_effect=[changed_run, changed_jobs]):
                    with self.assertRaises(work.Refusal):
                        work.verify_source_run(work.CI_RUN, path, "push", expected)

    def test_staging_receiver_must_match_native_attempt_and_public_proof(self):
        job = {"id": work.STAGE_DEPLOY, "run_id": work.STAGE_RUN, "run_attempt": 1, "head_sha": work.PR_HEAD,
               "name": "staging / deploy", "status": "completed", "conclusion": "success",
               "steps": [{"name": "Verify public HTTPS and readiness", "conclusion": "success"}]}
        campaign = {"id": work.CAMPAIGN, "run_attempt": 1, "status": "completed",
                    "conclusion": "failure", "path": ".github/workflows/qualification-work-campaign.yml"}
        with patch.object(work, "api", side_effect=[job, {"run_attempt": 1}, campaign]):
            work.verify_native_jobs()
        for changed_job, attempt in ((job, 2), ({**job, "steps": []}, 1)):
            with self.subTest(attempt=attempt), patch.object(work, "api", side_effect=[changed_job, {"run_attempt": attempt}]):
                with self.assertRaisesRegex(work.Refusal, "stage receiver evidence"):
                    work.verify_native_jobs()

    def test_unaccepted_plan_refuses_before_remote_promotion_or_archive(self):
        plan = (ROOT / "digitalocean/services/axxium-work-pilot/activation-release-plan.json").read_bytes()
        with (patch.object(work, "main_ancestor"), patch.object(work, "native_file", return_value=b"{}"),
              patch.object(work, "verify_promotion") as promotion, patch.object(work, "verify_archive") as archive):
            with self.assertRaisesRegex(work.Refusal, "accepted Services controller"):
                work.admit(plan, "a" * 40, Path("does-not-exist"))
            promotion.assert_not_called()
            archive.assert_not_called()
