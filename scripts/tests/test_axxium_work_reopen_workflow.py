"""Exercise the new Work scopes without contacting GitHub or the production host."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "68aa511626ab28ea3d93684c9c464b8c13edbb17"


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def run_step(step, directory, env):
    return subprocess.run(
        ["bash", "-c", step["run"]], cwd=directory, env={"PATH": os.environ["PATH"], **env},
        capture_output=True, text=True, timeout=10,
    )


class WorkReopenWorkflowTests(unittest.TestCase):
    def test_scope_authorization_requires_protected_main_and_exact_source_role(self):
        step = workflow("deploy-stack.yml")["jobs"]["authorize"]["steps"][0]
        defaults = {
            "CALLER_REF": "refs/heads/main", "CALLER_REF_PROTECTED": "true",
            "EVENT_NAME": "workflow_dispatch", "AXXIUM_RECOVER": "false",
            "INCLUDE_WEBSITE": "false", "WEBSITE_IMAGE": "", "AXXIUM_SHA": SOURCE,
            "DEPLOY_SCOPE": "axxium-work-pilot-reopen",
        }
        for overrides, allowed in (
            ({}, True),
            ({"CALLER_REF_PROTECTED": "false"}, False),
            ({"CALLER_REF": "refs/heads/topic"}, False),
            ({"AXXIUM_SHA": "main"}, False),
            ({"INCLUDE_WEBSITE": "true"}, False),
            ({"DEPLOY_SCOPE": "axxium-work-pilot-close", "AXXIUM_SHA": ""}, True),
            ({"DEPLOY_SCOPE": "axxium-work-pilot-close"}, False),
            ({"DEPLOY_SCOPE": "axxium-work-pilot-close", "AXXIUM_SHA": "", "CALLER_REF_PROTECTED": "false"}, False),
        ):
            with self.subTest(overrides=overrides), TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                result = run_step(step, directory, {**defaults, **overrides, "GITHUB_OUTPUT": str(output)})
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                self.assertEqual(output.exists(), allowed)

    def test_close_checks_canonical_caller_lock_and_source_before_ssh(self):
        job = workflow("deploy-axxium-work-activation.yml")["jobs"]["operate"]
        self.assertEqual(job["environment"], "production")
        step = job["steps"][1]
        self.assertNotIn("SSH_PRIVATE_KEY", step["env"])
        defaults = {
            "ACTION": "close", "SOURCE_SHA": "", "CONTROLLER_SHA": "a" * 40, "CALLER_LOCKED": "true",
            "CALLER_WORKFLOW": "open-hax/services/.github/workflows/deploy-stack.yml@refs/heads/main",
            "CALLER_REF_PROTECTED": "true",
        }
        for overrides, allowed in (
            ({}, True),
            ({"CALLER_LOCKED": "false"}, False),
            ({"CALLER_WORKFLOW": "other"}, False),
            ({"CALLER_REF_PROTECTED": "false"}, False),
            ({"SOURCE_SHA": SOURCE}, False),
        ):
            with self.subTest(overrides=overrides), TemporaryDirectory() as directory:
                result = run_step(step, directory, {**defaults, **overrides})
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                self.assertEqual(list(Path(directory).iterdir()), [])
