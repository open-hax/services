#!/usr/bin/env python3
"""Guard deployment dependency semantics that GitHub represents as skips."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-stack-chain.yml"
SERVICE_DEPLOY_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "deploy-digitalocean.yml"
)


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

    service_deploy = yaml.load(
        SERVICE_DEPLOY_WORKFLOW.read_text(), Loader=yaml.BaseLoader
    )
    steps = service_deploy["jobs"]["deploy"]["steps"]
    named_steps = {
        step.get("name"): (index, step)
        for index, step in enumerate(steps)
        if step.get("name")
    }
    required_steps = (
        "Load and validate host contract",
        "Sync service definition",
        "Health gate",
        "Admit Knoxx publication content",
        "Collect deployment report",
    )
    absent = [name for name in required_steps if name not in named_steps]
    if absent:
        print(
            "deploy chain error: missing service deployment steps: "
            + ", ".join(absent),
            file=sys.stderr,
        )
        return 1

    admission_steps = [
        step
        for step in steps
        if step.get("name") == "Admit Knoxx publication content"
    ]
    hook_invocations = sum(
        step.get("run", "").count("./post-deploy.sh") for step in steps
    )
    if len(admission_steps) != 1 or hook_invocations != 1:
        print(
            "deploy chain error: Knoxx content admission must have exactly one "
            "step and one post-deploy hook invocation",
            file=sys.stderr,
        )
        return 1

    _, contract = named_steps["Load and validate host contract"]
    _, sync = named_steps["Sync service definition"]
    if 'test -f "${dir}/post-deploy.sh"' not in contract.get("run", ""):
        print(
            "deploy chain error: Knoxx post-deploy hook is not a required service file",
            file=sys.stderr,
        )
        return 1
    if "chmod +x '${RUNTIME_PATH}/post-deploy.sh'" not in sync.get("run", ""):
        print(
            "deploy chain error: Knoxx post-deploy hook is not made executable after sync",
            file=sys.stderr,
        )
        return 1

    health_index, health = named_steps["Health gate"]
    admission_index, admission = named_steps["Admit Knoxx publication content"]
    report_index, report = named_steps["Collect deployment report"]
    if not health_index < admission_index < report_index:
        print(
            "deploy chain error: Knoxx content admission must run once after "
            "health and before deployment reporting",
            file=sys.stderr,
        )
        return 1
    if admission.get("if") != "inputs.service == 'knoxx'":
        print(
            "deploy chain error: content admission must be scoped exactly to Knoxx",
            file=sys.stderr,
        )
        return 1
    if "./post-deploy.sh" not in admission.get("run", ""):
        print(
            "deploy chain error: Knoxx content admission does not invoke post-deploy.sh",
            file=sys.stderr,
        )
        return 1
    report_env = report.get("env", {})
    report_run = report.get("run", "")
    if (
        report_env.get("POST_DEPLOY_RESULT") != "${{ steps.post_deploy.outcome }}"
        or 'postDeploy: $postDeploy' not in report_run
    ):
        print(
            "deploy chain error: deployment report does not preserve the "
            "post-deploy admission outcome",
            file=sys.stderr,
        )
        return 1
    health_run = health.get("run", "")
    if (
        'if [ "$service" = knoxx ]; then attempts=5; fi' not in health_run
        or 'for _ in $(seq 1 "$attempts")' not in health_run
    ):
        print(
            "deploy chain error: Knoxx health retries do not cap the bounded "
            "Gemma canary schedule",
            file=sys.stderr,
        )
        return 1
    print("deploy chain preserves upstream failure propagation")
    print("Knoxx content admission runs once after the health gate")
    print("deployment report preserves health and post-deploy outcomes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
