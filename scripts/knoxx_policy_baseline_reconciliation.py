#!/usr/bin/env python3
"""Read-only reconciliation of the fixed historical Knoxx policy baseline."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys

if __package__:
    from . import knoxx_public_policy_release as policy
    from . import verify_knoxx_policy_baseline_fixture as fixture
else:
    import knoxx_public_policy_release as policy
    import verify_knoxx_policy_baseline_fixture as fixture

SCHEMA = "knoxx-public-policy-baseline-reconciliation/v1"
CERTIFICATE_SHA256 = "cd4d8a8b687d558a506b77157774d8f776b03ed9ac195d1c01e33b35b7dc7ec5"
MCP_CHALLENGE = f'Bearer realm="mcp", resource_metadata="https://{policy.HOST}/.well-known/oauth-protected-resource"'


def load_certificate():
    raw = policy.read_file(fixture.FIXTURE)
    policy.require(policy.digest(raw) == CERTIFICATE_SHA256, "reviewed baseline certificate differs")
    certificate = json.loads(raw, object_pairs_hook=policy.unique_keys)
    fixture.verify(certificate)
    return certificate


def read_private(path):
    policy.directory(path.parent)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise policy.Refused("unsafe historical policy evidence file") from None
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        policy.require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid()
                       and not metadata.st_mode & 0o077, "unsafe historical policy evidence file")
        raw = source.read(policy.LIMIT + 1)
    policy.require(len(raw) <= policy.LIMIT, "historical policy evidence exceeds size limit")
    return raw


def verify_route_owners(certificate):
    selected = [(row["path"], row["fileSha256"], row["canonicalReceiptSha256"])
                for row in certificate["host_canonical_receipts"]]
    reopen = certificate["retained_work_reopen"]
    selected.append((reopen["path"], reopen["sha256"], reopen["canonicalReceiptSha256"]))
    for path, expected_bytes, expected_canonical in selected:
        raw = read_private(Path(path))
        policy.require(policy.digest(raw) == expected_bytes
                       and fixture.canonical(json.loads(raw, object_pairs_hook=policy.unique_keys)) == expected_canonical,
                       "selected host route-owner receipt differs")
    archived = certificate["historical_reconstruction"]
    base = read_private(Path(archived["archived_host_path"]))
    policy.require(policy.digest(base) == archived["archived_base_sha256"],
                   "selected archived Caddyfile differs")
    return base


def verify_managed(host, expected):
    base = policy.read_file(host.base)
    policy.require(policy.digest(base) == expected["base_sha256"], "current Caddyfile differs")
    names = sorted(path.name for path in host.managed.glob("*.caddy"))
    policy.require(names == [row["name"] for row in expected["managed"]], "managed Caddy manifest differs")
    actual = [{"name": name, "sha256": policy.digest(policy.read_file(host.managed / name))}
              for name in names]
    policy.require(actual == expected["managed"] and fixture.canonical(actual) == expected["managed_sha256"],
                   "managed Caddy bytes differ")
    return base


def historical_configs(host, base, snippet, excluded):
    lines, index, names = policy.compose_candidate(base, host.managed)
    policy.require(all(name in names for name in excluded), "historical managed exclusion differs")
    imports = [(name, f"import {policy.MOUNT}/{name}\n".encode()) for name in names if name not in excluded]
    native = [entry for name, entry in imports if name != policy.SNIPPET]
    active = [snippet + b"\n" if name == policy.SNIPPET else entry for name, entry in imports]
    return (host.adapt(b"".join(lines[:index] + native + lines[index + 1:])),
            host.adapt(b"".join(lines[:index] + active + lines[index + 1:])))


def mcp_identities(config):
    sites = [route for server in policy.routes(config).values() for route in server["routes"]
             if route.get("match") == [{"host": [policy.HOST]}]]
    policy.require(len(sites) == 1, "native Knoxx site identity differs")
    site = sites[0]
    transport = [route for route in policy.child_routes(site)
                 if route.get("match") == [{"path": ["/mcp*"]}]]
    policy.require(len(transport) == 1, "native Knoxx MCP handler identity differs")
    return policy.config_digest(site), policy.config_digest(transport[0])


def verify_mcp_challenge():
    opener = policy.build_opener(policy.ProxyHandler({}), policy.NoRedirect())
    query = policy.Request(f"https://{policy.HOST}/mcp", headers={"Accept": "text/event-stream"})
    try:
        response = opener.open(query, timeout=12)
    except policy.HTTPError as error:
        response = error
    with response:
        policy.require(response.code == 401 and response.headers.get_all("WWW-Authenticate") == [MCP_CHALLENGE],
                       "public Knoxx MCP challenge differs")


def observe(host, plan, plan_hash, artifacts, controller_sha, certificate):
    original = certificate["original_v1"]
    received = read_private(host.receipt)
    policy.require(policy.digest(received) == original["receipt_sha256"], "original v1 policy receipt differs")
    prior = json.loads(received, object_pairs_hook=policy.unique_keys)
    policy.require(prior.get("version", 1) == 1 and prior.get("phase") == "active"
                   and prior.get("controller_sha") == original["controller_sha"]
                   and prior.get("plan_sha256") == original["plan_sha256"] == plan_hash
                   and prior.get("incumbent_config_sha256") == original["native_config_sha256"]
                   and prior.get("candidate_config_sha256") == original["active_config_sha256"],
                   "original v1 policy receipt bindings differ")
    initial = host.identities(plan)
    policy.require(all(initial[name][field] == prior["containers"][name][field]
                       for name in (policy.FRONTEND, policy.BACKEND, policy.CADDY) for field in ("Id", "Image")),
                   "original policy container or image identity differs")
    policy.require(policy.digest(host.index()) == plan["incumbent_index_sha256"], "incumbent frontend index differs")
    _, disk = policy.exact_disk(host, artifacts)
    policy.require(all(value is not None for value in disk.values()) and host.shadow_absent(),
                   "active policy artifacts or shadow inventory differ")

    archive = verify_route_owners(certificate)
    before = certificate["observed_current"]
    current_base = verify_managed(host, before)
    snippet = artifacts[policy.SNIPPET]
    archived_native, archived_active = historical_configs(host, archive, snippet,
                                                         certificate["historical_reconstruction"]["excluded_managed_names"])
    policy.require(policy.config_digest(archived_native) == original["native_config_sha256"]
                   and policy.config_digest(archived_active) == original["active_config_sha256"],
                   "fresh historical Caddy adaptation differs from v1")
    native, active = host.configs(snippet)
    same_native, bridge = host.configs(policy.retirement_bridge(current_base, snippet))
    policy.verify_retirement_bridge(native, active, bridge)
    live, startup = host.current(), host.startup_config()
    configs = {name: policy.config_digest(config) for name, config in
               (("native", native), ("active", active), ("bridge", bridge), ("current", live), ("startup", startup))}
    policy.require(configs == before["config_sha256"] and policy.config_digest(same_native) == configs["native"],
                   "fresh current, live or startup Caddy identities differ")
    mcp = certificate["mcp_continuity"]
    expected = (mcp["historical_knoxx_site_sha256"], mcp["historical_mcp_route_sha256"])
    policy.require(all(mcp_identities(config) == expected for config in
                       (archived_native, archived_active, native, active, live, startup))
                   and mcp_identities(bridge)[1] == expected[1], "Knoxx site or MCP continuity differs")

    public = host.public()
    policy.verify_public(prior["public_before"], public, artifacts)
    policy.require(public == certificate["public_before_retirement"], "fixed public policy readback differs")
    verify_mcp_challenge()
    policy.require(read_private(host.receipt) == received and verify_route_owners(certificate) == archive
                   and verify_managed(host, before) == current_base and host.identities(plan) == initial
                   and policy.exact_disk(host, artifacts)[1] == disk and host.shadow_absent()
                   and policy.digest(host.index()) == plan["incumbent_index_sha256"]
                   and policy.config_digest(host.current()) == configs["current"]
                   and policy.config_digest(host.startup_config()) == configs["startup"],
                   "protected policy observations changed during reconciliation")
    return {"schema": SCHEMA, "result": "baseline_reconciled", "controller_sha": controller_sha,
            "certificate_sha256": CERTIFICATE_SHA256, "active_receipt_sha256": original["receipt_sha256"],
            "plan_sha256": plan_hash, "configs": configs, "knoxx_site_sha256": expected[0], "mcp_route_sha256": expected[1],
            "public_mcp_origin": certificate["public_mcp_origin"], "anonymous_mcp_status": 401,
            "mcp_bearer_challenge_verified": True,
            "live_authorization": False, "production_hold_cleared": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--services-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--controller-sha", required=True)
    args = parser.parse_args()
    try:
        policy.require(args.services_root == policy.SERVICES_ROOT and args.state_root == policy.STATE_ROOT
                       and policy.SOURCE.fullmatch(args.controller_sha), "invalid production roots or controller SHA")
        policy.directory(args.services_root)
        policy.directory(args.state_root)
        certificate = load_certificate()
        plan, plan_hash, artifacts = policy.load_bundle(args.bundle, args.services_root)
        host = policy.Host(args.services_root, args.state_root)
        with policy.host_lock(host.lock):
            result = observe(host, plan, plan_hash, artifacts, args.controller_sha, certificate)
        print(json.dumps(result, sort_keys=True))
    except (policy.Refused, fixture.FixtureRefused) as error:
        print(f"Knoxx baseline reconciliation refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Knoxx baseline reconciliation failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
