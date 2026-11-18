"""Exercise the protected caller's actual controller archive without host effects."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import axxium_active_mailbox_release as active

WORKFLOW = ".github/workflows/deploy-axxium-unified-mailbox.yml"
CONTROLLER_FILES = {
    active.PLAN_PATH, "digitalocean/services/axxium/unified-mailbox-production-plan.json",
    *("scripts/" + name + ".py" for name in (
        "axxium_active_mailbox_release", "axxium_active_mailbox_admission",
        "axxium_unified_mailbox_release", "axxium_unified_mailbox_admission",
        "axxium_minimal_release", "axxium_ingress_host", "axxium_ingress_transaction",
        "axxium_interrupted_handoff", "axxium_ingress_caddy", "axxium_oidc_ingress",
        "axxium_production_qualification", "environment_policy", "github_environment_admission"))}


def workflow(path=WORKFLOW):
    return yaml.safe_load((ROOT / path).read_text())


def delivery_step():
    return next(step for step in workflow()["jobs"]["deploy"]["steps"]
                if "SSH_PRIVATE_KEY" in step.get("env", {}))


def unpack_controller(directory):
    """Run the real workflow's tar command over its selected source inputs."""
    selected = workflow()
    command = next(line.strip() for line in delivery_step()["run"].replace("\\\n", " ").splitlines()
                   if line.strip().startswith("tar --mode="))
    args = shlex.split(command)
    names = [selected["env"]["PLAN"] if name == "$PLAN" else name
             for name in args[args.index("-cf") + 2:]]
    directory = Path(directory)
    source = directory / "source"
    source.mkdir()
    for name in names:
        original = ROOT / name
        if not original.is_file():
            raise AssertionError("Controller archive requires an unavailable extra input: " + name)
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    subprocess.run(["bash", "-euo", "pipefail", "-c", command], cwd=source,
                   env={"PATH": os.environ["PATH"], "PLAN": selected["env"]["PLAN"]},
                   check=True, capture_output=True)
    archive = source / "controller.tar"
    unpacked = directory / "unpacked"
    unpacked.mkdir()
    with tarfile.open(archive) as package:
        members = package.getmembers()
        if {member.name for member in members} != CONTROLLER_FILES:
            raise AssertionError("Controller archive does not contain the exact reviewed closure")
        if any(not member.isfile() or member.mode != 0o600 for member in members):
            raise AssertionError("Controller archive inputs must be private regular files")
        package.extractall(unpacked, filter="data")
    return unpacked


