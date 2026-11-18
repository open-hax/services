#!/usr/bin/env python3
"""Admit the fixed same-image Events profile and its native rehearsal evidence."""

from datetime import datetime, timezone
from hashlib import sha256
import io
from pathlib import Path
import subprocess
import zipfile

import axxium_active_mailbox_release as active
from axxium_production_qualification import (
    DIGEST, SHA, SERVICES, main_ancestor, native_file, require, strict_json,
)
from axxium_unified_mailbox_admission import admit, main
from environment_policy import timestamp
from github_environment_admission import api

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = "digitalocean/services/axxium/mailbox-events-production-plan.json"
PLAN_BYTES = (ROOT / PLAN_PATH).read_bytes()
SCHEMA = "axxium-mailbox-events-production/v1"
SUFFIX = "-mailbox-events"
WORKFLOW = ".github/workflows/axxium-events-staging.yml"
CENSUS_WORKFLOW = ".github/workflows/axxium-events-census.yml"
CENSUS_FILES = (
    CENSUS_WORKFLOW,
    "scripts/axxium_events_census.py",
    "scripts/axxium_events_profile.py",
    "scripts/staging/axxium/events-census/mailbox_events_export.cljc",
    "scripts/staging/axxium/events-census/probe.mjs",
    "scripts/staging/axxium/events-census/rehearsal_probe.cljs",
    "scripts/staging/axxium/events-census/shadow-cljs.edn",
    "scripts/staging/axxium/events-census/verify_build.py",
)
SCENARIOS = {"healthy", "known-failure", "uncertain"}
MAX_REHEARSAL_ARCHIVE = 8192
MAX_REHEARSAL_RECEIPT = 8192
# Every native release module staged into the rehearsal is covered by its
# native head and must be unchanged when the protected caller admits it.
REHEARSED_FILES = (
    PLAN_PATH,
    WORKFLOW,
    active.PLAN_PATH,
    "digitalocean/services/axxium/unified-mailbox-production-plan.json",
    "scripts/axxium_events_profile.py",
    "scripts/axxium_events_release.py",
    "scripts/axxium_events_census.py",
    "scripts/axxium_events_census_archive.py",
    "scripts/axxium_active_mailbox_release.py",
    "scripts/axxium_unified_mailbox_release.py",
    "scripts/axxium_minimal_release.py",
    "scripts/axxium_ingress_host.py",
    "scripts/axxium_ingress_transaction.py",
    "scripts/axxium_interrupted_handoff.py",
    "scripts/axxium_ingress_caddy.py",
    "scripts/axxium_oidc_ingress.py",
    "scripts/axxium_production_qualification.py",
    "scripts/environment_policy.py",
    "scripts/github_environment_admission.py",
    "scripts/staging/axxium/events_restart_fixture.py",
    "scripts/staging/axxium/active_upgrade_fixture.py",
    "scripts/staging/axxium/prepare_active_images.py",
    "scripts/staging/axxium/network_fixture.py",
    "scripts/staging/axxium/fixture_netns.py",
    "scripts/staging/axxium/full_recovery_probe.py",
    "scripts/staging/axxium/identity_fixture.py",
)


def validate_plan_bytes(raw):
    require(raw == PLAN_BYTES, "Events plan differs from the immutable reviewed profile")
    plan = strict_json(raw)
    require(plan.get("schema") == SCHEMA and plan["image_id"] == active.IMAGE
            and plan["expected"]["app_image_id"] == active.IMAGE
            and plan["source_sha"] == active.SOURCE == plan["incumbent"]["source_sha"]
            and plan["recipient_did"] == active.PLAN["identity"]["work_atproto_did"]
            and plan["incumbent"]["plan_sha256"] == sha256(active.PLAN_BYTES).hexdigest()
            and plan["compose_chain"][:-1] == active.PLAN["compose_chain"]
            and plan["identity"] == active.PLAN["identity"]
            and plan["retained_config"] == active.PLAN["retained_config"]
            and plan["ingress"] == active.PLAN["ingress"], "Events predecessor custody differs")
    pins = plan["census"]
    digests = {"module_sha256", "driver_sha256", "artifact_sha256"}
    ids = {"build_run_id", "build_attempt", "artifact_id"}
    require(set(pins) == digests | ids | {"build_head"}
            and all(isinstance(pins[key], str) and DIGEST.fullmatch(pins[key]) for key in digests)
            and all(type(pins[key]) is int and pins[key] > 0 for key in ids)
            and isinstance(pins["build_head"], str) and SHA.fullmatch(pins["build_head"]),
            "Reviewed callback census pins are missing")
    return plan


