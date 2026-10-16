#!/usr/bin/env python3
"""Pure admission rules for per-service environment promotion. No network or secret handling."""
from __future__ import annotations
from datetime import datetime, timezone
import re

LEASE_SECONDS = 2 * 60 * 60
MUTATION_BATCHES = 4
MUTANTS_PER_BATCH = 250
SHA = re.compile(r"^[0-9a-f]{40}$")
SERVICE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamps must be strings")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Lease timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def hostname(environment: str, service: str) -> str:
    if environment not in {"testing", "staging", "production", "stealth", "yoga"} or not SERVICE.fullmatch(service):
        raise ValueError("Unsupported environment or service")
    return f"{environment}.{service}.promethean.rest"


def testing_admission(candidate: dict, others: list[dict], *, now: datetime) -> dict:
    """The earliest current authorized label holds the slot for the closed two-hour lease.

    Labels are sourced from GitHub timeline events, not the PR update time. Re-labeling
    resets the lease. This function runs inside the service's serialized deployment job.
    """
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    if candidate.get("state") != "open" or candidate.get("base") != "main":
        return {"admitted": False, "reason": "testing requires an open PR targeting main"}
    if not candidate.get("testing_label") or not candidate.get("labeler_is_code_owner"):
        return {"admitted": False, "reason": "a current code-owner testing label is required"}
    if not SHA.fullmatch(candidate.get("head_sha", "")):
        return {"admitted": False, "reason": "candidate needs an immutable head SHA"}
    if candidate.get("authorization_sha") != candidate["head_sha"]:
        return {"admitted": False, "reason": "a submitted code-owner testing authorization for this exact head is required"}
    try:
        claimed = timestamp(candidate["labeled_at"])
        if claimed > now:
            raise ValueError("future label")
        if (now - claimed).total_seconds() > LEASE_SECONDS:
            return {"admitted": False, "reason": "candidate testing claim has expired"}
        blockers = []
        for pr in others:
            if pr.get("number") == candidate.get("number") or pr.get("state") != "open" or not pr.get("testing_label"):
                continue
            # Earlier claims count even on a different base branch; ties fail closed.
            # Missing/unparseable evidence fails closed; it is never treated as expired.
            other_claimed = timestamp(pr["labeled_at"])
            if other_claimed > now:
                raise ValueError("future label")
            age = (now - other_claimed).total_seconds()
            if age <= LEASE_SECONDS and other_claimed <= claimed:
                blockers.append(pr["number"])
    except (KeyError, TypeError, ValueError):
        return {"admitted": False, "reason": "missing or invalid label timeline evidence"}
    if blockers:
        return {"admitted": False, "reason": "another testing claim has not expired", "blocking_prs": sorted(blockers)}
    return {"admitted": True, "source_sha": candidate["head_sha"], "pr": candidate["number"],
            "labeled_at": claimed.isoformat(), "environment": "testing"}


def testing_authorization(reviews: list[dict], label: dict, head_sha: str, *, now: datetime) -> dict | None:
    """A deliberate native COMMENT grants testing only for its immutable commit.

    It is operational authorization, never a merge approval or reviewer vote.
    The collector separately establishes current CODEOWNERS coverage.
    """
    actor = label.get("actor", {})
    if (actor.get("type") != "User" or type(actor.get("id")) is not int or actor["id"] <= 0
            or not isinstance(actor.get("login"), str) or not SHA.fullmatch(head_sha)):
        return None
    try:
        labeled_at = timestamp(label["created_at"])
        if labeled_at > now:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    for review in reversed(reviews):
        user = review.get("user", {})
        if (review.get("state") != "COMMENTED" or review.get("commit_id") != head_sha
                or review.get("body") != f"Authorize testing deployment of {head_sha}."
                or type(review.get("id")) is not int or review["id"] <= 0
                or user.get("type") != "User" or user.get("id") != actor["id"]
                or str(user.get("login", "")).lower() != actor["login"].lower()):
            continue
        try:
            submitted_at = timestamp(review["submitted_at"])
            if submitted_at > labeled_at:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        return {"review_id": review["id"], "head_sha": head_sha, "user_id": actor["id"],
                "login": actor["login"], "submitted_at": submitted_at.isoformat(),
                "label_id": label.get("id"), "labeled_at": labeled_at.isoformat()}
    return None


def staging_admission(pr: dict) -> dict:
    if not pr.get("merged") or pr.get("base") != "main" or not SHA.fullmatch(pr.get("merge_sha", "")):
        return {"admitted": False, "reason": "staging requires a successful merge into main"}
    return {"admitted": True, "source_sha": pr["merge_sha"], "environment": "staging"}


def clean_receipt_markers(receipt: dict) -> bool:
    """Optional markers must explicitly describe no timeout and no warning entries."""
    warnings = receipt.get("warnings", [])
    return receipt.get("timed_out", False) is False and isinstance(warnings, list) and not warnings


def production_admission(source_sha: str, integration: dict, e2e: dict, batches: list[dict]) -> dict:
    """Never count stale, duplicated, planned, invalid, timed-out or untested mutants as proof."""
    if not SHA.fullmatch(source_sha):
        return {"admitted": False, "reason": "production requires an immutable source SHA"}
    for name, receipt in [("integration", integration), ("e2e", e2e)]:
        if (receipt.get("source_sha") != source_sha or receipt.get("status") != "passed"
                or receipt.get("assertions", 0) <= 0 or not clean_receipt_markers(receipt)):
            return {"admitted": False, "reason": f"{name} evidence missing, invalid or for a different revision"}
    if len(batches) != MUTATION_BATCHES:
        return {"admitted": False, "reason": "four completed mutation batches are required"}
    seen = set()
    indexes = set()
    for batch in batches:
        if (batch.get("source_sha") != source_sha or batch.get("status") != "passed"
                or batch.get("baseline") != "passed" or not clean_receipt_markers(batch)):
            return {"admitted": False, "reason": "mutation batch baseline, status or revision is invalid"}
        index = batch.get("batch_index")
        if index not in range(MUTATION_BATCHES) or index in indexes:
            return {"admitted": False, "reason": "mutation batch indexes are incomplete or duplicated"}
        indexes.add(index)
        mutants = batch.get("mutants", [])
        if len(mutants) < MUTANTS_PER_BATCH:
            return {"admitted": False, "reason": "each mutation batch requires at least 250 evaluated source mutations"}
        for mutant in mutants:
            key = mutant.get("fingerprint")
            if (not key or key in seen or mutant.get("status") != "killed"
                    or mutant.get("phase") != "test" or not clean_receipt_markers(mutant)):
                return {"admitted": False, "reason": "survived, duplicate, invalid or unevaluated mutation"}
            seen.add(key)
    return {"admitted": True, "source_sha": source_sha, "environment": "production", "evaluated_mutations": len(seen)}
