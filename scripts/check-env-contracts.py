#!/usr/bin/env python3
"""Ensure every active service placeholder has a deployment-time provider."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
EXPORT = re.compile(r"\bexport\s+([A-Z][A-Z0-9_]*)=")
EXTRA_KEY = re.compile(r'"([A-Z][A-Z0-9_]*)"\s*:')


def main() -> int:
    deploy_path = WORKFLOWS / "deploy-digitalocean.yml"
    document = yaml.load(deploy_path.read_text(), Loader=yaml.BaseLoader)
    render = next(
        step
        for step in document["jobs"]["deploy"]["steps"]
        if step.get("name") == "Render runtime environment"
    )

    providers = set(render["env"])
    providers.update(EXPORT.findall(render["run"]))

    # Shell helpers can require workflow context that is not represented by a
    # service template placeholder. Assert those bindings at the integration
    # boundary, not only inside the helper's isolated unit test.
    if 'resolve_caddy_dev_auth "$SERVICE" "$SERVICE_DIR"' in render["run"]:
        for required in ("SERVICE", "SERVICE_DIR"):
            if required not in render["env"]:
                print(
                    f"environment contract error: render helper uses ${required} "
                    "without a step-level provider",
                    file=sys.stderr,
                )
                return 1
    providers.update(
        EXTRA_KEY.findall((WORKFLOWS / "deploy-stack-chain.yml").read_text())
    )

    failures: list[str] = []

    # Knoxx's MCP verification is a mandatory production invariant, not an
    # optional placeholder-provider relationship. Keep the three independent
    # enforcement points wired together: render, host verification, and the
    # authentication resource consumed by the deployed application image.
    render_run = render["run"]
    knoxx_verify = (
        ROOT / "digitalocean" / "services" / "knoxx" / "verify.sh"
    ).read_text()
    mcp_auth_contract = (
        ROOT / "contracts" / "knoxx" / "authentication" / "mcp_http.edn"
    )
    if 'if [ "$SERVICE" = knoxx ]' not in render_run:
        failures.append("Knoxx render does not scope the mandatory MCP token policy")
    if "require_knoxx_mcp_verification_token" not in render_run:
        failures.append("Knoxx render does not enforce the mandatory MCP token policy")
    if "require_knoxx_mcp_verification_token" not in knoxx_verify:
        failures.append("Knoxx verify.sh does not enforce the mandatory MCP token policy")
    if not mcp_auth_contract.is_file():
        failures.append("Knoxx trusted-loopback authentication resource is missing")
    else:
        mcp_auth_text = mcp_auth_contract.read_text()
        if not re.search(
            r':grant/tools\s+\["semantic_query"\s+"events_status"\]',
            mcp_auth_text,
        ):
            failures.append(
                "Knoxx trusted-loopback grant is not clamped to semantic_query and events_status"
            )

    for template in sorted((ROOT / "digitalocean" / "services").glob("*/env.template")):
        placeholders: set[str] = set()
        for line in template.read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            placeholders.update(PLACEHOLDER.findall(line))
        missing = sorted(placeholders - providers)
        if missing:
            failures.append(f"{template.relative_to(ROOT)}: no provider for {', '.join(missing)}")

    if failures:
        print("\n".join(f"environment contract error: {failure}" for failure in failures), file=sys.stderr)
        return 1
    print("all active service placeholders have deployment-time providers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