def verify_native_census_build(pins, controller_sha, now):
    head, run_id = pins["build_head"], pins["build_run_id"]
    main_ancestor(SERVICES, head)
    run = api(f"repos/{SERVICES}/actions/runs/{run_id}")
    require(run.get("id") == run_id and run.get("head_sha") == head
            and run.get("repository", {}).get("full_name") == SERVICES
            and run.get("path") == CENSUS_WORKFLOW
            and run.get("run_attempt") == pins["build_attempt"]
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and timestamp(run["updated_at"]) <= now,
            "Native Events census build differs from the accepted binding")
    artifact = api(f"repos/{SERVICES}/actions/artifacts/{pins['artifact_id']}")
    custody = artifact.get("workflow_run", {})
    require(artifact.get("id") == pins["artifact_id"] and artifact.get("name") == "axxium-events-census"
            and artifact.get("digest") == "sha256:" + pins["artifact_sha256"]
            and artifact.get("expired") is False and timestamp(artifact["expires_at"]) > now
            and custody.get("id") == run_id and custody.get("head_sha") == head,
            "Native Events census artifact custody differs")
    for path in CENSUS_FILES:
        require(native_file(SERVICES, path, head) == native_file(SERVICES, path, controller_sha),
                "Protected Events census recipe differs from the hosted build")


def verify_native_rehearsal(e2e, controller_sha, now):
    head = e2e.get("rehearsal_head")
    run_id = e2e.get("rehearsal_run_id")
    attempt = e2e.get("rehearsal_attempt")
    require(isinstance(head, str) and SHA.fullmatch(head) and type(run_id) is int and run_id > 0,
            "Native Events rehearsal binding required")
    main_ancestor(SERVICES, head)
    run = api(f"repos/{SERVICES}/actions/runs/{run_id}")
    require(run.get("id") == run_id and run.get("head_sha") == head
            and run.get("repository", {}).get("full_name") == SERVICES
            and run.get("path") == WORKFLOW and run.get("event") in {"pull_request", "workflow_dispatch"}
            and type(attempt) is int and attempt > 0 and run.get("run_attempt") == attempt
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and timestamp(run["updated_at"]) <= now,
            "Native Events rehearsal run differs from the accepted binding")
    response = api(f"repos/{SERVICES}/actions/runs/{run_id}/attempts/{attempt}/jobs")
    jobs = response.get("jobs", [])
    expected = {f"same-image ({scenario})" for scenario in SCENARIOS}
    require(len(jobs) == len(expected) and {job.get("name") for job in jobs} == expected
            and all(job.get("status") == "completed" and job.get("conclusion") == "success"
                    and job.get("head_sha") == head and job.get("run_id") == run_id for job in jobs),
            "Native same-image restart, rollback or held interruption was not verified")
    for path in REHEARSED_FILES:
        require(native_file(SERVICES, path, head) == native_file(SERVICES, path, controller_sha),
                "Protected Events runtime or rehearsal input differs from the hosted candidate")
    return run


