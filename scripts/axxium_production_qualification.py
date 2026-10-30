#!/usr/bin/env python3
"""Read native custody and canonical qualification before any host capability.

The archive is inspected as data; nothing is extracted, loaded or executed.
Only an immutable Services-main qualification file can reach the existing
production_admission policy. A staging build or pilot report supplies no waiver.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

from environment_policy import production_admission, timestamp
from github_environment_admission import api

REPOSITORY = "open-hax/axxium"
SERVICES = "open-hax/services"
PLAN_PATH = "digitalocean/services/axxium/minimal-production-plan.json"
STAGE_WORKFLOW = ".github/workflows/service-environment-staging.yml"
REUSABLE = "open-hax/services/.github/workflows/deploy-service-environment.yml@"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_ZIP = 256 * 1024 * 1024
MAX_TAR = 512 * 1024 * 1024
MAX_JSON = 1024 * 1024

# This exception is deliberately bound to one candidate and one exact plan.
# A plan or shared-policy change requires a new review; it cannot silently
# expand the waiver or invalidate the interpretation of the receipt check.
WAIVER_SOURCE = "58234df96ca86507e6cdd87f65aaa9ab35621415"
WAIVER_PLAN_SHA256 = "6025ff0257a28711cc7847ca6935b97ffc3b11e4ccef76c3eb27a89f16bcafe1"
WAIVER_POLICY_SHA256 = "b32748ae2d103cacb9ccdecf17119659aac9c0797461e32d5ce96b2e1ce58b33"


class Refusal(ValueError):
    """A static, credential-free explanation of missing admission evidence."""


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def strict_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=object_pairs)


def stream_digest(stream, maximum):
    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        require(size <= maximum, "archive size exceeds inspected bounds")
        digest.update(block)
    return digest.hexdigest(), size


def native_file(repository, path, revision, maximum=MAX_JSON):
    value = api(f"repos/{repository}/contents/{path}?ref={revision}")
    require(isinstance(value, dict) and value.get("type") == "file"
            and value.get("encoding") == "base64" and value.get("path") == path,
            "native immutable file metadata unavailable")
    raw = base64.b64decode(value["content"].replace("\n", ""), validate=True)
    require(len(raw) <= maximum and type(value.get("size")) is int
            and value["size"] == len(raw), "native immutable file size mismatch")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    require(value.get("sha") == blob, "native immutable file bytes mismatch")
    return raw


def main_ancestor(repository, revision):
    require(isinstance(revision, str) and SHA.fullmatch(revision), "immutable revision required")
    comparison = api(f"repos/{repository}/compare/{revision}...main")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("base_commit", {}).get("sha") == revision
            and comparison.get("merge_base_commit", {}).get("sha") == revision,
            "revision is not accepted main ancestry")


def validate_plan(plan):
    require(isinstance(plan, dict) and plan.get("schema") == "axxium-minimal-production/v1"
            and plan.get("host") == "knoxx.promethean.rest", "unsupported production plan")
    for key in ("source_sha", "stage_controller_sha"):
        require(isinstance(plan.get(key), str) and SHA.fullmatch(plan[key]), "immutable plan source required")
    require(isinstance(plan.get("image_id"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", plan["image_id"]), "immutable image ID required")
    require(isinstance(plan.get("artifact_sha256"), str) and DIGEST.fullmatch(plan["artifact_sha256"]),
            "immutable artifact digest required")
    for key in ("source_pr", "stage_run_id", "artifact_id"):
        require(type(plan.get(key)) is int and plan[key] > 0, "positive native identity required")
    require(plan.get("stage_workflow_path") == STAGE_WORKFLOW, "unexpected staging caller")


def verify_native_stage(plan, now):
    main_ancestor(REPOSITORY, plan["source_sha"])
    main_ancestor(SERVICES, plan["stage_controller_sha"])
    pr = api(f"repos/{REPOSITORY}/pulls/{plan['source_pr']}")
    require(type(pr.get("number")) is int and pr.get("number") == plan["source_pr"] and pr.get("merged") is True
            and pr.get("state") == "closed" and pr.get("base", {}).get("ref") == "main"
            and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY
            and pr.get("merge_commit_sha") == plan["source_sha"], "native merged source PR mismatch")
    head = pr.get("head", {}).get("sha")
    require(isinstance(head, str) and SHA.fullmatch(head), "native source head unavailable")
    run = api(f"repos/{REPOSITORY}/actions/runs/{plan['stage_run_id']}")
    repository = run.get("repository", {})
    require(type(run.get("id")) is int and run.get("id") == plan["stage_run_id"] and run.get("event") == "pull_request"
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and run.get("path") == STAGE_WORKFLOW and run.get("head_sha") == head
            and repository.get("full_name") == REPOSITORY
            and type(repository.get("id")) is int and repository["id"] > 0,
            "native staging run mismatch")
    references = run.get("referenced_workflows")
    require(isinstance(references, list) and len(references) == 1
            and references[0].get("path") == REUSABLE + plan["stage_controller_sha"]
            and references[0].get("sha") == plan["stage_controller_sha"],
            "native staging controller mismatch")
    require(timestamp(pr["merged_at"]) <= timestamp(run["created_at"]) <= timestamp(run["updated_at"]) <= now,
            "native staging chronology mismatch")
    artifact = api(f"repos/{REPOSITORY}/actions/artifacts/{plan['artifact_id']}")
    custody = artifact.get("workflow_run", {})
    require(type(artifact.get("id")) is int and artifact.get("id") == plan["artifact_id"] and artifact.get("name") == "service-environment-images"
            and artifact.get("expired") is False
            and artifact.get("digest") == "sha256:" + plan["artifact_sha256"]
            and custody.get("id") == run["id"] and custody.get("head_sha") == head
            and custody.get("repository_id") == repository["id"]
            and custody.get("head_repository_id") == repository["id"],
            "native staging artifact custody mismatch")
    require(timestamp(run["created_at"]) <= timestamp(artifact["created_at"]) <= timestamp(run["updated_at"])
            and timestamp(artifact["expires_at"]) > now, "native artifact is stale or temporally unbound")
    require(type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ZIP,
            "native artifact size unavailable")
    return artifact


def verify_archive(plan, archive):
    """Verify one OCI Docker-save artifact without executing any member."""
    require(archive.is_file() and 0 < archive.stat().st_size <= MAX_ZIP, "archive unavailable or too large")
    with archive.open("rb") as stream:
        actual, _ = stream_digest(stream, MAX_ZIP)
    require(actual == plan["artifact_sha256"], "archive differs from native digest")
    metadata = {}
    sizes = {}
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        require(len(members) == 2 and {item.filename for item in members} == {"images.tar", "images.sha256"}
                and all(not item.is_dir() and not item.flag_bits & 1
                        and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for item in members),
                "unexpected artifact members")
        require(package.getinfo("images.sha256").file_size == 65
                and 0 < package.getinfo("images.tar").file_size <= MAX_TAR, "artifact member size mismatch")
        checksum = package.read("images.sha256").decode("ascii")
        require(re.fullmatch(r"[0-9a-f]{64}\n", checksum), "invalid archive checksum")
        with package.open("images.tar") as stream:
            tar_digest, _ = stream_digest(stream, MAX_TAR)
        require(tar_digest == checksum.strip(), "inner archive checksum mismatch")
        with package.open("images.tar") as stream, tarfile.open(fileobj=stream, mode="r|") as image:
            count = 0
            captured = 0
            for member in image:
                count += 1
                name = member.name
                require(count <= 256 and not PurePosixPath(name).is_absolute()
                        and ".." not in PurePosixPath(name).parts, "unsafe image member")
                if member.isdir():
                    require(name.rstrip("/") in ("blobs", "blobs/sha256"), "unexpected image directory")
                    continue
                require(member.isfile() and name not in sizes and 0 <= member.size <= MAX_TAR,
                        "ambiguous image member")
                sizes[name] = member.size
                blob = re.fullmatch(r"blobs/sha256/([0-9a-f]{64})", name)
                require(blob or name in ("index.json", "manifest.json", "oci-layout", "repositories"), "unexpected image format")
                data = image.extractfile(member)
                if member.size <= MAX_JSON:
                    raw = data.read(MAX_JSON + 1)
                    captured += len(raw)
                    require(captured <= 8 * MAX_JSON and len(raw) == member.size, "image metadata exceeds bounds")
                    metadata[name] = raw
                    digest = hashlib.sha256(raw).hexdigest()
                else:
                    digest, size = stream_digest(data, MAX_TAR)
                    require(size == member.size, "truncated image member")
                if blob:
                    require(digest == blob.group(1), "OCI blob digest mismatch")
    layout = strict_json(metadata["oci-layout"])
    index = strict_json(metadata["index.json"])
    require(layout == {"imageLayoutVersion": "1.0.0"} and index.get("schemaVersion") == 2
            and len(index.get("manifests", [])) == 1, "unexpected OCI layout")
    selected = index["manifests"][0]
    image_digest = plan["image_id"].split(":", 1)[1]
    image_path = "blobs/sha256/" + image_digest
    require(selected.get("digest") == plan["image_id"] and selected.get("size") == sizes.get(image_path)
            and selected.get("mediaType") == "application/vnd.oci.image.manifest.v1+json"
            and selected.get("annotations", {}).get("io.containerd.image.name")
                == "docker.io/library/promethean-axxium:" + plan["source_sha"]
            and selected.get("annotations", {}).get("org.opencontainers.image.ref.name") == plan["source_sha"],
            "OCI image/source reference mismatch")
    manifest = strict_json(metadata[image_path])
    config = manifest.get("config", {})
    require(manifest.get("schemaVersion") == 2 and isinstance(config.get("digest"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", config["digest"]), "OCI configuration unavailable")
    config_path = "blobs/sha256/" + config["digest"].split(":", 1)[1]
    require(config.get("size") == sizes.get(config_path), "OCI configuration size mismatch")
    configuration = strict_json(metadata[config_path])
    layers = manifest.get("layers")
    require(isinstance(layers, list) and layers, "OCI layers unavailable")
    paths = []
    for layer in layers:
        digest = layer.get("digest")
        require(isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                and layer.get("mediaType") == "application/vnd.oci.image.layer.v1.tar",
                "unexpected OCI layer format")
        path = "blobs/sha256/" + digest.split(":", 1)[1]
        require(layer.get("size") == sizes.get(path), "OCI layer size mismatch")
        paths.append(path)
    docker = strict_json(metadata["manifest.json"])
    require(isinstance(docker, list) and len(docker) == 1
            and docker[0].get("Config") == config_path and docker[0].get("Layers") == paths
            and docker[0].get("RepoTags") == ["promethean-axxium:" + plan["source_sha"]],
            "Docker manifest differs from native candidate")
    require(strict_json(metadata["repositories"]) == {
        "promethean-axxium": {plan["source_sha"]: layers[-1]["digest"].split(":", 1)[1]}},
        "legacy tag differs from OCI candidate")
    require(configuration.get("os") == "linux" and configuration.get("architecture") == "amd64"
            and configuration.get("rootfs") == {"type": "layers", "diff_ids": [item["digest"] for item in layers]},
            "OCI root filesystem binding mismatch")
    return plan["image_id"]


def verify_mutation_exception(plan, plan_bytes, proof):
    """Admit only the reviewed mutation waiver; never assert mutation success.

    A missing-batches verdict is used solely to confirm the existing shared
    policy has accepted integration and e2e. No synthetic mutation evidence is
    supplied. The exact shared policy bytes are pinned to prevent later policy
    changes from being silently skipped by this candidate-only exception.
    """
    policy_path = Path(__file__).with_name("environment_policy.py")
    require(hashlib.sha256(policy_path.read_bytes()).hexdigest() == WAIVER_POLICY_SHA256,
            "shared policy changed; mutation exception needs review")
    plan_hash = hashlib.sha256(plan_bytes).hexdigest()
    require(plan.get("source_sha") == WAIVER_SOURCE and plan_hash == WAIVER_PLAN_SHA256,
            "mutation exception does not apply to this candidate")
    exception = proof.get("mutation_exception")
    require(isinstance(exception, dict)
            and exception.get("schema") == "axxium-mutation-exception/v1"
            and exception.get("status") == "approved"
            and exception.get("scope") == "mutation-only"
            and exception.get("source_sha") == WAIVER_SOURCE
            and exception.get("plan_sha256") == plan_hash
            and exception.get("image_id") == plan.get("image_id")
            and exception.get("stage_run_id") == plan.get("stage_run_id")
            and exception.get("artifact_sha256") == plan.get("artifact_sha256")
            and exception.get("mutation_campaign_passed") is False
            and isinstance(exception.get("owner_decision_reference"), str)
            and bool(exception["owner_decision_reference"].strip()),
            "reviewed candidate-specific mutation exception unavailable")
    result = production_admission(plan["source_sha"], proof.get("integration"), proof.get("e2e"), None)
    require(result == {"admitted": False, "reason": "four completed mutation batches are required"},
            "integration or e2e evidence missing or invalid")
    return {"qualification": "owner-decision", "mutationCampaignPassed": False}


def admit(plan_bytes, controller_sha, archive, *, now=None):
    fixed_time = now
    now = now or datetime.now(timezone.utc)
    plan = strict_json(plan_bytes)
    validate_plan(plan)
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN_PATH, controller_sha, 65536) == plan_bytes,
            "plan differs from accepted immutable controller")
    artifact = verify_native_stage(plan, now)
    require(archive.stat().st_size == artifact["size_in_bytes"], "archive differs from native size")
    verify_archive(plan, archive)
    path = f"digitalocean/qualifications/axxium/{plan['source_sha']}.json"
    try:
        proof = strict_json(native_file(SERVICES, path, controller_sha))
    except Exception as error:
        raise Refusal("immutable source qualification unavailable") from error
    require(isinstance(proof, dict) and proof.get("repository") == REPOSITORY
            and proof.get("source_sha") == plan["source_sha"], "qualification source binding mismatch")
    verdict = production_admission(plan["source_sha"], proof.get("integration"),
                                   proof.get("e2e"), proof.get("mutation_batches"))
    exception_verdict = {}
    if verdict.get("admitted") is not True:
        exception_verdict = verify_mutation_exception(plan, plan_bytes, proof)
    # Re-read mutable native custody after archive inspection and policy admission.
    main_ancestor(SERVICES, controller_sha)
    verify_native_stage(plan, fixed_time or datetime.now(timezone.utc))
    return {"admitted": True, "source_sha": plan["source_sha"], "image_id": plan["image_id"],
            "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(), **exception_verdict}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--controller-sha", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(args.plan.is_file() and args.plan.stat().st_size <= 65536, "production plan unavailable")
        verdict = admit(args.plan.read_bytes(), args.controller_sha, args.archive)
    except Refusal as error:
        verdict = {"admitted": False, "reason": str(error)}
    except Exception:
        # Native/API/JSON errors can include protocol or credential-bearing URLs.
        verdict = {"admitted": False, "reason": "production evidence unavailable or malformed"}
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
