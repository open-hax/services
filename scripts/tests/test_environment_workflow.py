"""Inspect the shipped workflow and run its readiness shell without network.

The curl process double records the actual command and selected exit status.
These controls establish immutable inputs, timeout arguments and propagation;
they do not establish hosted action execution or public application readiness.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy-service-environment.yml"
CURL = """import json,os,sys
from pathlib import Path
Path(os.environ['CURL_CALLS']).write_text(json.dumps(sys.argv[1:]))
raise SystemExit(int(os.environ['CURL_EXIT']))
"""


class EnvironmentWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.source = WORKFLOW.read_text()
        self.workflow = yaml.load(self.source, Loader=yaml.BaseLoader)

    def test_every_action_input_is_immutable_and_version_annotated(self):
        actions = [line.strip() for line in self.source.splitlines()
                   if re.match(r"\s*(?:-\s*)?uses:", line)]
        self.assertTrue(actions, "Exercise the actual workflow action inputs")
        for line in actions:
            with self.subTest(action=line):
                self.assertRegex(line, r"^(?:-\s*)?uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+(?:\.\d+)*$")

    def test_clojure_cli_is_a_concrete_version(self):
        steps = self.workflow["jobs"]["build"]["steps"]
        setup = next(step for step in steps
                     if step.get("uses", "").startswith("DeLaGuardo/setup-clojure@"))
        self.assertRegex(setup["with"]["cli"], r"^\d+\.\d+\.\d+\.\d+$")

    def run_readiness(self, service, environment, exit_code=0):
        step = next(step for step in self.workflow["jobs"]["deploy"]["steps"]
                    if step.get("name") == "Verify public HTTPS and readiness")
        with tempfile.TemporaryDirectory(prefix="environment readiness ") as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            curl = binary / "curl"
            curl.write_text("#!" + sys.executable + " -S\n" + CURL)
            curl.chmod(0o700)
            calls = root / "calls.json"
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", step["run"]],
                env={"HOME": str(root), "PATH": str(binary) + os.pathsep + os.defpath,
                     "SERVICE": service, "ENVIRONMENT": environment,
                     "CURL_CALLS": str(calls), "CURL_EXIT": str(exit_code)},
                capture_output=True, text=True, timeout=10, check=False,
            )
            return result, json.loads(calls.read_text())

    def test_both_service_slots_bound_connect_and_complete_request(self):
        for service in ("knoxx", "axxium"):
            for environment in ("testing", "staging"):
                with self.subTest(service=service, environment=environment):
                    result, args = self.run_readiness(service, environment)
                    self.assertEqual(0, result.returncode, result.stderr)
                    for flag in ("--connect-timeout", "--max-time"):
                        self.assertIn(flag, args, "A stalled request must release the serialized slot")
                    connect = float(args[args.index("--connect-timeout") + 1])
                    request = float(args[args.index("--max-time") + 1])
                    self.assertGreater(connect, 0)
                    self.assertLessEqual(connect, request)
                    self.assertLessEqual(request, 20)
                    self.assertEqual("8", args[args.index("--retry") + 1])
                    self.assertEqual("5", args[args.index("--retry-delay") + 1])
                    self.assertIn("--retry-all-errors", args)
                    self.assertIn("--fail", args)
                    self.assertEqual(f"https://{environment}.{service}.promethean.rest/api/auth/config", args[-1])
                    self.assertEqual("promethean-${{ inputs.service }}-${{ inputs.environment }}",
                                     self.workflow["jobs"]["deploy"]["concurrency"]["group"])
                    self.assertEqual("false", self.workflow["jobs"]["deploy"]["concurrency"]["cancel-in-progress"])

    def test_readiness_timeout_exit_is_not_changed_to_success(self):
        result, _ = self.run_readiness("knoxx", "staging", exit_code=28)
        self.assertEqual(28, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
