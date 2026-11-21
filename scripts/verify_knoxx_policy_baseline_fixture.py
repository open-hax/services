#!/usr/bin/env python3
"""Check one historical, inactive baseline fixture. No host or network operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "digitalocean/qualifications/knoxx/public-policy-baseline-20261120.json"
ORIGINAL_RECEIPT = "094b9689b325b7864718634b28c003a0dcbac0c38989c479eb965ad2f81e6a57"
ORIGINAL_ARTIFACT_SHA256 = "f9aabb7addffd279a208cd19729a1acc35b4c50918da84eefaa4cffc711d6570"
ORIGINAL_CONFIGS = (
    "a07aa81b9f91b7252989f4d89f01910117c90cf4e3fe51b4c7450e1b02f414eb",
    "a5fbff211feb711e203ceee388a224e8601aa1898399d6b6357c6eeecf3415d9",
)
CURRENT_ACTIVE = "79ebbd17a6cb506658806704e6480f08746241610aa072dac2d0a7b6428cfa55"
CURRENT_CONFIGS = {
    "native": "41fcf4d707554cf008845c184b6d116e6305b898fcb4139860541040b25540bf",
    "active": CURRENT_ACTIVE,
    "bridge": "b22546c22c7ef38b970842f00e7f938ffcf67b04349cd1746450ecfdca22da08",
    "current": CURRENT_ACTIVE,
    "startup": CURRENT_ACTIVE,
}
KNOXX_SITE_SHA256 = "065e43d874fdeedc7424cbdb612d0057b8064e8a79af29690288f852f007e441"
MCP_ROUTE_SHA256 = "5fd598184a7fd34971d64432b1c4a523c322ffe0beb94f79d8f6b6efc88eadf6"
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
WORK_LIVE = "/srv/open-hax/services/axxium-work-pilot/live/"
AXXIUM_OPS = "/srv/open-hax/state/axxium/operations/"
UNIFIED_RECEIPT = AXXIUM_OPS + "d2f8dc8425f86f51a8450a0cae0b79a042694c64/deployment.json"
REOPEN_RECEIPT = WORK_LIVE + "reopen/active.json"


class OwnerArtifact(NamedTuple):
    controller: str
    id: int
    archive_sha256: str
    receipt_sha256: str
    canonical_sha256: str
    metadata_sha256: str
    host_path: str | None
    host_sha256: str | None


# Each immutable native run owns one artifact and one exact host receipt.
# The historical teardown has no current selected host receipt.
RUNS = {
    42882186202: OwnerArtifact(
        "e09dd8f8f5663833db95099dea6b31835da6f17a", 13794247860,
        "f2161780e1453de458f794273031f3f767fa4cb621231ef14153a9cbff73c893",
        "385a3ec7ead7641479876352f47e89ebf88ffb8e631b82a584860ef98c1f9349",
        "cd059959386296289eb04fee894641fd8833e3aff629ae8f816bf9845ef76ecc",
        "7a2cd2415ac594c482b0202a4a622a0568203e5951ea84a761f91ba6573d4cd4",
        WORK_LIVE + "active.json", "385a3ec7ead7641479876352f47e89ebf88ffb8e631b82a584860ef98c1f9349"),
    42885343175: OwnerArtifact(
        "9f7fa75e43d235f4e5da82fc3c9c59eeb6f77d37", 13794769172,
        "e457cbb8d46f1cdcff9d0923f42a863d717621b7fb9a6d89a24a14b14a29d8f9",
        "977ce43ef1eb92bca6a737b6fe7de1edbe6465c1b4ee95967d433ca965de78eb",
        "447b471e04bd53b29ed0c13bea95ce9c2a7b6901b9f43e330802a7d1fdd8214c",
        "79eb35c4fd297bf7d69a58de49dbd928813874176bcb53b78b43bfabd7157986",
        WORK_LIVE + "ingress-repair.json", "977ce43ef1eb92bca6a737b6fe7de1edbe6465c1b4ee95967d433ca965de78eb"),
    42887424799: OwnerArtifact(
        "9f7fa75e43d235f4e5da82fc3c9c59eeb6f77d37", 13796781885,
        "7661533e2e4aaf6b1642f145fdc7610022979c803e68ddef400068fc5aca3ff2",
        "a88c6b1fbd74f11dfa238c859038af0a046f43e220edfd1ad61bdc1b63267c3a",
        "eacf5baf9118216be05394f1de8b6fdc8e8a7916177940817596ade13ad45c07",
        "e757e1d5a6acd7d01841eb5fa23ea8e4e914cac7afae1bd1790abb2e42987345", None, None),
    42911424765: OwnerArtifact(
        "8fce0077baa6c2450ddb39d4633354b7e965a9a8", 13807691701,
        "e4d65dc113483b5bb5007f6068d2deab5dfb7c7f217c3592fa73bbaf5af54e14",
        "7cd623f11d3e49f43bdf50cb91d8a4c19f13348853412abaf20ef2b2b2ef37bd",
        "2c7df88485fd4b82d99d8a9f4c3473b55b96d2aebc486c0a0f20ec3722e03f4b",
        "a714d283ff37937857466bb61feab1cc26b93fb1404a5d380beb2f6da945a95c",
        REOPEN_RECEIPT, "7cd623f11d3e49f43bdf50cb91d8a4c19f13348853412abaf20ef2b2b2ef37bd"),
    44221313306: OwnerArtifact(
        "67791fd1fd5f9f113f45f22237e686d3ddc754e7", 14390094459,
        "9facb986f3ae80bd247b47c5c768ccf603a0d6bdd8358ee4df4347d5ca684bd9",
        "854d53abf8d83d4536fa40c6c88d3f7e18a0b2bec6473ec3a39d37cf6177723c",
        "eb0e9369e1b19f3ccad871fa1e0e1ec5825c9640d8ffc2bce53c3b2abdfa28fb",
        "a2730c451db0818ff6af2c12a5aa9a99137f06783065e1e4cbecbb537e58ea94",
        AXXIUM_OPS + "61b1cea021b13fac539232af23d6714f2287f8a4-active-mailbox/deployment.json",
        "62fae1de328f00ffcbe93013686d8d2e3a71eaa4ff2cd2a255c478ce2fa537e3"),
    44352129130: OwnerArtifact(
        "96408a1c82d318a7ccdbe0e9e143195b0140befe", 14449935557,
        "7d55c863363193c8f6f753777bfd3e8fde5e3e7e60bb45b9c07b13c3d0778191",
        "4426320cc0d51371c48104541be38b84d636df8e29c2503130bc52883dbdc56c",
        "46b441176b692608f034fa04ef7226a848e10fdeec234578e5a07e46516b7ca5",
        "3e317043d71b2797c81163cd8d53b1e8d663aa53ed7b888c5de7fc99af412c13",
        AXXIUM_OPS + "61b1cea021b13fac539232af23d6714f2287f8a4-mailbox-events/deployment.json",
        "1dd5c1dba746bdc9ea2ecfef65c08a9e2a67db5c03373e76ee2eab7ff3a2d75b"),
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
            and original["artifact_sha256"] == ORIGINAL_ARTIFACT_SHA256
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
    require(current["config_sha256"] == CURRENT_CONFIGS, "Captured current Caddy identities differ")
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
    artifacts = certificate["native_actions_artifacts"]
    require(len(artifacts) == len(RUNS) and {row["runId"] for row in artifacts} == set(RUNS),
            "Native owner run selection changed")
    hosts = {row["path"]: row for row in certificate["host_receipts"]}
    canonical_hosts = {row["path"]: row for row in certificate["host_canonical_receipts"]}
    expected_paths = {binding.host_path for binding in RUNS.values()
                      if binding.host_path not in (None, REOPEN_RECEIPT)} | {UNIFIED_RECEIPT}
    require(set(hosts) == set(canonical_hosts) == expected_paths
            and len(hosts) == len(certificate["host_receipts"])
            and len(canonical_hosts) == len(certificate["host_canonical_receipts"]),
            "Host receipt paths are ambiguous")
    for path, row in hosts.items():
        require(canonical_hosts[path]["fileSha256"] == row["sha256"],
                "Canonical host receipt was attached to another file")
    require(hosts[UNIFIED_RECEIPT]["sha256"] == active["incumbent"]["receipt_sha256"]
            and hosts[RUNS[44221313306].host_path]["sha256"] == events["incumbent"]["receipt_sha256"],
            "Protected owner receipt chain is incomplete")
    reopened = certificate["retained_work_reopen"]
    require(reopened["path"] == REOPEN_RECEIPT, "Retained Work receipt path changed")
    hosts[REOPEN_RECEIPT] = reopened
    canonical_hosts[REOPEN_RECEIPT] = reopened
    for row in artifacts:
        binding = RUNS[row["runId"]]
        require(row["artifactZipHashVerified"] is True and row["head_sha"] == binding.controller
                and row["artifactId"] == binding.id and row["nativeArtifactSha256"] == binding.archive_sha256
                and row["receiptFileSha256"] == binding.receipt_sha256
                and row["canonicalReceiptSha256"] == binding.canonical_sha256
                and row["head_branch"] == "main" and row["event"] == "workflow_dispatch"
                and row["conclusion"] == "success" and row["attempt"] == 1
                and row["workflow_path"] == ".github/workflows/deploy-stack.yml",
                "Native protected owner custody differs")
        require(canonical(row["receiptMetadata"]) == binding.metadata_sha256,
                "Native selected receipt metadata differs from the verified artifact")
        # Teardown removed the Work route before v1; its historical artifact
        # is retained, while the host's current selected receipt is reopen.
        if binding.host_path is not None:
            host_path = binding.host_path
            require(hosts[host_path]["sha256"] == binding.host_sha256
                    and canonical_hosts[host_path]["canonicalReceiptSha256"] == binding.canonical_sha256,
                    "Native artifact and host owner receipt differ")
            metadata = dict(hosts[host_path]["metadata"])
            if host_path.startswith(AXXIUM_OPS):
                metadata["operation"] = str(Path(host_path).parent)
            require(row["receiptMetadata"] == metadata, "Native artifact and same-path host metadata differ")
    repaired = next(row for row in artifacts if row["runId"] == 42885343175)
    require(repaired["receiptMetadata"]["updated_route_sha256"] == by_name["axxium-work-pilot.caddy"],
            "Work route repair does not bind the current exact fragment")

    mcp = certificate["mcp_continuity"]
    require(mcp["historical_knoxx_site_sha256"] == mcp["current_knoxx_site_sha256"] == KNOXX_SITE_SHA256
            and mcp["historical_mcp_route_sha256"] == mcp["current_mcp_route_sha256"] == MCP_ROUTE_SHA256
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