class ActiveMailboxCallerTests(unittest.TestCase):
    def test_existing_protected_scope_queue_and_admission_boundaries_remain(self):
        entry = workflow(".github/workflows/deploy-stack.yml")
        chain = workflow(".github/workflows/deploy-stack-chain.yml")
        body = workflow()
        self.assertEqual(entry["jobs"]["deploy"]["concurrency"],
                         {"group": "digitalocean-production-host", "cancel-in-progress": False})
        caller = chain["jobs"]["deploy-axxium-mailbox"]
        self.assertEqual(caller["if"], "inputs.scope == 'axxium-mailbox'")
        self.assertEqual(caller["uses"], "./.github/workflows/deploy-axxium-events.yml")
        self.assertIs(caller["with"]["caller_holds_production_host_lock"], True)
        self.assertEqual(set(body.get("on", body.get(True))), {"workflow_call"})
        self.assertFalse(any("concurrency" in job for job in body["jobs"].values()))
        self.assertEqual(body["env"]["PLAN"], active.PLAN_PATH)
        admit = body["jobs"]["admit"]
        self.assertNotIn("environment", admit)
        self.assertNotIn("secrets.", str(admit))
        first = next(step for step in admit["steps"] if step.get("id") == "qualification")
        self.assertIn('test "$CALLER_LOCKED" = true', first["run"])
        self.assertIn('test "$CALLER_REF_PROTECTED" = true', first["run"])
        self.assertIn("open-hax/services/.github/workflows/deploy-stack.yml@refs/heads/main", first["run"])
        self.assertIn('test "$SOURCE_SHA" = "$(jq -er .source_sha "$PLAN")"', first["run"])
        deploy = body["jobs"]["deploy"]
        self.assertEqual(deploy["needs"], "admit")
        self.assertEqual(deploy["environment"], "production")
        self.assertEqual(deploy["if"], "needs.admit.outputs.plan_sha256 != ''")
        for job in (admit, deploy):
            gates = [step for step in job["steps"] if "--archive stage.zip" in step.get("run", "")]
            self.assertEqual(len(gates), 1)
            gate = gates[0]
            self.assertIn("python3 scripts/axxium_active_mailbox_admission.py --plan", gate["run"])
            self.assertNotIn("python3 scripts/axxium_unified_mailbox_admission.py --plan", gate["run"])
            self.assertEqual(gate["env"]["GH_TOKEN"], gate["env"]["GITHUB_TOKEN"])
            self.assertIn("gh api", gate["run"])
            self.assertIn("--archive stage.zip", gate["run"])
        steps = deploy["steps"]
        recheck = next(i for i, step in enumerate(steps) if "--archive stage.zip" in step.get("run", ""))
        secret = next(i for i, step in enumerate(steps) if "SSH_PRIVATE_KEY" in step.get("env", {}))
        self.assertLess(recheck, secret)
        self.assertIn('.admitted == true and .plan_sha256 == $sha', steps[recheck]["run"])

    def test_upgrade_delivery_uses_active_cli_and_existing_identity_only(self):
        body = workflow()
        secrets = body.get("on", body.get(True))["workflow_call"]["secrets"]
        self.assertEqual(set(secrets), {"DIGITALOCEAN_SSH_PRIVATE_KEY"})
        step = delivery_step()
        self.assertEqual(set(step["env"]), {"SSH_PRIVATE_KEY", "CONTROLLER_SHA", "PLAN_SHA256"})
        self.assertIn("python3 -B '$controller/scripts/axxium_active_mailbox_release.py' --plan", step["run"])
        self.assertIn("--plan-sha256 '$PLAN_SHA256' --apply", step["run"])
        self.assertIn("assert_pinned_host_key", step["run"])
        self.assertIn("StrictHostKeyChecking yes", step["run"])
        self.assertIn("check-axxium-production-hold.sh", step["run"])
        for forbidden in ("BINDING_JSON", "OWNER_SEED", "production-mailbox-binding.json",
                          "production-mailbox-seed.edn", "provision_axxium_", "--recover-interrupted",
                          "docker load", "docker build"):
            self.assertNotIn(forbidden, step["run"])

    def test_real_receipt_filter_requires_exact_active_release_and_keeps_enrollment_held(self):
        command = next(line.strip() for line in delivery_step()["run"].replace("\\\n", " ").splitlines()
                       if line.strip().startswith("jq -e --arg source"))
        valid = {"schema": "axxium-active-mailbox-deployment/v1", "status": "healthy",
                 "source_sha": active.SOURCE, "image_id": active.IMAGE, "plan_sha256": active.PLAN_SHA256,
                 "mailbox_discovery_verified": True, "mailbox_messages_sent": False,
                 "connector_identity_verified": False, "webhook_enrollment": "held"}
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            plan.write_bytes(active.PLAN_BYTES)
            receipt = Path(directory) / "release-receipt.json"
            environment = {"PATH": os.environ["PATH"], "PLAN": str(plan), "PLAN_SHA256": active.PLAN_SHA256}
            receipt.write_text(json.dumps(valid))
            self.assertEqual(subprocess.run(["bash", "-euo", "pipefail", "-c", command], cwd=directory,
                                            env=environment, capture_output=True).returncode, 0)
            for field, value in (("schema", "axxium-unified-mailbox-deployment/v1"), ("status", "rolled_back"),
                                 ("source_sha", active.INCUMBENT_SOURCE),
                                 ("image_id", active.PLAN["image_manifest_digest"]), ("plan_sha256", "0" * 64),
                                 ("mailbox_discovery_verified", False), ("mailbox_messages_sent", True),
                                 ("connector_identity_verified", True), ("webhook_enrollment", "active")):
                receipt.write_text(json.dumps({**valid, field: value}))
                with self.subTest(field=field):
                    result = subprocess.run(["bash", "-euo", "pipefail", "-c", command], cwd=directory,
                                            env=environment, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)

    def test_actual_archive_imports_and_cli_select_the_active_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            unpacked = unpack_controller(directory)
            program = r'''
import contextlib, io, json, pathlib, sys
from unittest import mock
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / 'scripts'))
import axxium_active_mailbox_release as active
assert active.PRIOR_PLAN.read_bytes()
assert pathlib.Path(active.__file__).is_relative_to(root)
for name in ('axxium_ingress_host', 'axxium_ingress_transaction', 'axxium_interrupted_handoff',
             'axxium_unified_mailbox_release', 'axxium_production_qualification',
             'github_environment_admission', 'environment_policy'):
    assert pathlib.Path(sys.modules[name].__file__).is_relative_to(root)
argv = ['--plan', str(root / active.PLAN_PATH), '--plan-sha256', active.PLAN_SHA256, '--apply']
with mock.patch.object(active, 'Host') as host, mock.patch.object(active, 'execute_admitted',
        return_value={'status': 'healthy', 'schema': 'axxium-active-mailbox-deployment/v1'}) as execute:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert active.main(argv) == 0
    host.assert_called_once_with(active.PLAN_SHA256)
    execute.assert_called_once_with(active.PLAN, host.return_value)
    assert json.loads(output.getvalue())['schema'] == 'axxium-active-mailbox-deployment/v1'
print('isolated active archive and CLI selection passed')
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(unpacked)],
                                    cwd=directory, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "isolated active archive and CLI selection passed\n")

    def test_actual_archive_cli_refuses_digest_and_historical_recovery_without_host_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            unpacked = unpack_controller(directory)
            script = unpacked / "scripts/axxium_active_mailbox_release.py"
            args = ["--plan", str(unpacked / active.PLAN_PATH), "--plan-sha256", "0" * 64, "--apply"]
            for flags in (args, args + ["--recover-interrupted", "private-fixture-detail"], args + ["--check"]):
                result = subprocess.run([sys.executable, "-B", str(script), *flags],
                                        cwd=directory, capture_output=True, text=True, timeout=15,
                                        env={"PATH": "/fixture/no-host-tools"})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr,
                                 "Active Axxium mailbox release refused. Preserve the native operation journal.\n")

    def test_actual_archive_admission_still_refuses_missing_exact_source_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            unpacked = unpack_controller(directory)
            program = r'''
