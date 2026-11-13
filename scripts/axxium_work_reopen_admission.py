#!/usr/bin/env python3
"""Admit the exact request-ID release for the private Axxium Work pilot."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from axxium_production_qualification import (
    REPOSITORY, SERVICES, Refusal, main_ancestor, native_file, require,
    strict_json, validate_plan, verify_archive, verify_native_stage,
)
from axxium_work_activation_admission import ORIGIN
from axxium_work_dark_admission import CAMPAIGN, POLICY, SOURCE as CAMPAIGN_SOURCE, verify_transport
from environment_policy import production_admission
from github_environment_admission import api

PLAN = "digitalocean/services/axxium-work-pilot/reopen-release-plan.json"
LANE = "reopen-work-pilot"
SOURCE = "68aa511626ab28ea3d93684c9c464b8c13edbb17"
SOURCE_PR = 59
PR_HEAD = "967649d064a7b7baf3226ffdd430591a496698f4"
PR_BASE = "806ac21ea2e5ca949197564a5cf797aed8daa23c"
TESTED_TREE = "19abcd3f68affd0dd8124410dfa3d80f5fd228ed"
STAGE_RUN = 42908355906
STAGE_CONTROLLER = "a2aee9a01a51ef1481dcc6d1071104f449dcf6bd"
STAGE_DEPLOY = 130154417897
ARTIFACT = 13805144939
CI_RUN = 42908353537
CODEQL_RUN = 42908353032


def verify_decision(plan):
    """Keep the historical mutation decision specific to the reviewed Work release."""
    require((plan["source_sha"], plan["source_pr"], plan["stage_run_id"], plan["stage_controller_sha"],
             plan.get("stage_deploy_job_id"), plan["artifact_id"],
             plan.get("post_merge_ci_run_id"), plan.get("post_merge_codeql_run_id"),
             plan.get("lane"), plan.get("exposure"), plan.get("origin"))
            == (SOURCE, SOURCE_PR, STAGE_RUN, STAGE_CONTROLLER, STAGE_DEPLOY, ARTIFACT, CI_RUN, CODEQL_RUN,
                LANE, "two-actor-work-pilot", ORIGIN),
            "owner decision applies only to the request-ID Work pilot release")
    decision = plan.get("owner_decision", {})
    revision = decision.get("desk_revision")
    require(type(revision) is int and revision > 0 and decision == {
        "scope": "mutation-only",
        "reference": "https://axxium-integration-desk.enso-err.chatgpt.site",
        "desk_revision": revision,
        "source_sha": SOURCE,
        "artifact_id": plan["artifact_id"],
        "artifact_sha256": plan["artifact_sha256"],
        "mutation_campaign_passed": False,
    } and decision["mutation_campaign_passed"] is False,
        "reviewed mutation-only decision missing")
    campaign = plan.get("historical_campaign", {})
    require(type(campaign.get("attempt")) is int and type(campaign.get("qualified_batches")) is int and campaign == {
        "source_sha": CAMPAIGN_SOURCE,
        "run_id": CAMPAIGN,
        "attempt": 1,
        "qualified_batches": 0,
        "terminal_partitions": 4,
        "eligible_over_observed": [[68, 157], [88, 218], [193, 489], [115, 260]],
    }, "historical mutation results differ from retained negative evidence")
    require(sha256(Path(__file__).with_name("environment_policy.py").read_bytes()).hexdigest() == POLICY,
            "shared production policy changed; review this Work decision again")
    verdict = production_admission(SOURCE, plan.get("integration"), plan.get("e2e"), [])
    require(verdict == {"admitted": False, "reason": "four completed mutation batches are required"},
            verdict.get("reason", "unexpected mutation waiver verdict"))
    rehearsal = plan.get("local_rehearsal", {})
    require(rehearsal.get("status") == "passed"
            and re.fullmatch(r"[0-9a-f]{64}", rehearsal.get("log_sha256", ""))
            and isinstance(rehearsal.get("scope"), str) and bool(rehearsal["scope"]),
            "reviewed exact-image rehearsal evidence missing")


def verify_promotion():
    """Require the native merge to retain the exact reviewed candidate tree."""
    pr = api(f"repos/{REPOSITORY}/pulls/{SOURCE_PR}")
    require(pr.get("number") == SOURCE_PR and pr.get("merged") is True and pr.get("state") == "closed"
            and pr.get("merge_commit_sha") == SOURCE and pr.get("head", {}).get("sha") == PR_HEAD
            and pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
            and pr.get("base", {}).get("ref") == "main"
            and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY,
            "native request-ID promotion differs from the reviewed source PR")
    commit = api(f"repos/{REPOSITORY}/commits/{SOURCE}")
    require(commit.get("sha") == SOURCE and [parent.get("sha") for parent in commit.get("parents", [])]
            == [PR_BASE, PR_HEAD] and commit.get("commit", {}).get("tree", {}).get("sha") == TESTED_TREE,
            "native merge does not match the reviewed candidate tree")


def successful_job(job, run_id, attempt, head):
    """Check native job custody before accepting its conclusion."""
    return (job.get("run_id") == run_id and job.get("run_attempt") == attempt and job.get("head_sha") == head
            and job.get("status") == "completed" and job.get("conclusion") == "success")


def verify_source_run(run_id, path, event, expected_jobs):
    """Require complete native main-branch CI or CodeQL on the merged source."""
    run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    attempt = run.get("run_attempt")
    require(run.get("id") == run_id and type(attempt) is int and attempt > 0 and run.get("path") == path
            and run.get("head_branch") == "main" and run.get("head_sha") == SOURCE and run.get("event") == event
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("status") == "completed" and run.get("conclusion") == "success",
            "current native main CI or CodeQL run is missing or unsuccessful")
    response = api(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    jobs = response.get("jobs", [])
    require(response.get("total_count") == len(jobs) and len(jobs) == len(expected_jobs)
            and {job.get("name") for job in jobs} == expected_jobs
            and all(successful_job(job, run_id, attempt, SOURCE) for job in jobs),
            "current native main CI or CodeQL jobs are incomplete or unsuccessful")


def verify_native_jobs():
    """Bind the receiver proof and the historical unsuccessful mutation campaign."""
    staged = api(f"repos/{REPOSITORY}/actions/jobs/{STAGE_DEPLOY}")
    current = api(f"repos/{REPOSITORY}/actions/runs/{STAGE_RUN}")
    attempt = current.get("run_attempt")
    require(type(attempt) is int and attempt > 0 and staged.get("id") == STAGE_DEPLOY
            and successful_job(staged, STAGE_RUN, attempt, PR_HEAD) and staged.get("name") == "staging / deploy"
            and any(step.get("name") == "Verify public HTTPS and readiness"
                    and step.get("conclusion") == "success" for step in staged.get("steps", [])),
            "successful native stage receiver evidence missing")
    campaign = api(f"repos/{REPOSITORY}/actions/runs/{CAMPAIGN}/attempts/1")
    require(campaign.get("id") == CAMPAIGN and campaign.get("run_attempt") == 1
            and campaign.get("path") == ".github/workflows/qualification-work-campaign.yml"
            and campaign.get("status") == "completed" and campaign.get("conclusion") == "failure",
            "native terminal mutation evidence missing")


def admit(plan_bytes, controller_sha, archive, target=None):
    """Read the immutable release, native runs and complete OCI custody before transport."""
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN, controller_sha, 65536) == plan_bytes,
            "release plan differs from accepted Services controller")
    verify_decision(plan)
    verify_promotion()
    verify_source_run(CI_RUN, ".github/workflows/ci.yml", "push", {"build", "deployment-boundary"})
    verify_source_run(CODEQL_RUN, "dynamic/github-code-scanning/codeql", "dynamic",
                      {"Analyze (actions)", "Analyze (javascript-typescript)", "Analyze (python)"})
    artifact = verify_native_stage(plan, datetime.now(timezone.utc))
    verify_native_jobs()
    require(archive.stat().st_size == artifact["size_in_bytes"], "artifact differs from native size")
    verify_archive(plan, archive)
    verify_native_stage(plan, datetime.now(timezone.utc))
    transport = verify_transport(plan, archive, target)
    return {"admitted": True, "lane": LANE, "origin": ORIGIN, "source_sha": SOURCE,
            "image_id": plan["image_id"], "plan_sha256": sha256(plan_bytes).hexdigest(),
            "qualification": "owner-decision", "mutationCampaignPassed": False, **transport}


def main():
    """Keep native credential-bearing errors out of workflow logs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--controller-sha", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--extract-image", type=Path)
    args = parser.parse_args()
    try:
        require(args.plan.is_file() and args.plan.stat().st_size <= 65536, "release plan unavailable")
        verdict = admit(args.plan.read_bytes(), args.controller_sha, args.archive, args.extract_image)
    except Refusal as error:
        verdict = {"admitted": False, "reason": str(error)}
    except Exception:
        verdict = {"admitted": False, "reason": "native Work reopen evidence unavailable or malformed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
