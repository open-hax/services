#!/usr/bin/env python3
"""Admit only a reviewed owner decision for an exact Website image.

This is an operational controller, not a mutation-evidence verifier. Records
are trusted Services source; dispatch inputs cannot create or approve them.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "digitalocean/qualifications/website"
# Conservative public operational closure, including authored transitive helpers.
# Decision records are outside it so approving a record cannot hash itself.
INPUT_PREFIXES = (".github/", "scripts/", "digitalocean/hosts/", "digitalocean/known_hosts/",
                  "digitalocean/lib/", "digitalocean/scripts/", "digitalocean/services/")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_record(source, directory=RECORDS):
    require(bool(re.fullmatch(r"[0-9a-f]{40}", source)), "Full immutable Website source required")
    return json.loads((directory / (source + ".json")).read_text())


def inputs_sha256(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_input(path):
    return path.startswith(INPUT_PREFIXES)


def deployment_inputs():
    """Hash only tracked public operational bytes, rejecting links before reads."""
    indexed = subprocess.run(["git", "ls-files", "--stage", "--full-name", "-z"],
                             cwd=ROOT, capture_output=True, check=True).stdout.decode()
    files = []
    for item in filter(None, indexed.split("\0")):
        metadata, path = item.split("\t", 1)
        if not public_input(path):
            continue
        mode, _, stage = metadata.split()
        require(mode in ("100644", "100755") and stage == "0",
                "Deployment inputs must be regular tracked files without merge conflicts")
        relative = Path(path)
        require(not relative.is_absolute() and all(part not in ("", ".", "..") for part in relative.parts),
                "Unsafe deployment input path")
        public_env_example = (relative.name == ".env.example" or
                              relative.name.startswith(".env.") and relative.name.endswith(".example"))
        private_env = relative.name == ".env" or relative.name.startswith(".env.") and not public_env_example
        require(not private_env and relative.name not in ("id_rsa", "id_ed25519", "cloudflare.ini") and
                relative.suffix not in (".env", ".pem", ".key", ".crt", ".p12", ".pfx", ".log") and
                not any(part in ("secrets", "runtime", "state") for part in relative.parts[:-1]),
                "Private environment, key or runtime files cannot enter the public input binding")
        target = ROOT / relative
        for current in (target, *target.parents):
            if current == ROOT:
                break
            require(not current.is_symlink(), "Deployment inputs cannot traverse symlinks")
        require(stat.S_ISREG(target.stat().st_mode), "Deployment inputs must be regular files")
        files.append({"path": path, "mode": mode,
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    files.sort(key=lambda entry: entry["path"])
    require(files and len({entry["path"] for entry in files}) == len(files),
            "Deployment input selection is empty or ambiguous")
    return {"schemaVersion": 1, "files": files, "sha256": inputs_sha256(files)}


def verify_deployment_inputs(record):
    binding = record.get("deploymentInputs")
    require(isinstance(binding, dict) and set(binding) == {"schemaVersion", "files", "sha256"}
            and type(binding["schemaVersion"]) is int and binding["schemaVersion"] == 1,
            "Reviewed deployment input binding is absent or unsupported")
    files = binding["files"]
    require(isinstance(files, list) and files, "Reviewed deployment input selection is empty")
    for entry in files:
        require(isinstance(entry, dict) and set(entry) == {"path", "mode", "sha256"} and
                isinstance(entry["path"], str) and public_input(entry["path"]) and
                entry["mode"] in ("100644", "100755") and
                isinstance(entry["sha256"], str) and bool(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])),
                "Reviewed deployment input entry is malformed")
    paths = [entry["path"] for entry in files]
    require(paths == sorted(set(paths)), "Reviewed deployment input paths must be sorted and unique")
    require(binding["sha256"] == inputs_sha256(files), "Reviewed deployment input aggregate is invalid")
    require(binding == deployment_inputs(),
            "Public deployment inputs differ from the exact owner-approved configuration")
    return binding["sha256"]


def select(record, source, image):
    require(record.get("schemaVersion") == 1 and record.get("service") == "website",
            "Unsupported candidate decision record")
    require(bool(re.fullmatch(r"ghcr\.io/open-hax/website:" + re.escape(source) + r"@sha256:[0-9a-f]{64}", image)),
            "Source-bound immutable Website image required")
    require(source == record.get("sourceSha") and image == record.get("image"),
            "Source or digest differs from the reviewed candidate")
    decision = record.get("ownerDecision", {})
    require(decision.get("status") == "approved" and decision.get("owner") == "riatzukiza",
            "Explicit repository-owner decision is pending or absent")
    require(all(isinstance(decision.get(k), str) and decision[k].strip()
                for k in ("approvedAt", "question", "answer", "scope")), "Owner decision evidence is incomplete")
    datetime.datetime.fromisoformat(decision["approvedAt"].replace("Z", "+00:00"))
    return {"sourceSha": source, "image": image,
            "deploymentInputsSha256": verify_deployment_inputs(record)}


def qualify(record, source, image, services, nginx, receipt):
    result = select(record, source, image)
    require(bool(re.fullmatch(r"[0-9a-f]{40}", services)), "Current Services commit required")
    require(nginx == record.get("nginxSha"), "Serving configuration differs from the reviewed candidate")
    expected = {"service": "website", "phase": "staging", "health": "success",
                "sourceSha": source, "image": image, "servicesSha": services, "nginxSha": nginx,
                "composeProject": "website-staging", "publicHost": "staging.website.promethean.rest"}
    require(all(receipt.get(k) == value for k, value in expected.items()),
            "Fresh staging proof differs from this locked candidate")
    return {**result, "servicesSha": services, "nginxSha": nginx,
            "qualification": "owner-decision", "mutationCampaignPassed": False,
            "ownerDecision": record["ownerDecision"], "deploymentInputs": record["deploymentInputs"],
            "stageReceipt": receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("select", "qualify"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--services")
    args = parser.parse_args()
    try:
        record = read_record(args.source)
        result = select(record, args.source, args.image) if args.operation == "select" else qualify(
            record, args.source, args.image, args.services,
            hashlib.sha256((ROOT / "digitalocean/services/website/nginx.conf").read_bytes()).hexdigest(),
            json.load(sys.stdin))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        print(f"Website production remains closed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
