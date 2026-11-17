"""Upgrade the fixed active mailbox through the existing production transaction.

This adapter preserves the complete active configuration for candidate and
rollback. The protected caller must admit the exact source and evidence before
using it. No dispatch scope or recovery command is added here.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import axxium_ingress_caddy as caddy
from axxium_ingress_host import IngressHost
import axxium_minimal_release as release
from axxium_oidc_ingress import prepare_active_mailbox
from axxium_production_qualification import Refusal, require, strict_json, validate_plan as validate_custody
import axxium_unified_mailbox_release as unified

PLAN_PATH = "digitalocean/services/axxium/active-mailbox-upgrade-production-plan.json"
ROOT = Path(__file__).resolve().parents[1]
PLAN_BYTES = (ROOT / PLAN_PATH).read_bytes()
PLAN = strict_json(PLAN_BYTES)
PLAN_SHA256 = sha256(PLAN_BYTES).hexdigest()
SCHEMA = "axxium-active-mailbox-upgrade/v1"
SOURCE = PLAN["source_sha"]
IMAGE = PLAN["image_id"]
INCUMBENT_SOURCE = PLAN["incumbent"]["source_sha"]
INCUMBENT_OPERATION = release.STATE / "operations" / INCUMBENT_SOURCE
IDENTITY = INCUMBENT_OPERATION / "mailbox-identity"
PRIOR_PLAN = ROOT / "digitalocean/services/axxium/unified-mailbox-production-plan.json"
SIGNING = release.STATE / "operations" / (INCUMBENT_SOURCE + "-at-auth") / "actor-signing.pem"
LEDGERS = {"mailbox": release.STATE / "mailbox/ledger.edn", "identity": release.STATE / "identity/ledger.edn"}
TARGETS = {"/var/lib/axxium/mailbox/ledger.edn": "mailbox", "/var/lib/axxium/identity/ledger.edn": "identity"}


def validate_plan(plan):
    """Refuse every successor except the complete reviewed fixed plan."""
    require(isinstance(plan, dict) and plan == PLAN and plan.get("schema") == SCHEMA,
            "Unreviewed active mailbox upgrade plan")
    validate_custody({**plan, "schema": "axxium-minimal-production/v1"})
    return plan


def image_overlay(image):
    return {"services": {"axxium": {"image": image}}}


def validate_projection(current, candidate):
    """Require only the image to change over the full active Compose profile."""
    require(isinstance(current, dict) and isinstance(candidate, dict), "Compose projections required")
    old = current.get("services", {}).get("axxium")
    new = candidate.get("services", {}).get("axxium")
    require(isinstance(old, dict) and isinstance(new, dict)
            and old.get("image") == PLAN["incumbent"]["image_id"]
            and new.get("image") == IMAGE, "Active mailbox image identity differs")
    env = old.get("environment", {})
    require(all(env.get(k) == v for k, v in PLAN["transport"].items())
            and env.get("AXXIUM_MAILBOX_TRANSPORT_COEXISTENCE") is None,
            "Active mailbox transport profile differs")
    expected = deepcopy(current)
    expected["services"]["axxium"]["image"] = IMAGE
    require(expected == candidate, "Upgrade changes active configuration beyond the image")
    return deepcopy(new)


def verify_incumbent_receipt(raw, prior_plan):
    """Bind the current healthy mailbox to its immutable prior release history."""
    expected = PLAN["incumbent"]
    require(sha256(raw).hexdigest() == expected["receipt_sha256"]
            and sha256(prior_plan).hexdigest() == expected["plan_sha256"], "Incumbent receipt or plan bytes differ")
    receipt = strict_json(raw)
    known = {"schema": "axxium-unified-mailbox-deployment/v1", "status": "healthy",
             "source_sha": expected["source_sha"], "image_id": expected["image_id"],
             "app_container_id": expected["app_container_id"], "plan_sha256": expected["plan_sha256"]}
    require(isinstance(receipt, dict) and all(receipt.get(k) == v for k, v in known.items()),
            "Incumbent receipt lineage differs")


def verify_inputs(rows, inputs):
    """Check exact byte custody without treating parsed configuration as authority."""
    require(isinstance(inputs, dict) and set(inputs) == {r["path"] for r in rows}, "Retained input set differs")
    for row in rows:
        data = inputs[row["path"]]
        require(isinstance(data, bytes) and len(data) == row["bytes"]
                and sha256(data).hexdigest() == row["sha256"], "Retained input bytes differ")


def preserve_ledgers(before, current):
    """Retain opaque current ledger bytes, including accepted work and ACKs."""
    require(isinstance(before, dict) and isinstance(current, dict)
            and set(before) == set(current) == set(LEDGERS), "Mailbox and identity ledger snapshots required")
    require(all(isinstance(before[k], bytes) and isinstance(current[k], bytes)
                and current[k].startswith(before[k]) for k in LEDGERS), "Retained ledger prefix differs")
    return dict(current)


def ledger_bytes(paths):
    return {name: release.private_read(path, maximum=(2 if name == "mailbox" else 64) * 1024 * 1024)
            for name, path in paths.items()}


def execute_admitted(plan, host):
    """Use the existing transaction after the protected caller admits this plan."""
    return release.execute_admitted(validate_plan(plan), host)


class Host(IngressHost):
    """Reuse native locks, backup, rehearsals and rollback for the active service."""
    release_source = SOURCE
    release_image = IMAGE

    def __init__(self, plan_hash):
        require(plan_hash == PLAN_SHA256, "Active mailbox controller plan digest differs")
        super().__init__(plan_hash)
        self.phase = "live"
        self.before_caddy = None
        self.ledger_snapshots = {}
        self.probe_ledgers = None
        self.baseline_projection = None

    def operation_name(self, plan):
        return SOURCE + "-active-mailbox"

    def unchanged_dependencies(self, plan):
        super().unchanged_dependencies(plan)
        for rows in (plan["compose_chain"], plan["retained_config"]):
            inputs = {}
            for row in rows:
                path = Path(row["path"])
                release.controlled_file(path, secret=path != release.RUNTIME / "compose.yaml")
                inputs[row["path"]] = path.read_bytes()
            verify_inputs(rows, inputs)
        verify_incumbent_receipt(release.private_read(INCUMBENT_OPERATION / "deployment.json"),
                                 PRIOR_PLAN.read_bytes())
        self.identity_check()

    def recovery_preflight(self, plan):
        raise release.Refused("Active mailbox recovery requires its own independently qualified operation")

    def recovery_claim(self, plan):
        raise release.Refused("Active mailbox recovery cannot use the historical adoption operation")

    def identity_check(self):
        if release.digest(release.private_read(SIGNING)) != PLAN["identity"]["actor_signing_sha256"]:
            raise release.Uncertain("Retained actor signing key differs")
        verify_inputs(PLAN["retained_config"],
                      {row["path"]: release.private_read(Path(row["path"])) for row in PLAN["retained_config"]})
        work = PLAN["identity"]
        query = ("BEGIN READ ONLY; SET LOCAL statement_timeout=5000; "
                 "SELECT a.id FROM provider_bindings p JOIN actors a ON a.id=p.actor_id "
                 "JOIN entities e ON e.id=a.entity_id WHERE p.provider='atproto' AND p.subject='"
                 + work["work_atproto_did"] + "' AND a.status='active' AND e.kind='human'; ROLLBACK;")
        if self.sql("axxium", query).decode().splitlines() != [work["work_actor_id"]]:
            raise release.Uncertain("Existing production Work binding differs")

    def identity_preflight(self):
        self.identity_check()
        # Refuse drift before claiming/fencing; recreation must not normalize it.
        self.verify_active_runtime(self.inspect(release.APP))

    def provision_identity(self, plan):
        # The shared transaction keeps this checkpoint. No provisioner is run.
        self.unchanged_dependencies(plan)
        if "before" not in self.ledger_snapshots:
            raise release.Uncertain("Retained ledger snapshot unavailable")
        preserve_ledgers(self.ledger_snapshots["before"], ledger_bytes(LEDGERS))

    def compose_environment(self):
        return {"PATH": "/usr/local/bin:/usr/bin:/bin",
                "AXXIUM_PRODUCTION_MAILBOX_IMAGE": PLAN["incumbent"]["image_id"],
                "AXXIUM_PRODUCTION_MAILBOX_IDENTITY_PATH": str(IDENTITY)}

    def compose_command(self, overlay=None):
        args = ["docker", "compose", "--project-name", "axxium", "--project-directory", str(release.RUNTIME),
                "--env-file", str(release.RUNTIME / ".env"), "--env-file", str(IDENTITY / "mailbox.env")]
        for row in PLAN["compose_chain"]:
            args += ["-f", row["path"]]
        return args if overlay is None else args + ["-f", str(overlay)]

    def compose(self, *args, overlay=None):
        return self.command(self.compose_command(overlay) + list(args), timeout=940, effect=True,
                            env=self.compose_environment())

    def mailbox_projection(self):
        args = self.compose_command() + ["config", "--format", "json"]
        current = strict_json(self.command(args, env=self.compose_environment()))
        candidate = deepcopy(current)
        candidate["services"]["axxium"]["image"] = IMAGE
        selected = validate_projection(current, candidate)
        if self.baseline_projection is not None and current != self.baseline_projection:
            raise release.Uncertain("Retained resolved Compose chain changed")
        self.baseline_projection = current
        return selected

    def route_candidates(self, plan):
        observed = caddy.readback(self, plan["expected"]["caddy_container_id"], release.CADDY, release.MANAGED)
        selected = plan["ingress"]["live"]
        require(observed["active_sha256"] == observed["startup_sha256"] == selected["adapted_sha256"]
                and observed["root_sha256"] == selected["root_sha256"], "Active mailbox ingress differs")
        self.before_caddy = observed
        fenced, live = prepare_active_mailbox(self.original, selected["root_sha256"])
        require(release.digest(fenced) == plan["ingress"]["fenced"]["root_sha256"], "Mailbox fence differs")
        return fenced, live

    def replace_caddy(self, plan, data):
        self.unchanged_dependencies(plan)
        phase, = (key for key, row in plan["ingress"].items() if row["root_sha256"] == release.digest(data))
        self.before_caddy = caddy.publish(
            self, plan["expected"]["caddy_container_id"], release.CADDY, release.MANAGED,
            root_bytes=data, route_name=caddy.ROUTE_NAME, route_bytes=None, expected_before=self.before_caddy,
            expected_adapted_sha256=plan["ingress"][phase]["adapted_sha256"])
        self.phase = phase

    def backup(self, plan, label):
        super().backup(plan, label)
        state = ledger_bytes(LEDGERS)
        for prior in self.ledger_snapshots.values():
            preserve_ledgers(prior, state)
        self.ledger_snapshots[label] = state
        metadata = {}
        for name, data in state.items():
            path = self.operation / (label + "-" + name + ".edn")
            self.private_write(path, data)
            metadata[name] = {"path": str(path), "bytes": len(data), "sha256": release.digest(data)}
        self.private_write(self.operation / (label + "-ledgers.json"), json.dumps(metadata, sort_keys=True).encode())

    def rehearse(self, plan, label, *, candidate):
        state = self.ledger_snapshots[label]
        self.probe_ledgers = {name: self.operation / (label + "-probe-" + name + ".edn") for name in state}
        for name, data in state.items():
            self.private_write(self.probe_ledgers[name], data)
        # The same copies pass through candidate then incumbent, including ACKs.
        super().rehearse(plan, label, candidate=candidate)
        preserve_ledgers(state, ledger_bytes(self.probe_ledgers))
        preserve_ledgers(state, ledger_bytes(LEDGERS))
        self.probe_ledgers = None

    def probe_profile(self, plan, image, name, values, *, candidate):
        service = self.mailbox_projection()
        require(image in (IMAGE, PLAN["incumbent"]["image_id"]), "Unreviewed rehearsal image")
        require(self.probe_ledgers is not None, "Isolated retained ledgers unavailable")
        for key, value in service["environment"].items():
            if value is not None and (key.startswith(("AXXIUM_MAILBOX_", "AXXIUM_IDENTITY_", "AXXIUM_GITHUB_IDENTITY_"))
                                      or key in ("JWT_AUDIENCE", "AXXIUM_SIGNING_KEY_FILE", "AXXIUM_OIDC_CONFIG_FILE")):
                values[key] = value
        require(values.get("AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED") == "false"
                and "AXXIUM_MAILBOX_TRANSPORT_COEXISTENCE" not in values, "Rehearsal callback dispatch must stay disabled")
        mounts = []
        for row in service["volumes"]:
            target = row["target"]
            require(row["type"] == "bind" and "," not in row["source"] and "," not in target,
                    "Unexpected retained mount")
            if target in TARGETS:
                source = self.probe_ledgers[TARGETS[target]]
                mounts += ["--mount", f"type=bind,src={source},dst={target}"]
            else:
                require(row.get("read_only") is True, "Unexpected writable rehearsal mount")
                mounts += ["--mount", f'type=bind,src={row["source"]},dst={target},readonly']
        return values, mounts

    def start_candidate(self, plan):
        self.unchanged_dependencies(plan)
        if self.candidate_id is None:
            self.original_layer(plan)
        self.mailbox_projection()
        path = self.operation / "candidate.compose.json"
        raw = (json.dumps(image_overlay(IMAGE), sort_keys=True, separators=(",", ":")) + "\n").encode()
        if path.exists():
            require(release.private_read(path) == raw, "Candidate image overlay changed")
        else:
            self.private_write(path, raw)
        self.compose("up", "-d", "--no-deps", "--pull", "never", "axxium", overlay=path)
        selected = self.inspect(release.APP)
        if selected["Image"] != IMAGE:
            raise release.Uncertain("Candidate image differs")
        self.candidate_id = selected["Id"]

    def private_health(self, plan):
        super().private_health(plan)
        self.identity_check()
        self.verify_active_runtime(self.inspect(release.APP))
        for state in self.ledger_snapshots.values():
            preserve_ledgers(state, ledger_bytes(LEDGERS))
        raw = self.command(["docker", "exec", release.PROXY, "wget", "-q", "-O", "-",
                            "--header", "X-Forwarded-Proto: https", "--header", "Host: axxium.promethean.rest",
                            "http://axxium:8787" + unified.METADATA_PATH])
        unified.verify_metadata(raw)

    def verify_active_runtime(self, selected):
        service = self.mailbox_projection()
        environment = release.container_environment(selected)
        expected_env = release.container_environment(self.inspect(selected["Image"]))
        for key, value in service["environment"].items():
            if value is None:
                expected_env.pop(key, None)
            else:
                expected_env[key] = value
        if environment != expected_env:
            raise release.Uncertain("Running active mailbox environment differs")
        actual = sorted((r["Type"], r["Source"], r["Destination"], r["RW"]) for r in selected["Mounts"])
        expected = sorted((r["type"], r["source"], r["target"], not r.get("read_only", False)) for r in service["volumes"])
        if actual != expected:
            raise release.Uncertain("Running active mailbox mounts differ")

    def public_health(self, plan):
        super().public_health(plan)
        unified.Host.verify_preserved_public_routes(self)
        status, raw = self.http(unified.METADATA_PATH)
        if status != 200 or self.http(unified.RESOURCE_PATH)[0] != 401:
            raise release.KnownFailure("Active mailbox authentication boundary differs")
        unified.verify_metadata(raw)

    def verify_rollback(self, plan, current):
        self.identity_check()
        self.verify_active_runtime(current)
        for state in self.ledger_snapshots.values():
            preserve_ledgers(state, ledger_bytes(LEDGERS))
        self.public_health(plan)

    def commit(self, plan):
        self.unchanged_dependencies(plan)
        current = self.inspect(release.APP)
        observed = caddy.readback(self, plan["expected"]["caddy_container_id"], release.CADDY, release.MANAGED)
        if (current["Id"] != self.candidate_id or current["Image"] != IMAGE
                or current["State"].get("Health", {}).get("Status") != "healthy"
                or self.phase != "live" or observed != self.before_caddy):
            raise release.Uncertain("Final active mailbox selection differs")
        self.public_health(plan)
        self.private_write(self.operation / "deployment.json", json.dumps(self.receipt("healthy"), sort_keys=True).encode())

    def receipt(self, status):
        return {**super().receipt(status), "schema": "axxium-active-mailbox-deployment/v1",
                "incumbent_source_sha": INCUMBENT_SOURCE, "mailbox_discovery_verified": status == "healthy",
                "mailbox_messages_sent": False, "connector_identity_verified": False, "webhook_enrollment": "held"}
