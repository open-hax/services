#!/usr/bin/env python3
"""Admit the unified Axxium mailbox image and its reviewed rehearsal evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from axxium_production_qualification import (
    DIGEST, REPOSITORY, SERVICES, Refusal, main_ancestor, native_file, require,
    strict_json, validate_plan, verify_archive, verify_native_stage,
)
from environment_policy import timestamp

PLAN_PATH = "digitalocean/services/axxium/unified-mailbox-production-plan.json"
PLAN_SCHEMA = "axxium-unified-mailbox-production/v1"
QUALIFICATION_SCHEMA = "axxium-unified-mailbox-qualification/v1"
MAX_PLAN = 65536


def verify_qualification(proof, plan, plan_hash, now):
    require(isinstance(proof, dict) and proof.get("schema") == QUALIFICATION_SCHEMA
            and proof.get("repository") == REPOSITORY and proof.get("status") == "passed"
            and proof.get("source_sha") == plan["source_sha"]
            and proof.get("image_id") == plan["image_id"] and proof.get("plan_sha256") == plan_hash,
            "unified mailbox qualification does not bind the candidate and plan")
    for kind in ("integration", "e2e"):
        receipt = proof.get(kind)
        reason = f"exact unified mailbox {kind} evidence unavailable or invalid"
        require(isinstance(receipt, dict) and receipt.get("kind") == kind
                and receipt.get("source_sha") == plan["source_sha"] and receipt.get("status") == "passed"
                and type(receipt.get("assertions")) is int and receipt["assertions"] > 0
                and receipt.get("warnings") == [] and receipt.get("timed_out") is False
                and isinstance(receipt.get("evidence_sha256"), str)
                and DIGEST.fullmatch(receipt["evidence_sha256"]), reason)
        try:
            completed_at = timestamp(receipt.get("completed_at"))
        except ValueError as error:
            raise Refusal(reason) from error
        require(completed_at <= now, reason)
    require(proof["e2e"].get("image_id") == plan["image_id"],
            "end-to-end rehearsal image differs from the reviewed candidate")
    # The owner's retired harness applies only to this release lane. The legacy
    # fixed plan and other service admission continue to use their existing laws.
    require(proof.get("mutation_batches") == []
            and proof.get("mutation_harness") == {"status": "retired", "campaign_passed": False}
            and proof["mutation_harness"]["campaign_passed"] is False,
            "unified mailbox qualification must explicitly record retired mutation harness")


def admit(plan_bytes, controller_sha, archive, *, now=None):
    observed_at = now or datetime.now(timezone.utc)
    require(0 < len(plan_bytes) <= MAX_PLAN, "unified mailbox production plan unavailable")
    plan = strict_json(plan_bytes)
    require(isinstance(plan, dict) and plan.get("schema") == PLAN_SCHEMA,
            "unsupported unified mailbox production plan")
    # Both plans share the immutable host and artifact-custody fields.
    validate_plan({**plan, "schema": "axxium-minimal-production/v1"})
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN_PATH, controller_sha, MAX_PLAN) == plan_bytes,
            "unified mailbox plan differs from accepted immutable controller")
    plan_hash = sha256(plan_bytes).hexdigest()
    qualification_path = f"digitalocean/qualifications/axxium/{plan['source_sha']}.json"
    try:
        proof = strict_json(native_file(SERVICES, qualification_path, controller_sha))
    except Exception as error:
        raise Refusal("immutable unified mailbox source qualification unavailable") from error
    verify_qualification(proof, plan, plan_hash, observed_at)
    artifact = verify_native_stage(plan, observed_at)
    require(archive.stat().st_size == artifact["size_in_bytes"], "archive differs from native size")
    verify_archive(plan, archive)
    main_ancestor(SERVICES, controller_sha)
    current_artifact = verify_native_stage(plan, now or datetime.now(timezone.utc))
    require(current_artifact["size_in_bytes"] == artifact["size_in_bytes"], "native artifact size changed")
    return {"admitted": True, "qualification": "unified-mailbox", "source_sha": plan["source_sha"],
            "plan_sha256": plan_hash, "image_id": plan["image_id"], "mutationHarness": "retired",
            "mutationCampaignPassed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--controller-sha", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(args.plan.is_file() and 0 < args.plan.stat().st_size <= MAX_PLAN,
                "unified mailbox production plan unavailable")
        verdict = admit(args.plan.read_bytes(), args.controller_sha, args.archive)
    except Refusal as error:
        verdict = {"admitted": False, "reason": str(error)}
    except Exception:
        verdict = {"admitted": False, "reason": "unified mailbox production evidence unavailable or malformed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