import contextlib, io, json, pathlib, sys
from unittest import mock
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / 'scripts'))
import axxium_active_mailbox_admission as admission
import axxium_active_mailbox_release as active
import axxium_unified_mailbox_admission as shared
from axxium_production_qualification import Refusal
archive = root / 'unused-stage.zip'
archive.write_bytes(b'not used before qualification')
with mock.patch.object(shared, 'main_ancestor'), mock.patch.object(shared, 'native_file',
        side_effect=[active.PLAN_BYTES, OSError('private-fixture-detail')]), \
        mock.patch.object(shared, 'verify_native_stage') as stage:
    try:
        admission.admit_active_mailbox(active.PLAN_BYTES, 'f' * 40, archive)
    except Refusal as error:
        assert str(error) == 'immutable unified mailbox source qualification unavailable'
    else:
        raise AssertionError('Missing qualification was admitted')
    stage.assert_not_called()
print('isolated missing exact-source qualification refused')
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(unpacked)],
                                    cwd=directory, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "isolated missing exact-source qualification refused\n")


class ActiveMailboxCliTests(unittest.TestCase):
    def test_plan_bytes_digest_and_host_errors_have_one_sanitized_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            plan.write_bytes(active.PLAN_BYTES)
            plan.chmod(0o600)
            argv = ["--plan", str(plan), "--plan-sha256", active.PLAN_SHA256, "--apply"]
            variants = ((active.PLAN_BYTES + b"\n", argv),
                        (active.PLAN_BYTES, [*argv[:-2], "0" * 64, "--apply"]),
                        (b'{"schema":"unreviewed"}', argv))
            for raw, flags in variants:
                plan.write_bytes(raw)
                output, error = io.StringIO(), io.StringIO()
                with (mock.patch.object(active, "Host") as host,
                      redirect_stdout(output), redirect_stderr(error)):
                    self.assertEqual(active.main(flags), 2)
                host.assert_not_called()
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(error.getvalue(),
                                 "Active Axxium mailbox release refused. Preserve the native operation journal.\n")
            plan.write_bytes(active.PLAN_BYTES)
            with (mock.patch.object(active, "Host", side_effect=OSError("private-fixture-detail")),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error):
                self.assertEqual(active.main(argv), 2)
            self.assertNotIn("private-fixture-detail", error.getvalue())


if __name__ == "__main__":
    unittest.main()
