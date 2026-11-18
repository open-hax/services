"""Run an admitted same-image Events transition using the native mailbox transaction."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import axxium_active_mailbox_release as active
import axxium_events_profile as profile
import axxium_minimal_release as release
from axxium_production_qualification import require, strict_json


def execute_admitted(plan, host):
    """Accept the exact plan supplied by the protected admission wrapper."""
    require(plan == host.plan, "Events host plan differs from the admitted plan")
    return release.execute_admitted(plan, host)


class EventsHost(active.Host):
    """Change the transport on the active image and retain native rollback."""

    changed_environment = active.Host.changed_environment | {profile.SUBSCRIPTIONS, profile.COEXISTENCE}

    def __init__(self, plan, plan_hash, census):
        active.IngressHost.__init__(self, plan_hash)
        self.plan = deepcopy(plan)
        self.release_source = plan["source_sha"]
        self.release_image = plan["image_id"]
        self.predecessor_plan_bytes = active.PLAN_BYTES
        self.census = census
        self.phase = "live"
        self.before_caddy = None
        self.ledger_snapshots = {}
        self.probe_ledgers = None
        self.baseline_projection = None
        self.overlay = json.dumps({"services": {"axxium": {"environment": {
            profile.SUBSCRIPTIONS: "true", profile.COEXISTENCE: "true",
        }}}}, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    def operation_name(self, plan):
        return plan["source_sha"] + "-mailbox-events"

    def unchanged_dependencies(self, plan):
        active.IngressHost.unchanged_dependencies(self, plan)
        for rows in (plan["compose_chain"], plan["retained_config"]):
            inputs = {}
            for row in rows:
                path = Path(row["path"])
                inputs[row["path"]] = (
                    path.read_bytes() if path == release.RUNTIME / "compose.yaml" else release.private_read(path)
                )
            active.verify_inputs(rows, inputs)
        previous = plan["incumbent"]
        precedent = release.STATE / "operations" / (self.release_source + "-active-mailbox") / "deployment.json"
        raw = release.private_read(precedent)
        require(sha256(raw).hexdigest() == previous["receipt_sha256"]
                and sha256(self.predecessor_plan_bytes).hexdigest() == previous["plan_sha256"],
                "Active deployment receipt or plan differs from the reviewed pins")
        receipt = strict_json(raw)
        identity = {
            "schema": "axxium-active-mailbox-deployment/v1",
            "status": "healthy",
            "source_sha": self.release_source,
            "image_id": self.release_image,
            "app_container_id": plan["expected"]["app_container_id"],
            "plan_sha256": previous["plan_sha256"],
            "webhook_enrollment": "held",
        }
        require(isinstance(receipt, dict) and all(receipt.get(k) == v for k, v in identity.items()),
                "Healthy active deployment receipt does not bind this incumbent")
        self.identity_check()

    def compose_command(self, overlay=None):
        command = super().compose_command()
        base_layers = 2 * len(active.PLAN["compose_chain"])
        command = command[:-base_layers]
        for row in self.plan["compose_chain"]:
            command += ["-f", row["path"]]
        return command if overlay is None else command + ["-f", str(overlay)]

    def projections(self):
        render = ["config", "--format", "json"]
        incumbent = strict_json(self.command(self.compose_command() + render, env=self.compose_environment()))
        candidate = strict_json(self.command(
            self.compose_command("-") + render, env=self.compose_environment(), data=self.overlay,
        ))
        profile.validate_projection(
            incumbent, candidate, image_id=self.release_image, recipient_did=self.plan["recipient_did"],
        )
        if self.baseline_projection is not None and incumbent != self.baseline_projection:
            raise release.Uncertain("Retained Compose projection changed")
        self.baseline_projection = incumbent
        return incumbent, candidate

    def mailbox_projection(self):
        incumbent, _ = self.projections()
        return incumbent["services"]["axxium"]

    def verify_held_census(self, plan):
        self.recheck_lock()
        require(self.pending_bytes is not None and self.operation is not None
                and "before" in self.ledger_snapshots, "Events preflight requires a held native backup")
        before = active.ledger_bytes(active.LEDGERS)
        for snapshot in self.ledger_snapshots.values():
            active.preserve_ledgers(snapshot, before)
        counts = self.census(before["mailbox"], plan["recipient_did"])
        profile.validate_census(counts)
        if active.ledger_bytes(active.LEDGERS) != before:
            raise release.Uncertain("Retained ledgers changed during the held callback census")
        self.recheck_lock()

    def start_candidate(self, plan):
        self.unchanged_dependencies(plan)
        stopped = self.inspect(release.APP)
        previous = self.candidate_id or plan["expected"]["app_container_id"]
        if (stopped["Id"] != previous or stopped["Image"] != self.release_image
                or stopped["State"]["Running"] is not False):
            raise release.Uncertain("Expected stopped application was not observed before Events start")
        if self.candidate_id is None:
            self.original_layer(plan)
        self.projections()
        overlay_path = self.operation / "candidate.compose.json"
        if overlay_path.exists():
            require(release.private_read(overlay_path) == self.overlay, "Candidate Events overlay changed")
        else:
            self.private_write(overlay_path, self.overlay)
        self.verify_held_census(plan)
        self.compose("up", "-d", "--no-deps", "--pull", "never", "axxium", overlay=overlay_path)
        selected = self.inspect(release.APP)
        if (selected["Id"] == plan["expected"]["app_container_id"]
                or selected["Image"] != self.release_image or selected["State"]["Running"] is not True):
            raise release.Uncertain("Events application recreation was not observed")
        self.candidate_id = selected["Id"]
        self.verify_active_runtime(selected, candidate=True)

    def verify_active_runtime(self, selected, *, candidate=None):
        if selected["Image"] != self.release_image:
            raise release.Uncertain("Active application image differs")
        if candidate is None:
            candidate = self.candidate_id is not None and selected["Id"] == self.candidate_id
        incumbent, proposed = self.projections()
        expected = (proposed if candidate else incumbent)["services"]["axxium"]
        actual_env = release.container_environment(selected)
        expected_env = release.container_environment(self.inspect(self.release_image))
        for key, value in expected["environment"].items():
            if value is None:
                expected_env.pop(key, None)
            else:
                expected_env[key] = value
        if actual_env != expected_env:
            raise release.Uncertain("Active Events runtime environment differs")
        actual_mounts = sorted((row["Type"], row["Source"], row["Destination"], row["RW"])
                               for row in selected["Mounts"])
        expected_mounts = sorted((row["type"], row["source"], row["target"], not row.get("read_only", False))
                                 for row in expected["volumes"])
        if actual_mounts != expected_mounts:
            raise release.Uncertain("Active Events runtime mounts differ")

    def verify_rollback(self, plan, current):
        self.verify_active_runtime(current, candidate=False)
        super().verify_rollback(plan, current)

    def receipt(self, status):
        return {
            **super().receipt(status),
            "schema": "axxium-mailbox-events-deployment/v1",
            "incumbent_source_sha": self.release_source,
            "server_events_profile": "enabled" if status == "healthy" else "restored-sse-only",
        }
