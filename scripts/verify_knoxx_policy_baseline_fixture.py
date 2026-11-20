#!/usr/bin/env python3
"""Check one historical, inactive baseline fixture. No host or network operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "digitalocean/qualifications/knoxx/public-policy-baseline-20261120.json"
ORIGINAL_RECEIPT = "094b9689b325b7864718634b28c003a0dcbac0c38989c479eb965ad2f81e6a57"
ORIGINAL_CONFIGS = (
    "a07aa81b9f91b7252989f4d89f01910117c90cf4e3fe51b4c7450e1b02f414eb",
    "a5fbff211feb711e203ceee388a224e8601aa1898399d6b6357c6eeecf3415d9",
)
SOURCE_FILES = {
    "digitalocean/services/axxium/unified-mailbox-production-plan.json",
    "digitalocean/services/axxium/active-mailbox-upgrade-production-plan.json",
    "digitalocean/services/axxium/mailbox-events-production-plan.json",
    "digitalocean/services/axxium-work-pilot/ingress/active.caddy",
    "digitalocean/services/axxium-work-pilot/reopen-release-plan.json",
    "digitalocean/services/knoxx/public-policy/knoxx-public-policy.caddy",
    "scripts/knoxx_public_policy_release.py",
    "digitalocean/scripts/check-knoxx-public-policy-hold.py",
}
RUNS = {
    42882186202: "e09dd8f8f5663833db95099dea6b31835da6f17a",
    42885343175: "9f7fa75e43d235f4e5da82fc3c9c59eeb6f77d37",
    42887424799: "9f7fa75e43d235f4e5da82fc3c9c59eeb6f77d37",
    42911424765: "8fce0077baa6c2450ddb39d4633354b7e965a9a8",
    44221313306: "67791fd1fd5f9f113f45f22237e686d3ddc754e7",
    44352129130: "96408a1c82d318a7ccdbe0e9e143195b0140befe",
}


class FixtureRefused(ValueError):
    """Only fixed, credential-free fixture diagnostics leave the checker."""


def require(value, reason):
    if not value:
        raise FixtureRefused(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def unique_keys(pairs):
    value = dict(pairs)
    require(len(value) == len(pairs), "Duplicate fixture field")
    return value


def read_json(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique_keys)


def verify(certificate, root=ROOT):
    """Verify captured proof consistency; this does not authorize a live change."""
    require(certificate["schema"] == "knoxx-public-policy-additive-baseline-fixture/v1"
            and certificate["phase"] == "source-only-prepared"
            and certificate["production_hold_cleared"] is False
            and certificate["live_authorization"] is False, "Fixture cannot clear or activate a hold")
    require(certificate["host"] == "knoxx.promethean.rest"
            and certificate["public_mcp_origin"] == "https://knoxx.promethean.rest/mcp",
            "The existing production MCP origin must be preserved")
    require(set(certificate["protected_source_files"]) == SOURCE_FILES,
            "Unexpected protected source selection")
    for name, expected in certificate["protected_source_files"].items():
        require(digest((root / name).read_bytes()) == expected, "Protected repository bytes changed")

    original = certificate["original_v1"]
    require(original["receipt_sha256"] == ORIGINAL_RECEIPT
            and original["version_present"] is False and original["default_version"] == 1
            and original["phase"] == "active" and original["native_artifact_config_hashes_match"] is True
            and (original["native_config_sha256"], original["active_config_sha256"]) == ORIGINAL_CONFIGS
            and original["run_id"] == 42896505254 and original["artifact_id"] == 13801450441
            and original["controller_sha"] == "22da621059a947a4776ae22638bf93416051f1f7",
            "The exact original v1 seal must remain intact")
    historical = certificate["historical_reconstruction"]
    require(historical["excluded_managed_names"] == ["axxium-work-pilot.caddy"]
            and historical["remaining_fragment_changes"] == []
            and all(historical[name] == original[name]
                    for name in ("native_config_sha256", "active_config_sha256")),
            "Both historical whole-config identities must be reconstructed")
    current = certificate["observed_current"]
    require(current["v1_receipt_unchanged"] is True
            and current["containers_equal_original_ids_and_images"] is True
            and current["live_equals_startup"] is True, "Observed incumbent identity changed")
    configs = current["config_sha256"]
    require(configs["active"] == configs["current"] == configs["startup"]
            and len({configs["native"], configs["active"], configs["bridge"]}) == 3,
            "Current startup/live/bridge identities conflict")
    managed = current["managed"]
    require(len(managed) == 6 and len({row["name"] for row in managed}) == 6
            and managed == sorted(managed, key=lambda row: row["name"])
            and canonical(managed) == current["managed_sha256"], "Managed fragment identity changed")
    by_name = {row["name"]: row["sha256"] for row in managed}
    for name, source in (
        ("axxium-work-pilot.caddy", "digitalocean/services/axxium-work-pilot/ingress/active.caddy"),
        ("knoxx-public-policy.caddy", "digitalocean/services/knoxx/public-policy/knoxx-public-policy.caddy"),
    ):
        require(by_name[name] == certificate["protected_source_files"][source],
                "Installed route differs from its protected source")

    unified = read_json(root / "digitalocean/services/axxium/unified-mailbox-production-plan.json")
    active = read_json(root / "digitalocean/services/axxium/active-mailbox-upgrade-production-plan.json")
    events = read_json(root / "digitalocean/services/axxium/mailbox-events-production-plan.json")
    require(historical["archived_base_sha256"] == unified["ingress"]["incumbent"]["root_sha256"],
            "Historical host archive is outside its owner plan")
    require(all(current["base_sha256"] == plan["ingress"]["live"]["root_sha256"]
                and current["managed_sha256"] == plan["expected"]["managed_caddy_sha256"]
                for plan in (unified, active, events)), "Current ingress is outside protected owner plans")
    host_files = {row["sha256"] for row in certificate["host_receipts"]}
    require(active["incumbent"]["receipt_sha256"] in host_files
            and events["incumbent"]["receipt_sha256"] in host_files,
            "Protected owner receipt chain is incomplete")

    artifacts = certificate["native_actions_artifacts"]
    require(len(artifacts) == len(RUNS) and {row["runId"] for row in artifacts} == set(RUNS),
            "Native owner run selection changed")
    host_hashes = {row["canonicalReceiptSha256"] for row in certificate["host_canonical_receipts"]}
    host_hashes.add(certificate["retained_work_reopen"]["canonicalReceiptSha256"])
    for row in artifacts:
        require(row["artifactZipHashVerified"] is True and row["head_sha"] == RUNS[row["runId"]]
                and row["head_branch"] == "main" and row["event"] == "workflow_dispatch"
                and row["conclusion"] == "success" and row["attempt"] == 1
                and row["workflow_path"] == ".github/workflows/deploy-stack.yml",
                "Native protected owner custody differs")
        # Teardown removed the Work route before v1; its historical artifact
        # is retained, while the host's current selected receipt is reopen.
        if row["runId"] != 42887424799:
            require(row["canonicalReceiptSha256"] in host_hashes,
                    "Native artifact and host owner receipt differ")
    repaired = next(row for row in artifacts if row["runId"] == 42885343175)
    require(repaired["receiptMetadata"]["updated_route_sha256"] == by_name["axxium-work-pilot.caddy"],
            "Work route repair does not bind the current exact fragment")

    mcp = certificate["mcp_continuity"]
    require(mcp["historical_knoxx_site_sha256"] == mcp["current_knoxx_site_sha256"]
            and mcp["historical_mcp_route_sha256"] == mcp["current_mcp_route_sha256"]
            and mcp["anonymous_http_status"] == 401 and mcp["exact_bearer_resource_challenge"] is True
            and mcp["grant_or_session_used"] is False and mcp["credential_or_auth_state_modified"] is False,
            "Existing production MCP continuity is unqualified")
    public = certificate["public_before_retirement"]
    for path, filename in (("/", "homepage.html"), ("/privacy.html", "privacy.html")):
        expected = digest((root / "digitalocean/services/knoxx/public-policy" / filename).read_bytes())
        require(public[path] == {"status": 200, "type": "text/html", "sha256": expected}
                and public["HEAD " + path] == {"status": 200, "type": "text/html", "sha256": digest(b"")},
                "Public policy bytes differ from the protected bundle")
    return {"status": "fixture-verified", "original_receipt_sha256": ORIGINAL_RECEIPT,
            "original_receipt_modified": False, "production_hold_cleared": False,
            "live_authorization": False, "native_owner_runs": len(RUNS)}


def main():
    try:
        result = verify(read_json(FIXTURE))
    except (FixtureRefused, KeyError, TypeError, ValueError, OSError):
        print(json.dumps({"status": "refused", "reason": "Inactive fixture or protected proof differs"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
