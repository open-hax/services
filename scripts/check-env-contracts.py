#!/usr/bin/env python3
"""Ensure service environments and Knoxx admission prerequisites stay lawful."""

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
    deploy_text = deploy_path.read_text()
    document = yaml.load(deploy_text, Loader=yaml.BaseLoader)
    code_quality_text = (WORKFLOWS / "code-quality.yml").read_text()
    host_workflow_text = (WORKFLOWS / "digitalocean-host.yml").read_text()
    production_host_contract = (
        ROOT / "digitalocean" / "hosts" / "production.yaml"
    ).read_text()
    host_bootstrap = (
        ROOT / "digitalocean" / "scripts" / "bootstrap-host.sh"
    ).read_text()
    host_verify = (
        ROOT / "digitalocean" / "scripts" / "verify-host.sh"
    ).read_text()
    ollama_provisioner = (
        ROOT / "digitalocean" / "scripts" / "provision-ollama.sh"
    ).read_text()
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
    knoxx_compose = (
        ROOT / "digitalocean" / "services" / "knoxx" / "compose.yaml"
    ).read_text()
    knoxx_template = (
        ROOT / "digitalocean" / "services" / "knoxx" / "env.template"
    ).read_text()
    knoxx_operator_docs = (
        ROOT / "docs" / "knoxx-ollama-embeddings.md"
    ).read_text()
    knoxx_event_agent_limits = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "event-agent-limits.sh"
    ).read_text()
    knoxx_event_agent_limits_test = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "test-event-agent-limits.sh"
    ).read_text()
    knoxx_mongodb_identity = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "mongodb-database-identity.sh"
    ).read_text()
    knoxx_mongodb_identity_test = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "test-mongodb-database-identity.sh"
    ).read_text()
    knoxx_ollama_probe_path = (
        ROOT / "digitalocean" / "services" / "knoxx" / "probe-ollama.js"
    )
    knoxx_ollama_probe = (
        knoxx_ollama_probe_path.read_text()
        if knoxx_ollama_probe_path.is_file()
        else ""
    )
    knoxx_edn_contract_law_path = ROOT / "scripts" / "check-edn-contracts.clj"
    knoxx_edn_contract_law = (
        knoxx_edn_contract_law_path.read_text()
        if knoxx_edn_contract_law_path.is_file()
        else ""
    )
    knoxx_embedding_migration_probe_path = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "probe-embedding-migration.js"
    )
    knoxx_embedding_migration_probe = (
        knoxx_embedding_migration_probe_path.read_text()
        if knoxx_embedding_migration_probe_path.is_file()
        else ""
    )
    knoxx_post_deploy_path = (
        ROOT / "digitalocean" / "services" / "knoxx" / "post-deploy.sh"
    )
    knoxx_post_deploy = (
        knoxx_post_deploy_path.read_text()
        if knoxx_post_deploy_path.is_file()
        else ""
    )
    knoxx_post_deploy_test_path = (
        ROOT / "digitalocean" / "services" / "knoxx" / "test-post-deploy.sh"
    )
    knoxx_post_deploy_test = (
        knoxx_post_deploy_test_path.read_text()
        if knoxx_post_deploy_test_path.is_file()
        else ""
    )
    knoxx_document_inventory_path = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "document-anchor-inventory.cljc"
    )
    knoxx_document_inventory = (
        knoxx_document_inventory_path.read_text()
        if knoxx_document_inventory_path.is_file()
        else ""
    )
    knoxx_embedding_receipt_path = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "embedding-contract-receipt.sh"
    )
    knoxx_embedding_receipt = (
        knoxx_embedding_receipt_path.read_text()
        if knoxx_embedding_receipt_path.is_file()
        else ""
    )
    knoxx_embedding_receipt_test_path = (
        ROOT
        / "digitalocean"
        / "services"
        / "knoxx"
        / "test-embedding-contract-receipt.sh"
    )
    knoxx_embedding_receipt_test = (
        knoxx_embedding_receipt_test_path.read_text()
        if knoxx_embedding_receipt_test_path.is_file()
        else ""
    )
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

    # The deployment-specific publication-agent overrides are only lawful when
    # their exact model contract exists and the backend can reach that provider.
    # A model id in the environment alone resolves at startup but cannot run.
    translation_model_contract = (
        ROOT / "contracts" / "knoxx" / "models" / "gemma4_e2b.edn"
    )
    required_knoxx_environment = {
        "publication-agent model overrides": (
            knoxx_template,
            "KNOXX_AGENT_MODEL_OVERRIDES='publication_translator=gemma4:e2b,publication_post_drafter=gemma4:e2b'",
        ),
        "publication-agent model override container binding": (
            knoxx_compose,
            "KNOXX_AGENT_MODEL_OVERRIDES:",
        ),
        "publication-agent thinking overrides": (
            knoxx_template,
            "KNOXX_AGENT_THINKING_OVERRIDES='publication_translator=off,publication_post_drafter=off'",
        ),
        "publication-agent thinking override container binding": (
            knoxx_compose,
            "KNOXX_AGENT_THINKING_OVERRIDES:",
        ),
        "in-process translation runner container binding": (
            knoxx_compose,
            "KNOXX_TRANSLATION_RUNNER: agent",
        ),
        "in-process translation runner verification": (
            knoxx_verify,
            'if [ "${KNOXX_TRANSLATION_RUNNER:-}" != "agent" ]',
        ),
        "event-agent concurrency default": (
            knoxx_template,
            "KNOXX_EVENT_AGENT_CONCURRENCY='1'",
        ),
        "event-agent concurrency container binding": (
            knoxx_compose,
            "KNOXX_EVENT_AGENT_CONCURRENCY:",
        ),
        "event-agent queue-limit default": (
            knoxx_template,
            "KNOXX_EVENT_AGENT_QUEUE_LIMIT='256'",
        ),
        "event-agent queue-limit container binding": (
            knoxx_compose,
            "KNOXX_EVENT_AGENT_QUEUE_LIMIT:",
        ),
        "event-agent turn-timeout default": (
            knoxx_template,
            "KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS='300000'",
        ),
        "event-agent turn-timeout container binding": (
            knoxx_compose,
            "KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS:",
        ),
        "event-agent limiter verification": (
            knoxx_verify,
            "for limiter_name in KNOXX_EVENT_AGENT_CONCURRENCY KNOXX_EVENT_AGENT_QUEUE_LIMIT; do",
        ),
        "event-agent turn-timeout canonical decimal enforcement": (
            knoxx_event_agent_limits,
            "must not contain leading zeroes",
        ),
        "event-agent turn-timeout Node maximum": (
            knoxx_event_agent_limits,
            "event_timeout_max=2147483647",
        ),
        "event-agent turn-timeout locale-stable decimal comparison": (
            knoxx_event_agent_limits,
            "local LC_ALL=C",
        ),
        "event-agent turn-timeout non-arithmetic maximum comparison": (
            knoxx_event_agent_limits,
            '[[ "$candidate" > "$maximum" ]]',
        ),
        "event-agent turn-timeout fail-closed range diagnostic": (
            knoxx_event_agent_limits,
            "must be between 1 and ${event_timeout_max}",
        ),
        "event-agent turn-timeout verifier invocation": (
            knoxx_verify,
            "validate_knoxx_event_agent_turn_timeout",
        ),
        "event-agent turn-timeout verifier helper source": (
            knoxx_verify,
            '. "$verify_dir/event-agent-limits.sh"',
        ),
        "event-agent turn-timeout helper deployment": (
            deploy_text,
            "for helper in document-anchor-inventory.cljc embedding-contract-receipt.sh event-agent-limits.sh mongodb-database-identity.sh probe-embedding-migration.js probe-mcp.js probe-ollama.js",
        ),
        "event-agent turn-timeout regression CI wiring": (
            code_quality_text,
            "bash digitalocean/services/knoxx/test-event-agent-limits.sh",
        ),
        "event-agent turn-timeout maximum acceptance regression": (
            knoxx_event_agent_limits_test,
            'expect_valid "Node maximum" "2147483647"',
        ),
        "event-agent turn-timeout first overflow regression": (
            knoxx_event_agent_limits_test,
            'expect_invalid "above Node maximum" "2147483648"',
        ),
        "event-agent turn-timeout leading-zero regression": (
            knoxx_event_agent_limits_test,
            'expect_invalid "deployment default with padding" "000300000"',
        ),
        "event-agent turn-timeout huge overflow regression": (
            knoxx_event_agent_limits_test,
            '"far beyond Bash integer range"',
        ),
        "event-agent turn-timeout operator documentation": (
            knoxx_operator_docs,
            "KNOXX_EVENT_AGENT_TURN_TIMEOUT_MS=300000",
        ),
        "event-agent turn-timeout operator range": (
            knoxx_operator_docs,
            "valid range is `1..2147483647` milliseconds",
        ),
        "host Ollama endpoint": (
            knoxx_template,
            "OLLAMA_BASE_URL='http://172.30.114.1:11434'",
        ),
        "pinned host Ollama runtime": (
            ollama_provisioner,
            "OLLAMA_VERSION=0.33.2",
        ),
        "host contract Ollama runtime": (
            production_host_contract,
            'version: "0.33.2"',
        ),
        "host contract dedicated bridge bind": (
            production_host_contract,
            "bind: dedicated-docker-bridge",
        ),
        "host contract Ollama network": (
            production_host_contract,
            "network: knoxx-ollama",
        ),
        "host contract Ollama bridge interface": (
            production_host_contract,
            "bridgeInterface: knoxx-ollama0",
        ),
        "host contract Ollama subnet": (
            production_host_contract,
            "subnet: 172.30.114.0/29",
        ),
        "host contract Ollama gateway": (
            production_host_contract,
            "gateway: 172.30.114.1",
        ),
        "host contract Ollama backend address": (
            production_host_contract,
            "backendAddress: 172.30.114.2",
        ),
        "pinned host Ollama archive": (
            ollama_provisioner,
            "OLLAMA_ARCHIVE_SHA256=9785247dea264d9072f09f6c9c0eb4b8e666892826a3d8388eba3e8fb9ed1db9",
        ),
        "pinned host translation-model manifest": (
            ollama_provisioner,
            "OLLAMA_TRANSLATION_DIGEST=7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e",
        ),
        "host contract translation-model manifest": (
            production_host_contract,
            "digest: 7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e",
        ),
        "pinned host embedding-model manifest": (
            ollama_provisioner,
            "OLLAMA_EMBEDDING_DIGEST=64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b",
        ),
        "host contract embedding-model manifest": (
            production_host_contract,
            "digest: 64b933495768fbd3b87c20583d379728a07471e0c66733a9df87cd1901b3c44b",
        ),
        "dedicated-bridge-only Ollama bind": (
            ollama_provisioner,
            'Environment="OLLAMA_HOST=${OLLAMA_NETWORK_GATEWAY}:${OLLAMA_PORT}"',
        ),
        "dedicated internal Ollama bridge": (
            ollama_provisioner,
            'OLLAMA_NETWORK_NAME=knoxx-ollama',
        ),
        "host provisioning creates the audited Ollama network": (
            ollama_provisioner,
            '"$DOCKER_BIN" network create',
        ),
        "host provisioning retires the broad Ollama rule": (
            ollama_provisioner,
            'delete allow from 172.16.0.0/12',
        ),
        "exact-interface Ollama firewall rule": (
            ollama_provisioner,
            'allow in on "$OLLAMA_BRIDGE_INTERFACE"',
        ),
        "exact-source Ollama firewall rule": (
            ollama_provisioner,
            'from "$OLLAMA_BACKEND_ADDRESS" to "$OLLAMA_NETWORK_GATEWAY"',
        ),
        "Ollama wildcard-listener rejection": (
            ollama_provisioner,
            'listener_is_ready',
        ),
        "host bootstrap provisions Ollama": (
            host_bootstrap,
            '"$OLLAMA_PROVISIONER"',
        ),
        "host verification requires Ollama": (
            host_verify,
            "check ollama-host-runtime ollama_ready",
        ),
        "host workflow installs the Ollama provisioner": (
            host_workflow_text,
            "/usr/local/sbin/open-hax-provision-ollama",
        ),
        "host workflow tests Ollama provisioning readiness": (
            host_workflow_text,
            "digitalocean/scripts/test-provision-ollama.sh",
        ),
        "Knoxx deploy verifies the host Ollama boundary": (
            deploy_text,
            "sudo -n /usr/local/sbin/open-hax-provision-ollama --readiness",
        ),
        "Knoxx deploy rejects a stale host Ollama provisioner": (
            deploy_text,
            'installed_sha=$(sha256sum "$installed_provisioner"',
        ),
        "host verification rejects a stale Ollama provisioner": (
            host_workflow_text,
            'installed_provisioner_sha=${installed_provisioner_sha%% *}',
        ),
        "deploy user receives only Ollama readiness permission": (
            host_bootstrap,
            'printf \'%s ALL=(root) NOPASSWD: %s --readiness\\n\'',
        ),
        "host Ollama endpoint container binding": (
            knoxx_compose,
            "OLLAMA_BASE_URL:",
        ),
        "generated contract root": (
            knoxx_template,
            "KNOXX_GENERATED_CONTRACTS_DIR='/app/workspace/.knoxx/contracts'",
        ),
        "generated contract root container binding": (
            knoxx_compose,
            "KNOXX_GENERATED_CONTRACTS_DIR:",
        ),
        "Ollama dedicated network mapping": (
            knoxx_compose,
            "ipv4_address: 172.30.114.2",
        ),
        "Ollama external network identity": (
            knoxx_compose,
            "name: knoxx-ollama",
        ),
        "host Ollama runtime gate": (
            knoxx_verify,
            "ollama_runtime_probe",
        ),
        "host Ollama translation-model precondition": (
            knoxx_verify,
            "translation_model=gemma4:e2b",
        ),
        "host Ollama embedding-model precondition": (
            knoxx_verify,
            "embedding_model=qwen3-embedding:8b",
        ),
        "host Ollama embedding-dimension precondition": (
            knoxx_verify,
            "embedding_dimensions=1024",
        ),
        "translator exact runtime trigger gate": (
            knoxx_verify,
            'translation_trigger_id="publication/translation-needed"',
        ),
        "translator exact runtime agent gate": (
            knoxx_verify,
            'translation_agent_id="publication_translator"',
        ),
        "translator exact runtime listener gate": (
            knoxx_verify,
            'translation_listener_id="pi"',
        ),
        "translator trusted emitter gate": (
            knoxx_verify,
            'if [ "$trigger_emitter" != "knoxx-publication" ]',
        ),
        "translator event authority gate": (
            knoxx_verify,
            '[ "$trigger_resource_policies" != "true" ] || [ "$trigger_execution_snapshot" != "true" ]',
        ),
        "translator exact runtime write-tool gate": (
            knoxx_verify,
            'translation_tool_id="save_translation"',
        ),
        "translator resolved model gate": (
            knoxx_verify,
            'if [ "$resolved_translation_model" != "$translation_model" ]',
        ),
        "translator resolved thinking gate": (
            knoxx_verify,
            'if [ "$resolved_translation_thinking" != "off" ]',
        ),
        "translator resolved required-first tools-choice gate": (
            knoxx_verify,
            'if [ "$resolved_translation_tools_choice" != "required-first" ]',
        ),
        "translator exact runtime action gate": (
            knoxx_verify,
            'if [ "$trigger_action" != "start-agent-session" ]',
        ),
        "post-drafter exact runtime trigger gate": (
            knoxx_verify,
            'draft_trigger_id="publication/craft-post-from-indexed-document"',
        ),
        "post-drafter exact runtime agent gate": (
            knoxx_verify,
            'draft_agent_id="publication_post_drafter"',
        ),
        "post-drafter exact runtime listener gate": (
            knoxx_verify,
            'draft_listener_id="pi"',
        ),
        "post-drafter trusted emitter gate": (
            knoxx_verify,
            'if [ "$draft_trigger_emitter" != "knoxx-publication" ]',
        ),
        "post-drafter generation-policy gate": (
            knoxx_verify,
            '[ "$draft_trigger_condition" != "true" ] || [ "$draft_trigger_resource_policies" != "true" ]',
        ),
        "post-drafter runtime write-tool gate": (
            knoxx_verify,
            'draft_tool_id="save_publication_draft"',
        ),
        "post-drafter enabled-trigger assertion": (
            knoxx_verify,
            'if [ "$draft_trigger_enabled" != "true" ]',
        ),
        "post-drafter actor-scoped catalog lookup": (
            knoxx_verify,
            '"/api/knoxx/agents/catalog?actorId=${draft_listener_id}"',
        ),
        "post-drafter resolved runtime tool assertion": (
            knoxx_verify,
            '((.["tool-ids"] // []) | index($tool)) != null',
        ),
        "post-drafter resolved model gate": (
            knoxx_verify,
            'if [ "$resolved_drafter_model" != "$translation_model" ]',
        ),
        "post-drafter resolved thinking gate": (
            knoxx_verify,
            'if [ "$resolved_drafter_thinking" != "off" ]',
        ),
        "post-drafter resolved required-first tools-choice gate": (
            knoxx_verify,
            'if [ "$resolved_drafter_tools_choice" != "required-first" ]',
        ),
        "post-drafter exact runtime action gate": (
            knoxx_verify,
            'if [ "$draft_trigger_action" != "start-agent-session" ]',
        ),
        "translation recovery warning": (
            knoxx_verify,
            "there is no autonomous retry timer",
        ),
        "direct host Ollama embedding binding": (
            knoxx_compose,
            "EMBED_PROVIDER_BASE_URL: ${OLLAMA_BASE_URL:?OLLAMA_BASE_URL must be set}",
        ),
        "credential-free host Ollama embedding binding": (
            knoxx_compose,
            'EMBED_PROVIDER_API_KEY: ""',
        ),
        "pinned embedding model": (
            knoxx_template,
            "EMBED_PROVIDER_MODEL='qwen3-embedding:8b'",
        ),
        "1024-dimensional embedding contract": (
            knoxx_template,
            "EMBED_PROVIDER_DIMENSIONS='1024'",
        ),
        "embedding migration target model": (
            knoxx_embedding_migration_probe,
            'const TARGET_MODEL = "qwen3-embedding:8b";',
        ),
        "embedding migration target dimensions": (
            knoxx_embedding_migration_probe,
            'const TARGET_DIMENSIONS = "1024";',
        ),
        "embedding migration relevant collections": (
            knoxx_embedding_migration_probe,
            '"vector_partitions",',
        ),
        "embedding migration graph-index inventory": (
            knoxx_embedding_migration_probe,
            '.listSearchIndexes(GRAPH_INDEX)',
        ),
        "embedding migration populated-store refusal": (
            knoxx_embedding_migration_probe,
            'reason: "populated-store-requires-authoritative-migration"',
        ),
        "embedding migration incompatible-writer refusal": (
            knoxx_embedding_migration_probe,
            'reason: "incompatible-writer-active"',
        ),
        "embedding migration stopped-backend recovery": (
            knoxx_embedding_migration_probe,
            'EMBED_SOURCE_CONTRACT_PRESENT',
        ),
        "embedding migration incompatible-stopped refusal": (
            knoxx_embedding_migration_probe,
            'reason: "incompatible-stopped-contract"',
        ),
        "embedding migration inventories stopped project containers": (
            deploy_text,
            "docker ps --all",
        ),
        "embedding migration reads durable container contract": (
            deploy_text,
            "docker inspect --format '{{json .Config.Env}}'",
        ),
        "embedding migration credential-free database fingerprint": (
            knoxx_mongodb_identity,
            "seedlist=${authority##*@}",
        ),
        "embedding migration harmless-option-insensitive database fingerprint": (
            knoxx_mongodb_identity,
            'case "${option_key,,}" in',
        ),
        "embedding migration SRV endpoint identity": (
            knoxx_mongodb_identity,
            'identity="${identity}?srvServiceName=${srv_service_name}"',
        ),
        "embedding migration replica-set identity": (
            knoxx_mongodb_identity,
            'replicaSet=${replica_set}',
        ),
        "embedding migration stable seed ordering": (
            knoxx_mongodb_identity,
            "LC_ALL=C sort -u",
        ),
        "embedding migration database identity helper source": (
            deploy_text,
            '. "$probe_dir/mongodb-database-identity.sh"',
        ),
        "embedding migration target identity diagnostic": (
            deploy_text,
            "embedding migration gate rejected target Mongo database identity",
        ),
        "embedding migration source identity diagnostic": (
            deploy_text,
            "embedding migration gate rejected existing backend Mongo database identity",
        ),
        "Ollama provisioner hash diagnostic": (
            deploy_text,
            "cannot hash the installed Knoxx host Ollama provisioner",
        ),
        "embedding migration database identity regression": (
            knoxx_mongodb_identity_test,
            "equivalent Mongo database identities produced different fingerprints",
        ),
        "embedding migration database identity CI": (
            code_quality_text,
            "bash digitalocean/services/knoxx/test-mongodb-database-identity.sh",
        ),
        "bounded pre-cutover migration probe": (
            deploy_text,
            "timeout --kill-after=5s 90s docker run --rm",
        ),
        "embedding migration probe regression CI": (
            code_quality_text,
            "node digitalocean/services/knoxx/probe-embedding-migration.js",
        ),
        "OpenAI-compatible Ollama embedding request": (
            knoxx_ollama_probe,
            "`${embedBaseUrl}/v1/embeddings`",
        ),
        "required Gemma inference model": (
            knoxx_ollama_probe,
            "const REQUIRED_TRANSLATION_MODEL = 'gemma4:e2b';",
        ),
        "exact Gemma inference model precondition": (
            knoxx_ollama_probe,
            "translationModel === REQUIRED_TRANSLATION_MODEL",
        ),
        "native Ollama structured translation request": (
            knoxx_ollama_probe,
            "`${ollamaBaseUrl}/api/chat`",
        ),
        "OpenAI-compatible Ollama agent request": (
            knoxx_ollama_probe,
            "`${ollamaBaseUrl}/v1/chat/completions`",
        ),
        "Ollama draft tool requires every accepted argument": (
            knoxx_ollama_probe,
            "required: ['title', 'content']",
        ),
        "named first-turn tool choice": (
            knoxx_ollama_probe,
            "function: {name: TOOL_PROBE.name}",
        ),
        # The openai-completions adapter emits reasoning_effort only when the
        # model declares reasoning and compat supports the field. gemma4:e2b
        # declares both off, so the deployed post-drafter request omits it and
        # the canary must omit it too, or the health gate probes a shape
        # production never sends.
        "OpenAI-compatible reasoning field omission": (
            knoxx_ollama_probe,
            "assert.equal('reasoning_effort' in agentRequestBody, false);",
        ),
        "OpenAI-compatible reasoning contract law": (
            knoxx_edn_contract_law,
            ":supportsReasoningEffort",
        ),
        "OpenAI-compatible reasoning contract law CI": (
            code_quality_text,
            "clojure -M scripts/check-edn-contracts.clj",
        ),
        "required agent tool-call assertion": (
            knoxx_ollama_probe,
            "agentToolCall.wellFormed",
        ),
        "deterministic Gemma structured translation request": (
            knoxx_ollama_probe,
            "options: {temperature: 0, seed: 0}",
        ),
        "Gemma reasoning-disabled translation request": (
            knoxx_ollama_probe,
            "think: false",
        ),
        "strict Gemma translation schema": (
            knoxx_ollama_probe,
            "format: TRANSLATION_SCHEMA",
        ),
        "Gemma structured translation parser": (
            knoxx_ollama_probe,
            "parsedContent = JSON.parse(content)",
        ),
        "exact Gemma structured shape assertion": (
            knoxx_ollama_probe,
            "Object.keys(parsedContent).length === 1",
        ),
        "Gemma native completion assertion": (
            knoxx_ollama_probe,
            "doneReason === 'stop'",
        ),
        "Gemma inference HTTP assertion": (
            knoxx_ollama_probe,
            "translationResponse.status === 200",
        ),
        "bounded Gemma inference timeout": (
            knoxx_ollama_probe,
            "inferenceTimeoutMs <= 180000",
        ),
        "separate Gemma inference timeout gate": (
            knoxx_verify,
            "KNOXX_OLLAMA_INFERENCE_TIMEOUT_MS=${KNOXX_OLLAMA_INFERENCE_TIMEOUT_MS:-90000}",
        ),
        "nonempty finite embedding-vector assertion": (
            knoxx_ollama_probe,
            "nonemptyFiniteVector: vectorFinite",
        ),
        "exact embedding-dimension assertion": (
            knoxx_ollama_probe,
            "dimensionsMatch = vector.length === expectedDimensions",
        ),
        "configured embedding-dimension input": (
            knoxx_ollama_probe,
            "const configuredDimensions = Number(env.EMBED_PROVIDER_DIMENSIONS);",
        ),
        "configured embedding-dimension agreement": (
            knoxx_ollama_probe,
            "configuredDimensions === expectedDimensions",
        ),
        "plain-prose-without-structured-output regression": (
            knoxx_ollama_probe,
            "plain model prose",
        ),
        "malformed-structured-output regression": (
            knoxx_ollama_probe,
            "malformed structured content",
        ),
        "blank-translation regression": (
            knoxx_ollama_probe,
            "blank translation",
        ),
        "extra-structured-field regression": (
            knoxx_ollama_probe,
            "extra structured field",
        ),
        "unfinished-native-response regression": (
            knoxx_ollama_probe,
            "unfinished native response",
        ),
        "unbounded-inference-timeout regression": (
            knoxx_ollama_probe,
            "unbounded inference timeout",
        ),
    }
    for label, (text, required) in required_knoxx_environment.items():
        if required not in text:
            failures.append(f"Knoxx deployment is missing {label}")
    for surface, text in (
        ("environment template", knoxx_template),
        ("Compose binding", knoxx_compose),
        ("host verification", knoxx_verify),
        ("event-agent limit helper", knoxx_event_agent_limits),
        ("event-agent limit test", knoxx_event_agent_limits_test),
        ("operator documentation", knoxx_operator_docs),
    ):
        if "KNOXX_AGENT_TURN_TIMEOUT_MS" in text:
            failures.append(
                f"Knoxx {surface} still uses the global agent-turn timeout name"
            )
    ollama_canary_position = knoxx_verify.find(
        'ollama_probe=$(ollama_runtime_probe "$translation_model"'
    )
    frontend_proxy_position = knoxx_verify.find(
        'proxied=$(curl -s -o /dev/null'
    )
    if (
        ollama_canary_position < 0
        or frontend_proxy_position < 0
        or ollama_canary_position < frontend_proxy_position
    ):
        failures.append(
            "Knoxx runs its expensive Ollama translation canary before the fast "
            "runtime gates, multiplying inference cost across readiness retries"
        )

    # The hook inventories the exact contract tree present on the deployment
    # host and reconciles it against the endpoint response. Keep both the
    # production invariants and their fail-closed regression cases in CI.
    required_admission_reconciliation = {
        "runtime real-EDN authored-anchor inventory": (
            knoxx_post_deploy,
            "/app/node_modules/.bin/nbb -e",
        ),
        "shared JVM ownership validation": (
            code_quality_text,
            "clojure -M digitalocean/services/knoxx/document-anchor-inventory.cljc",
        ),
        "complete EDN input consumption": (
            knoxx_document_inventory,
            "resource must contain exactly one form",
        ),
        "top-level anchor validation": (
            knoxx_document_inventory,
            "must declare top-level :document/anchor? true",
        ),
        "decoded nonblank organization validation": (
            knoxx_document_inventory,
            "whitespace-only-pattern",
        ),
        "unambiguous public/organization boundary": (
            knoxx_document_inventory,
            "must not declare both public visibility and an organization owner",
        ),
        "public-only deployment corpus": (
            knoxx_document_inventory,
            "Services-authored deployment anchors must be explicitly public",
        ),
        "document symlink refusal": (
            knoxx_document_inventory,
            "must be a regular non-symlink file",
        ),
        "recursive document-tree drift refusal": (
            knoxx_document_inventory,
            "document resource subdirectories are not allowed",
        ),
        "injection-safe expected-anchor JSON": (
            knoxx_post_deploy,
            '--argjson expected "$expected_anchor_ids_json"',
        ),
        "typed admission transport response": (
            knoxx_post_deploy,
            'and (.status | type == "number")',
        ),
        "selected/admitted count coherence": (
            knoxx_post_deploy,
            "and .selected <= .admitted",
        ),
        "authored-anchor selected-count floor": (
            knoxx_post_deploy,
            "and .selected >= ($expected | length)",
        ),
        "admitted/result count coherence": (
            knoxx_post_deploy,
            "and .admitted == (.results | length)",
        ),
        "unique admission result ids": (
            knoxx_post_deploy,
            "([.results[].id] | unique | length)",
        ),
        "authored-anchor exact-once reconciliation": (
            knoxx_post_deploy,
            "did not return every authored anchor exactly once",
        ),
        "missing authored-anchor regression": (
            knoxx_post_deploy_test,
            '"missing authored anchor"',
        ),
        "duplicate authored-anchor regression": (
            knoxx_post_deploy_test,
            '"duplicate authored anchor"',
        ),
        "incoherent count regression": (
            knoxx_post_deploy_test,
            '"incoherent admitted count"',
        ),
        "incoherent selected-count regression": (
            knoxx_post_deploy_test,
            '"incoherent selected count"',
        ),
        "malformed authored-id regression": (
            knoxx_post_deploy_test,
            '"malformed authored anchor id"',
        ),
        "duplicate authored-resource-id regression": (
            knoxx_post_deploy_test,
            '"duplicate authored resource id"',
        ),
        "missing authored-document ownership regression": (
            knoxx_post_deploy_test,
            '"missing authored document ownership"',
        ),
        "blank authored-document owner regression": (
            knoxx_post_deploy_test,
            '"blank authored document owner"',
        ),
        "organization-owned deployment-anchor regression": (
            knoxx_post_deploy_test,
            '"organization-owned deployment anchor"',
        ),
        "nested ownership-marker regression": (
            knoxx_post_deploy_test,
            '"nested ownership marker"',
        ),
        "escaped blank owner regression": (
            knoxx_post_deploy_test,
            '"escaped blank authored document owner"',
        ),
        "duplicate visibility regression": (
            knoxx_post_deploy_test,
            '"duplicate document visibility"',
        ),
        "duplicate owner regression": (
            knoxx_post_deploy_test,
            '"duplicate document owner"',
        ),
        "malformed owner regression": (
            knoxx_post_deploy_test,
            '"malformed document owner"',
        ),
        "ambiguous public/owner regression": (
            knoxx_post_deploy_test,
            '"ambiguous public and organization ownership"',
        ),
        "trailing form regression": (
            knoxx_post_deploy_test,
            '"trailing document form"',
        ),
        "symlinked document regression": (
            knoxx_post_deploy_test,
            '"symlinked document resource"',
        ),
        "nested document-tree regression": (
            knoxx_post_deploy_test,
            '"nested document resource"',
        ),
        "hidden document regression": (
            knoxx_post_deploy_test,
            '"hidden document resource"',
        ),
        "malformed transport regression": (
            knoxx_post_deploy_test,
            '"malformed transport"',
        ),
        "generated-result allowance regression": (
            knoxx_post_deploy_test,
            "generated.documents/extra",
        ),
        "Gemma runtime failure diagnostic": (
            knoxx_verify,
            "{reason, routing, catalog, translation, agentToolCall, embedding}",
        ),
        "Gemma runtime success diagnostic": (
            knoxx_verify,
            "returned strict structured translation output",
        ),
    }
    for label, (text, required) in required_admission_reconciliation.items():
        if required not in text:
            failures.append(f"Knoxx deployment is missing {label}")
    inventory_position = knoxx_post_deploy.find(
        "/app/node_modules/.bin/nbb -e"
    )
    admission_position = knoxx_post_deploy.find(
        "/api/publications/documents/admit"
    )
    if (
        inventory_position < 0
        or admission_position < 0
        or inventory_position > admission_position
    ):
        failures.append(
            "Knoxx deployment does not validate the authored document inventory "
            "before admission"
        )

    required_embedding_receipt = {
        "strict embedding receipt schema": (
            knoxx_embedding_receipt,
            '(keys | sort) == ["databaseFingerprint", "dimensions", "model", "version"]',
        ),
        "atomic embedding receipt publication": (
            knoxx_embedding_receipt,
            'mv -f -- "$receipt_tmp" "$receipt_path"',
        ),
        "embedding receipt symlink refusal": (
            knoxx_embedding_receipt,
            "regular non-symlink file",
        ),
        "partial embedding receipt regression": (
            knoxx_embedding_receipt_test,
            'fail "partial receipt was accepted"',
        ),
        "failed embedding receipt write preservation": (
            knoxx_embedding_receipt_test,
            'fail "failed write changed the prior receipt"',
        ),
        "embedding receipt CI self-test": (
            code_quality_text,
            "digitalocean/services/knoxx/test-embedding-contract-receipt.sh",
        ),
    }
    for label, (text, required) in required_embedding_receipt.items():
        if required not in text:
            failures.append(f"Knoxx deployment is missing {label}")
    if "mid-run stays in flight" in knoxx_verify:
        failures.append(
            "Knoxx verify.sh still reports the obsolete stranded in-flight "
            "translation-claim warning"
        )
    if not translation_model_contract.is_file():
        failures.append("Knoxx gemma4:e2b model contract is missing")
    else:
        model_text = translation_model_contract.read_text()
        if (
            ':model/id "gemma4:e2b"' not in model_text
            or ":model/provider :ollama" not in model_text
            or ':model/api "openai-completions"' not in model_text
        ):
            failures.append(
                "Knoxx gemma4:e2b contract does not select the Ollama "
                "OpenAI-completions provider path"
            )

    # These agents must force a tool choice only for the first provider
    # response. That prevents an initial prose-only completion while allowing
    # the provider to finish normally after an accepted tool result.
    for publication_agent_contract in (
        ROOT / "contracts" / "knoxx" / "agents" / "publication_translator.edn",
        ROOT / "contracts" / "knoxx" / "agents" / "publication_post_drafter.edn",
    ):
        if not publication_agent_contract.is_file():
            failures.append(
                "Knoxx publication agent contract is missing: "
                f"{publication_agent_contract.relative_to(ROOT)}"
            )
        elif ":tools/choice :required-first" not in publication_agent_contract.read_text():
            failures.append(
                f"{publication_agent_contract.relative_to(ROOT)} does not force "
                "the initial provider tool choice"
            )

    # These four resources form the complete contract chain for generated post
    # drafts. File presence alone is insufficient: assert the cross-references
    # that let the indexed-document trigger resolve an agent, model, role,
    # capability, and its single server-pinned write tool.
    required_drafter_resources = {
        ROOT / "contracts" / "knoxx" / "agents" / "publication_post_drafter.edn": (
            ':contract/id "publication_post_drafter"',
            ":contract/kind :agent",
            ":role/publication-drafter",
            ':model "gemma4:e2b"',
            '"save_publication_draft"',
        ),
        ROOT / "contracts" / "knoxx" / "capabilities" / "cap_publication_draft.edn": (
            ":cap/id :cap/publication-draft",
            ":save_publication_draft",
        ),
        translation_model_contract: (
            ':model/id "gemma4:e2b"',
            ":model/provider :ollama",
            ':model/api "openai-completions"',
            ":model/reasoning false",
        ),
        ROOT / "contracts" / "knoxx" / "roles" / "publication_drafter.edn": (
            ":role/id :role/publication-drafter",
            ":role/capabilities [:cap/publication-draft]",
        ),
    }
    for resource_path, required_fragments in required_drafter_resources.items():
        if not resource_path.is_file():
            failures.append(
                f"Knoxx post-drafter resource is missing: {resource_path.relative_to(ROOT)}"
            )
            continue
        resource_text = resource_path.read_text()
        missing_fragments = [
            fragment for fragment in required_fragments if fragment not in resource_text
        ]
        if missing_fragments:
            failures.append(
                f"{resource_path.relative_to(ROOT)} is missing required post-drafter "
                f"claims: {', '.join(missing_fragments)}"
            )

    publication_namespace = (
        ROOT / "contracts" / "knoxx" / "namespaces" / "publication.edn"
    ).read_text()
    for required in (
        ":trigger/id :craft-post-from-indexed-document",
        ":trigger/events [:publication/document-indexed]",
        ':trigger/emitter "knoxx-publication"',
        ":trigger/condition (conditions/publication.generate-draft event)",
        ":resource-policies-from-event true",
        ':agent-id "publication_post_drafter"',
    ):
        if required not in publication_namespace:
            failures.append(
                f"Knoxx publication namespace does not wire post drafter claim {required}"
            )

    knoxx_compose_document = yaml.safe_load(knoxx_compose)
    knoxx_environment = knoxx_compose_document["services"]["knoxx-backend"][
        "environment"
    ]
    knoxx_backend = knoxx_compose_document["services"]["knoxx-backend"]
    ollama_attachment = knoxx_backend.get("networks", {}).get("ollama-access")
    if ollama_attachment != {"ipv4_address": "172.30.114.2"}:
        failures.append(
            "Knoxx backend does not have the sole fixed Ollama bridge address"
        )
    if "extra_hosts" in knoxx_backend:
        failures.append("Knoxx backend retains a host-gateway escape route")
    ollama_network = knoxx_compose_document.get("networks", {}).get(
        "ollama-access"
    )
    if ollama_network != {"name": "knoxx-ollama", "external": True}:
        failures.append(
            "Knoxx Ollama network is not the pre-provisioned external boundary"
        )
    for untrusted_service in ("knoxx-sandboxd", "knoxx-frontend"):
        service_networks = knoxx_compose_document["services"][untrusted_service].get(
            "networks", {}
        )
        if "ollama-access" in service_networks:
            failures.append(
                f"{untrusted_service} is attached to the backend-only Ollama bridge"
            )
    expected_ollama_binding = "${OLLAMA_BASE_URL:?OLLAMA_BASE_URL must be set}"
    if knoxx_environment.get("EMBED_PROVIDER_BASE_URL") != expected_ollama_binding:
        failures.append(
            "Knoxx OpenPlanner embeddings are not routed directly from OLLAMA_BASE_URL"
        )
    if knoxx_environment.get("EMBED_PROVIDER_API_KEY") != "":
        failures.append(
            "Knoxx sends an unnecessary provider credential to host Ollama"
        )
    if knoxx_environment.get("KNOXX_TRANSLATION_RUNNER") != "agent":
        failures.append(
            "Knoxx publication translations do not use the in-process agent runner"
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
    print("Knoxx host-Ollama inference and embedding probes are fail closed")
    print("Knoxx embedding cutover requires an unchanged target or fresh store")
    print("Knoxx post-drafter contracts and runtime trigger/agent/tool proof are wired")
    print("Knoxx deployed document inventory uses one shared real-EDN ownership validator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
