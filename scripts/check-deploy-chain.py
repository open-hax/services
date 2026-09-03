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
HOST_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "digitalocean-host.yml"
)
STACK_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "deploy-stack.yml"
)


def main() -> int:
    document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    if set(document.get("on", {})) != {"workflow_call"}:
        print(
            "deploy chain error: reusable production chain has an unlocked "
            "entrypoint",
            file=sys.stderr,
        )
        return 1
    jobs = document["jobs"]
    host_prerequisite = jobs.get("provision-host", {})
    deploy_proxx_needs = jobs.get("deploy-proxx", {}).get("needs", [])
    if isinstance(deploy_proxx_needs, str):
        deploy_proxx_needs = [deploy_proxx_needs]
    if (
        host_prerequisite.get("uses") != "./.github/workflows/digitalocean-host.yml"
        or host_prerequisite.get("with", {}).get("operation") != "bootstrap"
        or host_prerequisite.get("with", {}).get(
            "caller_holds_production_host_lock"
        )
        != "true"
        or "provision-host" not in deploy_proxx_needs
    ):
        print(
            "deploy chain error: production services do not wait for the "
            "reviewed host bootstrap",
            file=sys.stderr,
        )
        return 1

    host_workflow = yaml.load(HOST_WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    host_events = host_workflow.get("on", {})
    host_call = host_events.get("workflow_call", {})
    host_dispatch = host_events.get("workflow_dispatch", {})
    host_job = host_workflow.get("jobs", {}).get("host", {})
    bootstrap_host = next(
        (
            step
            for step in host_job.get("steps", [])
            if step.get("name") == "Bootstrap host"
        ),
        {},
    )
    resolve_target = next(
        (
            step
            for step in host_job.get("steps", [])
            if step.get("name") == "Resolve target"
        ),
        {},
    )
    resolve_environment = resolve_target.get("env", {})
    resolve_run = resolve_target.get("run", "")
    verify_host = next(
        (
            step
            for step in host_job.get("steps", [])
            if step.get("name") == "Verify host"
        ),
        {},
    )
    verify_environment = verify_host.get("env", {})
    remove_credentials = next(
        (
            step
            for step in host_job.get("steps", [])
            if step.get("name") == "Remove deployment credentials"
        ),
        {},
    )
    cleanup_environment = remove_credentials.get("env", {})
    host_callers = []
    chain_callers = []
    service_deploy_callers = []
    workflow_paths = sorted(
        set(HOST_WORKFLOW.parent.glob("*.yml"))
        | set(HOST_WORKFLOW.parent.glob("*.yaml"))
    )
    for workflow_path in workflow_paths:
        workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
        for job_name, job in workflow.get("jobs", {}).items():
            if job.get("uses") == "./.github/workflows/digitalocean-host.yml":
                host_callers.append(
                    (
                        workflow_path.name,
                        job_name,
                        job.get("with", {}).get(
                            "caller_holds_production_host_lock"
                        ),
                    )
                )
            if job.get("uses") == "./.github/workflows/deploy-stack-chain.yml":
                chain_callers.append((workflow_path.name, job_name))
            if job.get("uses") == "./.github/workflows/deploy-digitalocean.yml":
                service_deploy_callers.append(
                    (
                        workflow_path.name,
                        job_name,
                        job.get("with", {}).get(
                            "caller_holds_production_host_lock"
                        ),
                    )
                )
    stack_workflow = yaml.load(STACK_WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    stack_concurrency = (
        stack_workflow.get("jobs", {}).get("deploy", {}).get("concurrency", {})
    )
    if (
        host_call.get("inputs", {}).get("operation", {}).get("required")
        != "true"
        or host_call.get("inputs", {}).get("operation", {}).get("default")
        is not None
        or host_call.get("inputs", {})
        .get("caller_holds_production_host_lock", {})
        .get("required")
        != "true"
        or "push" in host_events
        or "operation" in host_dispatch.get("inputs", {})
        or host_workflow.get("concurrency") is not None
        or host_job.get("if") != "github.event_name != 'pull_request'"
        or bootstrap_host.get("if")
        != "steps.target.outputs.operation == 'bootstrap'"
        or host_callers
        != [("deploy-stack-chain.yml", "provision-host", "true")]
        or chain_callers != [("deploy-stack.yml", "deploy")]
        or resolve_environment.get("INPUT_HOST") != "${{ inputs.host || '' }}"
        or resolve_environment.get("INPUT_OPERATION")
        != "${{ inputs.operation || 'verify' }}"
        or "host bootstrap requires the Deploy Stack production lock"
        not in resolve_run
        or "${{ github.run_id }}" not in verify_environment.get(
            "REMOTE_VERIFY_SCRIPT", ""
        )
        or "${{ github.run_attempt }}" not in verify_environment.get(
            "REMOTE_VERIFY_SCRIPT", ""
        )
        or "${{ github.run_id }}" not in verify_environment.get(
            "REMOTE_VERIFY_REPORT", ""
        )
        or "${{ github.run_attempt }}" not in verify_environment.get(
            "REMOTE_VERIFY_REPORT", ""
        )
        or cleanup_environment != verify_environment
        or "${REMOTE_VERIFY_SCRIPT}" not in verify_host.get("run", "")
        or "${REMOTE_VERIFY_REPORT}" not in verify_host.get("run", "")
        or remove_credentials.get("if") != "always()"
        or "rm -f -- '${REMOTE_VERIFY_SCRIPT}' '${REMOTE_VERIFY_REPORT}'"
        not in remove_credentials.get("run", "")
        or stack_concurrency.get("group") != "digitalocean-production-host"
        or stack_concurrency.get("cancel-in-progress") != "false"
    ):
        print(
            "deploy chain error: host bootstrap is not confined to the locked "
            "Deploy Stack orchestrator",
            file=sys.stderr,
        )
        return 1

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
    service_events = service_deploy.get("on", {})
    service_call = service_events.get("workflow_call", {})
    expected_service_callers = [
        ("deploy-stack-chain.yml", "deploy-proxx", "true"),
        ("deploy-stack-chain.yml", "deploy-knoxx", "true"),
        ("deploy-stack-chain.yml", "deploy-caddy", "true"),
        ("deploy-stack-chain.yml", "deploy-website", "true"),
    ]
    contract_step = next(
        (
            step
            for step in service_deploy.get("jobs", {}).get("deploy", {}).get("steps", [])
            if step.get("name") == "Load and validate host contract"
        ),
        {},
    )
    if (
        set(service_events) != {"workflow_call"}
        or service_call.get("inputs", {})
        .get("caller_holds_production_host_lock", {})
        .get("required")
        != "true"
        or service_call.get("inputs", {})
        .get("caller_holds_production_host_lock", {})
        .get("default")
        is not None
        or service_deploy.get("concurrency") is not None
        or service_deploy_callers != expected_service_callers
        or contract_step.get("env", {}).get(
            "CALLER_HOLDS_PRODUCTION_HOST_LOCK"
        )
        != "${{ inputs.caller_holds_production_host_lock }}"
        or "service deployment requires the Deploy Stack production lock"
        not in contract_step.get("run", "")
    ):
        print(
            "deploy chain error: service mutation is not confined to the "
            "locked Deploy Stack orchestrator",
            file=sys.stderr,
        )
        return 1
    steps = service_deploy["jobs"]["deploy"]["steps"]
    named_steps = {
        step.get("name"): (index, step)
        for index, step in enumerate(steps)
        if step.get("name")
    }
    required_steps = (
        "Load and validate host contract",
        "Render runtime environment",
        "Authenticate the host to GHCR",
        "Verify Knoxx embedding cutover",
        "Sync service definition",
        "Deploy",
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
    render_index, _ = named_steps["Render runtime environment"]
    registry_index, _ = named_steps["Authenticate the host to GHCR"]
    migration_index, migration = named_steps["Verify Knoxx embedding cutover"]
    _, sync = named_steps["Sync service definition"]
    contract_run = contract.get("run", "")
    required_knoxx_helpers = (
        "document-anchor-inventory.cljc",
        "embedding-contract-receipt.sh",
        "post-deploy.sh",
    )
    missing_knoxx_helpers = [
        helper
        for helper in required_knoxx_helpers
        if helper not in contract_run
    ]
    if missing_knoxx_helpers:
        print(
            "deploy chain error: Knoxx deployment helper contract is incomplete: "
            + ", ".join(missing_knoxx_helpers),
            file=sys.stderr,
        )
        return 1
    if "chmod +x '${RUNTIME_PATH}/post-deploy.sh'" not in sync.get("run", ""):
        print(
            "deploy chain error: Knoxx post-deploy hook is not made executable after sync",
            file=sys.stderr,
        )
        return 1

    migration_run = migration.get("run", "")
    required_migration_gate = (
        "probe-embedding-migration.js",
        "embedding-contract-receipt.sh",
        "docker ps --all",
        "EMBED_SOURCE_CONTRACT_PRESENT",
        "EMBED_SOURCE_WRITER_ACTIVE",
        "EMBED_TARGET_DATABASE_FINGERPRINT",
        "docker inspect --format '{{json .Config.Env}}'",
        "timeout --kill-after=5s 90s docker run --rm",
        'label=com.docker.compose.service=knoxx-backend',
        'receipt_path="${state_path}/embedding-contract.json"',
        'read_embedding_contract_receipt "$receipt_path"',
        'write_embedding_contract_receipt',
    )
    missing_migration_gate = [
        clause for clause in required_migration_gate if clause not in migration_run
    ]
    if (
        migration.get("if") != "inputs.service == 'knoxx'"
        or missing_migration_gate
        or migration_run.count("--entrypoint node") != 1
        or migration.get("env", {}).get("STATE_PATH")
        != "${{ steps.contract.outputs.state_path }}"
        or migration_run.find("write_embedding_contract_receipt")
        < migration_run.find("timeout --kill-after=5s 90s docker run --rm")
    ):
        print(
            "deploy chain error: Knoxx embedding migration gate is not a "
            "single bounded pre-cutover probe: " + ", ".join(missing_migration_gate),
            file=sys.stderr,
        )
        return 1

    health_index, health = named_steps["Health gate"]
    admission_index, admission = named_steps["Admit Knoxx publication content"]
    report_index, report = named_steps["Collect deployment report"]
    sync_index, _ = named_steps["Sync service definition"]
    deploy_index, _ = named_steps["Deploy"]
    if not (
        render_index
        < registry_index
        < migration_index
        < sync_index
        < deploy_index
        < health_index
        < admission_index
        < report_index
    ):
        print(
            "deploy chain error: render, registry auth, embedding migration, "
            "sync, deploy, health, admission, and report ordering drifted",
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
        report_env.get("EMBEDDING_MIGRATION_RESULT")
        != "${{ steps.embedding_migration.outcome }}"
        or 'embeddingMigration: $embeddingMigration' not in report_run
        or report_env.get("POST_DEPLOY_RESULT")
        != "${{ steps.post_deploy.outcome }}"
        or 'postDeploy: $postDeploy' not in report_run
    ):
        print(
            "deploy chain error: deployment report does not preserve the "
            "embedding-migration and post-deploy outcomes",
            file=sys.stderr,
        )
        return 1
    health_run = health.get("run", "")
    required_health_budget = (
        "attempts=5",
        "health_budget_seconds=900",
        "health_deadline=$((SECONDS + health_budget_seconds))",
        "remaining=$((health_deadline - SECONDS - kill_grace_seconds))",
        'timeout --kill-after="${kill_grace_seconds}s" "${remaining}s" ./verify.sh',
        'if [ "$remaining" -lt "$sleep_seconds" ]; then sleep_seconds=$remaining; fi',
        'for _ in $(seq 1 "$attempts")',
    )
    missing_health_budget = [
        clause for clause in required_health_budget if clause not in health_run
    ]
    if missing_health_budget:
        print(
            "deploy chain error: Knoxx health retries do not enforce a complete "
            "verifier wall-clock budget: " + ", ".join(missing_health_budget),
            file=sys.stderr,
        )
        return 1
    print("deploy chain preserves upstream failure propagation")
    print("Knoxx content admission runs once after the health gate")
    print("deployment report preserves health and post-deploy outcomes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
