import fcntl
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "hold", ROOT / "digitalocean/scripts/check-knoxx-public-policy-hold.py")
hold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hold)


class KnoxxPolicyStackHoldTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.caddy = self.state / "caddy"
        self.managed = self.caddy / "managed"
        self.receipt = self.caddy / ".knoxx-public-policy-release.json"

    def record(self, phase):
        self.receipt.write_text(json.dumps({
            "phase": phase,
            "controller_sha": "a" * 40,
            "plan_sha256": "b" * 64,
            "incumbent_config_sha256": "c" * 64,
            "candidate_config_sha256": "d" * 64,
        }))
        self.receipt.chmod(0o600)

    def test_fresh_state_and_proven_rollback_allow_stack(self):
        hold.stack_is_clear(self.state)
        self.managed.mkdir(parents=True)
        hold.stack_is_clear(self.state)
        self.record("rolled_back")
        hold.stack_is_clear(self.state)

    def test_activation_and_interrupted_state_hold_stack(self):
        self.managed.mkdir(parents=True)
        for phase in ("prepared", "active", "unexpected"):
            with self.subTest(phase=phase):
                self.record(phase)
                with self.assertRaises(ValueError):
                    hold.stack_is_clear(self.state)

    def test_running_controller_holds_stack_before_prepared_receipt(self):
        self.managed.mkdir(parents=True)
        lock = self.caddy / ".knoxx-public-policy-release.lock"
        with lock.open("wb") as stream:
            lock.chmod(0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                hold.stack_is_clear(self.state)
        hold.stack_is_clear(self.state)

    def test_policy_artifacts_hold_stack_even_without_a_receipt(self):
        self.managed.mkdir(parents=True)
        (self.managed / "knoxx-public-policy.caddy").symlink_to("/missing")
        with self.assertRaises(ValueError):
            hold.stack_is_clear(self.state)

    def test_bad_receipt_or_noncanonical_path_holds_stack(self):
        self.caddy.mkdir(parents=True)
        self.receipt.write_text('{"phase":"rolled_back"}')
        self.receipt.chmod(0o600)
        with self.assertRaises(ValueError):
            hold.stack_is_clear(self.state)
        self.receipt.unlink()
        self.managed.symlink_to(self.caddy, target_is_directory=True)
        with self.assertRaises(ValueError):
            hold.stack_is_clear(self.state)


class KnoxxPolicyWorkflowHoldTest(unittest.TestCase):
    def test_guard_precedes_bootstrap_and_service_render(self):
        for filename, job, step, condition, subsequent in (
            ("digitalocean-host.yml", "host", "Refuse a pending Knoxx policy retirement before stack bootstrap",
             "steps.target.outputs.operation == 'bootstrap'", "Bootstrap host"),
            ("deploy-digitalocean.yml", "deploy", "Refuse a pending Knoxx policy retirement before service changes",
             "inputs.environment == 'production' && (inputs.service == 'knoxx' || inputs.service == 'caddy')",
             "Render runtime environment"),
        ):
            with self.subTest(workflow=filename):
                workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader)
                steps = workflow["jobs"][job]["steps"]
                names = [entry.get("name") for entry in steps]
                guard = steps[names.index(step)]
                self.assertLess(names.index(step), names.index(subsequent))
                self.assertEqual(guard["if"], condition)
                self.assertIn("check-knoxx-public-policy-hold.py", guard["run"])
