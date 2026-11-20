#!/usr/bin/env python3
"""Select the reviewed Proxx-only production artifact by its exact source."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


PIN = Path(__file__).resolve().parents[1] / "digitalocean/qualifications/proxx/production-artifact.json"
IMAGE = "ghcr.io/open-hax/proxx"
SOURCE = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_fields(pairs):
    fields = dict(pairs)
    require(len(fields) == len(pairs), "duplicate field in artifact evidence")
    return fields


def read_json(raw):
    return json.loads(raw, object_pairs_hook=unique_fields)


def read_pin(source):
    require(SOURCE.fullmatch(source), "expected an immutable Proxx source")
    pin = read_json(PIN.read_bytes())
    require(isinstance(pin, dict) and set(pin) == {
        "schema", "source", "index", "amd64_manifest", "amd64_config", "build",
    } and pin["schema"] == "proxx-production-artifact/v1", "invalid Proxx artifact record")
    for key, pattern in (("source", SOURCE), ("index", DIGEST),
                         ("amd64_manifest", DIGEST), ("amd64_config", DIGEST)):
        require(isinstance(pin[key], str) and pattern.fullmatch(pin[key]),
                "invalid Proxx source or digest")
    build = pin["build"]
    require(isinstance(build, dict) and set(build) == {"controller", "run", "job", "attempt"}
            and isinstance(build["controller"], str) and SOURCE.fullmatch(build["controller"])
            and all(type(build[field]) is int and build[field] > 0 for field in ("run", "job", "attempt")),
            "invalid Proxx build identity")
    return pin if pin["source"] == source else None


def read_cmd(*args):
    return subprocess.run(args, check=True, capture_output=True, timeout=30).stdout


def read_gh(path):
    return read_json(read_cmd("gh", "api", path))


def verify_build_log(pin, log):
    source = pin["source"]
    source_line = f"Building proxx from open-hax/proxx@{source}".encode()
    push_line = re.compile(source.encode() + rb": digest: (sha256:[0-9a-f]{64}) size: [0-9]+")
    sources, pushes = [], []
    for line in log.splitlines():
        label, separator, payload = line.partition(b"\t")
        if label != b"Build proxx" or not separator:
            continue
        step, _, timed = payload.partition(b"\t")
        _, _, message = timed.partition(b" ")
        if step == b"Record resolved source commit" and message.startswith(b"Building proxx from "):
            sources.append(message)
        if step == b"Build and push":
            match = push_line.fullmatch(message)
            if match:
                pushes.append(match[1])
    require(sources == [source_line] and pushes == [pin["index"].encode()],
            "trusted build log does not bind the exact Proxx source and pushed digest")


def verify_build(pin):
    build = pin["build"]
    repo = "repos/open-hax/services"
    run = read_gh(f"{repo}/actions/runs/{build['run']}/attempts/{build['attempt']}")
    expected = {
        "id": build["run"], "run_attempt": build["attempt"], "head_sha": build["controller"],
        "head_branch": "main", "path": ".github/workflows/build-images.yml",
        "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
    }
    require(all(run.get(key) == value for key, value in expected.items())
            and run.get("repository", {}).get("full_name") == "open-hax/services"
            and run.get("head_repository", {}).get("full_name") == "open-hax/services",
            "record does not bind a successful Services main builder attempt")
    job = read_gh(f"{repo}/actions/jobs/{build['job']}")
    expected = {
        "id": build["job"], "run_id": build["run"], "run_attempt": build["attempt"],
        "name": "Build proxx", "status": "completed", "conclusion": "success",
    }
    require(all(job.get(key) == value for key, value in expected.items()),
            "record does not bind the successful Proxx build job")
    source = pin["source"]
    compare = read_gh(f"repos/open-hax/proxx/compare/{source}...main")
    require(compare.get("merge_base_commit", {}).get("sha") == source,
            "artifact source is not reachable from Proxx main")
    log = read_cmd("gh", "run", "view", str(build["run"]), "--repo", "open-hax/services",
                   "--attempt", str(build["attempt"]), "--job", str(build["job"]), "--log")
    verify_build_log(pin, log)


def registry_manifest(digest):
    raw = read_cmd("docker", "buildx", "imagetools", "inspect", "--raw", f"{IMAGE}@{digest}")
    require("sha256:" + hashlib.sha256(raw).hexdigest() == digest,
            "registry response bytes do not match the recorded digest")
    manifest = read_json(raw)
    require(manifest.get("schemaVersion") == 2, "unsupported Proxx artifact manifest schema")
    return manifest


def verify_registry(pin):
    index = registry_manifest(pin["index"])
    require(index.get("mediaType") == "application/vnd.oci.image.index.v1+json",
            "recorded Proxx digest is not an OCI index")
    manifests = [item for item in index["manifests"]
                 if item.get("platform", {}).get("os") == "linux"
                 and item.get("platform", {}).get("architecture") == "amd64"]
    require(len(manifests) == 1 and manifests[0].get("digest") == pin["amd64_manifest"],
            "OCI index does not select the reviewed linux/amd64 manifest")
    manifest = registry_manifest(pin["amd64_manifest"])
    require(manifest.get("mediaType") == "application/vnd.oci.image.manifest.v1+json"
            and manifest.get("config", {}).get("digest") == pin["amd64_config"],
            "linux/amd64 manifest does not use the reviewed config")


def main():
    try:
        mode, source = sys.argv[1:]
        pin = read_pin(source)
        if mode == "match":
            print(f"matched={'true' if pin else 'false'}")
        elif mode == "verify":
            require(pin is not None, "requested source does not have a reviewed Proxx artifact")
            verify_build(pin)
            verify_registry(pin)
            print(f"image={IMAGE}@{pin['index']}")
        else:
            raise ValueError("expected match or verify")
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        message = str(error) if type(error) is ValueError else "invalid evidence"
        print(f"Proxx artifact admission refused: {message}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("Proxx artifact admission refused: trusted evidence unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
