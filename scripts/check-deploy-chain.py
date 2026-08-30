#!/usr/bin/env python3
"""Guard deployment dependency semantics that GitHub represents as skips."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-stack-chain.yml"


def main() -> int:
    document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    condition = " ".join(document["jobs"]["deploy-website"]["if"].split())
    required = (
        "!inputs.include_ingress && needs.deploy-caddy.result == 'skipped'",
        "inputs.include_ingress && needs.deploy-caddy.result == 'success'",
    )
    missing = [clause for clause in required if clause not in condition]
    if missing:
        print(
            "deploy chain error: website must distinguish intentionally disabled "
            "ingress from Caddy skipped by an upstream failure",
            file=sys.stderr,
        )
        return 1
    print("deploy chain preserves upstream failure propagation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
