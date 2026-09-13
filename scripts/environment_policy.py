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
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Lease timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def hostname(environment: str, service: str) -> str:
    if environment not in {"testing", "staging", "production", "stealth", "yoga"} or not SERVICE.fullmatch(service):
        raise ValueError("Unsupported environment or service")
    return f"{environment}.{service}.promethean.rest"


def testing_admission(candidate: dict, others: list[dict], *, now: datetime) -> dict:
    """A current authorized label claims the slot unless another open label is <=2h old.

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
    try:
        claimed = timestamp(candidate["labeled_at"])
        if claimed > now:
            raise ValueError("future label")
        blockers = []
        for pr in others:
            if pr.get("number") == candidate.get("number") or pr.get("state") != "open" or not pr.get("testing_label"):
                continue
            # Every other open testing label counts, even on a different base branch.
            # Missing/unparseable evidence fails closed; it is never treated as expired.
            age = (now - timestamp(pr["labeled_at"])).total_seconds()
            if age <= LEASE_SECONDS:
                blockers.append(pr["number"])
    except (KeyError, TypeError, ValueError):
        return {"admitted": False, "reason": "missing or invalid label timeline evidence"}
    if blockers:
        return {"admitted": False, "reason": "another testing claim has not expired", "blocking_prs": sorted(blockers)}
    return {"admitted": True, "source_sha": candidate["head_sha"], "pr": candidate["number"],
            "labeled_at": claimed.isoformat(), "environment": "testing"}


def staging_admission(pr: dict) -> dict:
    if not pr.get("merged") or pr.get("base") != "main" or not SHA.fullmatch(pr.get("merge_sha", "")):
        return {"admitted": False, "reason": "staging requires a successful merge into main"}
    return {"admitted": True, "source_sha": pr["merge_sha"], "environment": "staging"}


def production_admission(source_sha: str, integration: dict, e2e: dict, batches: list[dict]) -> dict:
    """Never count stale, duplicated, planned, invalid, timed-out or untested mutants as proof."""
    if not SHA.fullmatch(source_sha):
        return {"admitted": False, "reason": "production requires an immutable source SHA"}
    for name, receipt in [("integration", integration), ("e2e", e2e)]:
        if receipt.get("source_sha") != source_sha or receipt.get("status") != "passed" or receipt.get("assertions", 0) <= 0:
            return {"admitted": False, "reason": f"{name} evidence missing or for a different revision"}
    if len(batches) != MUTATION_BATCHES:
        return {"admitted": False, "reason": "four completed mutation batches are required"}
    seen = set()
    indexes = set()
    for batch in batches:
        if batch.get("source_sha") != source_sha or batch.get("status") != "passed" or batch.get("baseline") != "passed":
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
            if not key or key in seen or mutant.get("status") != "killed" or mutant.get("phase") != "test":
                return {"admitted": False, "reason": "survived, duplicate, invalid or unevaluated mutation"}
            seen.add(key)
    return {"admitted": True, "source_sha": source_sha, "environment": "production", "evaluated_mutations": len(seen)}
