#!/usr/bin/env python3
"""Admit the owner-authorized fenced Work artifact for the two-actor pilot."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from axxium_production_qualification import (
    REPOSITORY, SERVICES, Refusal, main_ancestor, native_file, require,
    strict_json, validate_plan, verify_archive, verify_native_stage,
)
from axxium_work_dark_admission import CAMPAIGN, POLICY, SOURCE as DARK_SOURCE, verify_transport
from environment_policy import production_admission
from github_environment_admission import api

PLAN = "digitalocean/services/axxium-work-pilot/activation-release-plan.json"
REHEARSAL = "digitalocean/services/axxium-work-pilot/activation-rehearsal.json"
REHEARSAL_SHA256 = "6800dcbbfe9ec5a109ec048017472e57960ab6007a1d172832b67b53345632ad"
SOURCE = "7daca10cd0e6e83a4c8da626899bf59236a681be"
STAGE_RUN = 42875788429
STAGE_DEPLOY = 130041327018
ARTIFACT = 13791652349
LANE = "activate-work-pilot"
ORIGIN = "https://work.pilot.axxium.promethean.rest"


def verify_decision(plan):
    """Keep the mutation exception confined to the fenced pilot release."""
    require((plan["source_sha"], plan["source_pr"], plan["stage_run_id"], plan["artifact_id"],
             plan.get("stage_deploy_job_id"), plan.get("lane"), plan.get("exposure"), plan.get("origin"))
            == (SOURCE, 57, STAGE_RUN, ARTIFACT, STAGE_DEPLOY, LANE, "two-actor-work-pilot", ORIGIN),
            "owner decision applies only to the fenced Work pilot release")
    require(plan.get("owner_decision") == {
        "scope": "mutation-only",
        "reference": "https://axxium-integration-desk.enso-err.chatgpt.site",
        "desk_revision": 101,
        "source_sha": SOURCE,
        "artifact_id": ARTIFACT,
        "artifact_sha256": plan["artifact_sha256"],
        "mutation_campaign_passed": False,
    } and plan["owner_decision"]["mutation_campaign_passed"] is False,
        "reviewed mutation-only decision missing")
    require(plan.get("historical_campaign") == {
        "source_sha": DARK_SOURCE,
        "run_id": CAMPAIGN,
        "attempt": 1,
        "qualified_batches": 0,
        "terminal_partitions": 4,
        "eligible_over_observed": [[68, 157], [88, 218], [193, 489], [115, 260]],
    }, "predecessor mutation results differ from retained negative evidence")
    require(sha256(Path(__file__).with_name("environment_policy.py").read_bytes()).hexdigest() == POLICY,
            "shared production policy changed; review this exception again")
    verdict = production_admission(SOURCE, plan.get("integration"), plan.get("e2e"), [])
    require(verdict == {"admitted": False, "reason": "four completed mutation batches are required"},
            verdict.get("reason", "unexpected mutation waiver verdict"))


def verify_rehearsal(plan, receipt_bytes):
    """Bind ordinary verification to the exact staged artifact and retained rehearsal."""
    rehearsal = plan.get("exact_image_rehearsal", {})
    require(rehearsal.get("receipt_sha256") == REHEARSAL_SHA256
            and sha256(receipt_bytes).hexdigest() == REHEARSAL_SHA256,
            "exact image rehearsal differs from reviewed receipt")
    receipt = strict_json(receipt_bytes)
    require(receipt.get("schema") == "axxium-fenced-exact-image-rehearsal/v1"
            and receipt.get("status") == "passed"
            and receipt.get("log_sha256") == rehearsal.get("log_sha256")
            and receipt.get("image_index_digest") == plan["image_id"]
            and all(receipt.get(key) == plan.get(key) for key in (
                "source_sha", "source_pr", "stage_run_id", "stage_deploy_job_id",
                "artifact_id", "artifact_sha256", "images_tar_sha256",
                "image_manifest_digest", "image_config_digest")),
            "exact image rehearsal is not bound to this release")


def verify_native_jobs():
    """Require the hosted receiver result and keep the predecessor failure explicit."""
    staged = api(f"repos/{REPOSITORY}/actions/jobs/{STAGE_DEPLOY}")
    require(staged.get("id") == STAGE_DEPLOY and staged.get("run_id") == STAGE_RUN
            and staged.get("name") == "staging / deploy" and staged.get("conclusion") == "success"
            and any(step.get("name") == "Verify public HTTPS and readiness"
                    and step.get("conclusion") == "success" for step in staged.get("steps", [])),
            "successful native stage receiver evidence missing")
    campaign = api(f"repos/{REPOSITORY}/actions/runs/{CAMPAIGN}/attempts/1")
    require(campaign.get("id") == CAMPAIGN and campaign.get("run_attempt") == 1
            and campaign.get("path") == ".github/workflows/qualification-work-campaign.yml"
            and campaign.get("status") == "completed" and campaign.get("conclusion") == "failure",
            "native terminal mutation evidence missing")


def admit(plan_bytes, controller_sha, archive, target=None):
    """Verify immutable Services-main evidence and archive before host transport."""
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN, controller_sha, 65536) == plan_bytes,
            "release plan differs from accepted Services controller")
    verify_decision(plan)
    verify_rehearsal(plan, native_file(SERVICES, REHEARSAL, controller_sha, 65536))
    verify_native_jobs()
    artifact = verify_native_stage(plan, datetime.now(timezone.utc))
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
        verdict = {"admitted": False, "reason": "native Work activation evidence unavailable or malformed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