def verify_rehearsal_evidence(plan, e2e, run, controller_sha, now):
    path = f"digitalocean/qualifications/axxium/evidence/{plan['source_sha']}{SUFFIX}-e2e.json"
    raw = native_file(SERVICES, path, controller_sha)
    require(sha256(raw).hexdigest() == e2e["evidence_sha256"],
            "Hosted Events rehearsal receipt bytes differ")
    evidence = strict_json(raw)
    require(evidence.get("schema") == "axxium-events-native-rehearsal/v1"
            and evidence.get("run_id") == e2e["rehearsal_run_id"]
            and evidence.get("run_attempt") == e2e["rehearsal_attempt"]
            and evidence.get("controller_head") == e2e["rehearsal_head"]
            and evidence.get("source_sha") == plan["source_sha"]
            and evidence.get("census_module_sha256") == plan["census"]["module_sha256"]
            and set(evidence.get("scenarios", {})) == SCENARIOS,
            "Hosted Events rehearsal receipts do not match the plan")
    scenarios = {
        "healthy": ("healthy", 2, False),
        "known-failure": ("rolled_back", 1, True),
        "uncertain": ("held", 1, False),
    }
    for name, (status, starts, rollback) in scenarios.items():
        row = evidence["scenarios"][name]
        receipt = row["receipt"]
        require(receipt.get("scenario") == name and receipt.get("status") == status
                and receipt.get("source_sha") == plan["source_sha"]
                and receipt.get("image_id") == plan["image_id"]
                and receipt.get("same_image_restart_verified") is True
                and receipt.get("actual_candidate_starts") == starts
                and receipt.get("held_callback_census_checks") == starts
                and receipt.get("census_module_sha256") == plan["census"]["module_sha256"]
                and receipt.get("candidate_acknowledgement") is rollback
                and receipt.get("rollback_verified") is rollback,
                "Hosted Events rehearsal scenario is incomplete")
        artifact = api(f"repos/{SERVICES}/actions/artifacts/{row['artifact_id']}")
        custody = artifact.get("workflow_run", {})
        require(artifact.get("id") == row["artifact_id"] and artifact.get("name") == "axxium-events-fixture-" + name
                and artifact.get("digest") == "sha256:" + row["archive_sha256"]
                and artifact.get("expired") is False and timestamp(artifact["expires_at"]) > now
                and timestamp(run["run_started_at"]) <= timestamp(artifact["created_at"]) <= timestamp(run["updated_at"])
                and custody.get("id") == evidence["run_id"] and custody.get("head_sha") == evidence["controller_head"],
                "Native Events rehearsal artifact custody differs")
        verify_native_receipt(artifact, row)


def verify_native_receipt(artifact, row):
    size = artifact.get("size_in_bytes")
    require(type(size) is int and 0 < size <= MAX_REHEARSAL_ARCHIVE,
            "Native Events rehearsal archive size differs")
    result = subprocess.run(
        ["gh", "api", f"repos/{SERVICES}/actions/artifacts/{artifact['id']}/zip"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
    )
    require(result.returncode == 0 and len(result.stdout) == size
            and sha256(result.stdout).hexdigest() == row["archive_sha256"],
            "Native Events rehearsal archive bytes differ")
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as archive:
        members = archive.infolist()
        require(len(members) == 1 and members[0].filename == "events-restart-result.json"
                and 0 < members[0].file_size <= MAX_REHEARSAL_RECEIPT,
                "Native Events rehearsal receipt member differs")
        raw = archive.read(members[0])
    require(sha256(raw).hexdigest() == row["receipt_sha256"] and strict_json(raw) == row["receipt"],
            "Native Events rehearsal receipt differs from the admitted copy")


def admit_events(plan_bytes, controller_sha, archive, *, now=None):
    observed_at = now or datetime.now(timezone.utc)
    plan = validate_plan_bytes(plan_bytes)
    verdict = admit(
        plan_bytes, controller_sha, archive, now=now, plan_path=PLAN_PATH, plan_schema=SCHEMA,
        qualification_scope="mailbox-events", qualification_suffix=SUFFIX,
    )
    path = f"digitalocean/qualifications/axxium/{plan['source_sha']}{SUFFIX}.json"
    proof = strict_json(native_file(SERVICES, path, controller_sha))
    run = verify_native_rehearsal(proof["e2e"], controller_sha, observed_at)
    verify_rehearsal_evidence(plan, proof["e2e"], run, controller_sha, observed_at)
    verify_native_census_build(plan["census"], controller_sha, observed_at)
    return verdict


if __name__ == "__main__":
    raise SystemExit(main(admit_events))
