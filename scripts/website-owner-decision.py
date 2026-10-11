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
import sys

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "digitalocean/qualifications/website"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_record(source, directory=RECORDS):
    require(bool(re.fullmatch(r"[0-9a-f]{40}", source)), "Full immutable Website source required")
    return json.loads((directory / (source + ".json")).read_text())


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
    return {"sourceSha": source, "image": image}


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
            "ownerDecision": record["ownerDecision"], "stageReceipt": receipt}


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
    except (ValueError, OSError, TypeError, AttributeError) as error:
        print(f"Website production remains closed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
