#!/usr/bin/env python3
"""Admit the single owner-authorized Work artifact into the dark production slot."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import zipfile

from axxium_production_qualification import (
    MAX_TAR, REPOSITORY, SERVICES, Refusal, main_ancestor, native_file, require,
    stream_digest, strict_json, validate_plan, verify_archive, verify_native_stage,
)
from environment_policy import production_admission
from github_environment_admission import api

PLAN = "digitalocean/services/axxium-work-pilot/dark-release-plan.json"
SOURCE = "4805e9aa1ab574c2e517f6c156bca0c1e69696fb"
STAGE_RUN = 42747386912
STAGE_DEPLOY = 129604052831
ARTIFACT = 13731817265
CAMPAIGN = 42834010838
POLICY = "b32748ae2d103cacb9ccdecf17119659aac9c0797461e32d5ce96b2e1ce58b33"


def verify_decision(plan):
    """Keep the override bounded to this release, without inventing mutation proof."""
    require((plan["source_sha"], plan["source_pr"], plan["stage_run_id"], plan["artifact_id"],
             plan.get("stage_deploy_job_id"), plan.get("lane"), plan.get("exposure"))
            == (SOURCE, 52, STAGE_RUN, ARTIFACT, STAGE_DEPLOY, "dark-work-pilot", "none"),
            "owner decision applies only to the dark Work release")
    decision = plan.get("owner_decision", {})
    require(decision.get("scope") == "mutation-only"
            and decision.get("reference") == "https://axxium-integration-desk.enso-err.chatgpt.site"
            and decision.get("desk_revision") == 92
            and decision.get("mutation_campaign_passed") is False
            and decision.get("run_id") == CAMPAIGN and decision.get("attempt") == 1
            and decision.get("qualified_batches") == 0 and decision.get("terminal_partitions") == 4
            and decision.get("eligible_over_observed") == [[68, 157], [88, 218], [193, 489], [115, 260]],
            "reviewed mutation-only decision or negative campaign evidence missing")
    require(sha256(Path(__file__).with_name("environment_policy.py").read_bytes()).hexdigest() == POLICY,
            "shared production policy changed; review this exception again")
    verdict = production_admission(SOURCE, plan.get("integration"), plan.get("e2e"), [])
    require(verdict == {"admitted": False, "reason": "four completed mutation batches are required"},
            verdict.get("reason", "unexpected mutation waiver verdict"))
    require(re.fullmatch(r"[0-9a-f]{64}", plan["integration"].get("log_sha256", "")),
            "integration baseline log binding missing")
    rehearsal = plan.get("exact_image_rehearsal", {})
    require(rehearsal.get("source_sha") == SOURCE and rehearsal.get("status") == "passed"
            and all(re.fullmatch(r"[0-9a-f]{64}", rehearsal.get(key, ""))
                    for key in ("log_sha256", "receipt_sha256")),
            "exact image rehearsal evidence missing")


def verify_native_jobs():
    """Cross-check protected staging and the terminal campaign without rerunning either."""
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


def verify_transport(plan, archive, target):
    """Re-hash the verified Docker member before allowing transport to the host."""
    expected = plan.get("images_tar_sha256", "")
    config = plan.get("image_config_digest", "")
    manifest = plan.get("image_manifest_digest", "")
    require(re.fullmatch(r"[0-9a-f]{64}", expected)
            and all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in (config, manifest)),
            "immutable Docker transport digests missing")
    with zipfile.ZipFile(archive) as package:
        with package.open("images.tar") as stream:
            actual, _ = stream_digest(stream, MAX_TAR)
        require(actual == expected, "Docker transport differs from reviewed release")
        if target is not None:
            with target.open("xb") as destination:
                try:
                    with package.open("images.tar") as stream:
                        digest = sha256()
                        size = 0
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            size += len(block)
                            require(size <= MAX_TAR, "Docker transport exceeds bound")
                            digest.update(block)
                            destination.write(block)
                        require(digest.hexdigest() == expected, "copied transport digest mismatch")
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
    return {"images_tar_sha256": expected, "image_config_digest": config, "image_manifest_digest": manifest}


def admit(plan_bytes, controller_sha, archive, target=None):
    """Read immutable controller and native custody before producing any host payload."""
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN, controller_sha, 65536) == plan_bytes,
            "release plan differs from accepted Services controller")
    verify_decision(plan)
    verify_native_jobs()
    artifact = verify_native_stage(plan, datetime.now(timezone.utc))
    require(archive.stat().st_size == artifact["size_in_bytes"], "artifact differs from native size")
    verify_archive(plan, archive)
    verify_native_stage(plan, datetime.now(timezone.utc))
    transport = verify_transport(plan, archive, target)
    return {"admitted": True, "lane": "dark-work-pilot", "source_sha": SOURCE,
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
        verdict = {"admitted": False, "reason": "native Work evidence unavailable or malformed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
