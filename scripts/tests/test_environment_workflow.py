"""Inspect the shipped workflow and run its readiness shell without network.

The curl process double records the actual command and selected exit status.
These controls establish immutable inputs, timeout arguments and propagation;
they do not establish hosted action execution or public application readiness.
"""
import json
import hashlib
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
print(os.environ.get('CURL_BODY','{\"ok\":true}'))
raise SystemExit(int(os.environ['CURL_EXIT']))
"""
CONTROLLER_SHA = "a" * 40
WORKFLOW_IDENTITY = {
    "workflow_sha": CONTROLLER_SHA,
    "workflow_repository": "open-hax/services",
    "workflow_file_path": ".github/workflows/deploy-service-environment.yml",
    "workflow_ref": "open-hax/services/.github/workflows/deploy-service-environment.yml@" + CONTROLLER_SHA,
}
SSH = """import hashlib,json,os,stat,sys
from pathlib import Path
args=sys.argv[1:]
key=Path(args[args.index('-i')+1])
hosts=Path(os.environ['HOME'])/'.ssh/known_hosts'
data=sys.stdin.buffer.read()
Path(os.environ['SSH_CALLS']).write_text(json.dumps({
    'args':args,'stdin_sha256':hashlib.sha256(data).hexdigest(),
    'key_sha256':hashlib.sha256(key.read_bytes()).hexdigest(),
    'key_mode':stat.S_IMODE(key.stat().st_mode),
    'directory_mode':stat.S_IMODE(key.parent.stat().st_mode),
    'hosts_sha256':hashlib.sha256(hosts.read_bytes()).hexdigest()}))
if int(os.environ['SSH_EXIT']):print('synthetic ssh failure',file=sys.stderr)
raise SystemExit(int(os.environ['SSH_EXIT']))
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

    def run_controller(self, context=WORKFLOW_IDENTITY, controller=CONTROLLER_SHA, **overrides):
        step = self.workflow["jobs"]["admit"]["steps"][0]
        with tempfile.TemporaryDirectory(prefix="environment controller ") as directory:
            root = Path(directory)
            output = root / "output"
            output.write_text("preserved=value\n")
            environment = {"HOME": str(root), "PATH": os.defpath,
                "CONTROLLER_SHA": controller, "SERVICE": "knoxx", "ENVIRONMENT": "testing",
                "PR_NUMBER": "1", "SOURCE_REPOSITORY": "open-hax/knoxx",
                "WORKFLOW_CONTEXT": context if isinstance(context, str) else json.dumps(context),
                "GITHUB_OUTPUT": str(output)}
            environment.update(overrides)
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]],
                cwd=root, env=environment, capture_output=True, text=True, timeout=10, check=False)
            return result, output.read_text()

    def test_native_callee_identity_is_required_before_any_controller_checkout(self):
        jobs = self.workflow["jobs"]
        first = jobs["admit"]["steps"][0]
        self.assertEqual("controller", first.get("id"))
        self.assertEqual("${{ toJSON(job) }}", first["env"].get("WORKFLOW_CONTEXT"))
        self.assertEqual("${{ steps.controller.outputs.controller_sha }}", jobs["admit"]["outputs"].get("controller_sha"))
        for name in ("admit", "build", "deploy"):
            with self.subTest(job=name):
                checkouts = [s for s in jobs[name]["steps"]
                    if s.get("with", {}).get("repository") == "open-hax/services"]
                self.assertEqual(1, len(checkouts))
                expected = ("${{ steps.controller.outputs.controller_sha }}" if name == "admit"
                    else "${{ needs.admit.outputs.controller_sha }}")
                self.assertEqual(expected, checkouts[0]["with"]["ref"])

    def test_matching_native_identity_emits_only_validated_immutable_sha(self):
        for service in ("knoxx", "axxium", "proxx"):
            for environment in ("testing", "staging"):
                with self.subTest(service=service, environment=environment):
                    result, output = self.run_controller(SERVICE=service,
                        SOURCE_REPOSITORY="open-hax/"+service, ENVIRONMENT=environment)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual("preserved=value\ncontroller_sha="+CONTROLLER_SHA+"\n", output)

    def test_independently_mismatched_caller_pin_refuses_without_output(self):
        result, output = self.run_controller(controller="b" * 40)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("preserved=value\n", output)

    def test_missing_malformed_or_wrong_native_identity_fails_closed(self):
        cases = [None, [], 42, "", "{sensitive-native-input",
                 {}, *[{k: v for k, v in WORKFLOW_IDENTITY.items() if k != field}
                       for field in WORKFLOW_IDENTITY]]
        for field, values in {
            "workflow_sha": [None, True, "main", "A"*40, "b"*40, CONTROLLER_SHA+"\n"],
            "workflow_repository": ["open-hax/knoxx", "sensitive-native-input"],
            "workflow_file_path": [".github/workflows/other.yml", "/.github/workflows/deploy-service-environment.yml"],
            "workflow_ref": ["open-hax/services/.github/workflows/deploy-service-environment.yml@refs/heads/main",
                WORKFLOW_IDENTITY["workflow_ref"]+"\n", WORKFLOW_IDENTITY["workflow_ref"].replace(CONTROLLER_SHA, "b"*40)],
        }.items():
            cases.extend({**WORKFLOW_IDENTITY, field: value} for value in values)
        for context in cases:
            with self.subTest(context=context):
                result, output = self.run_controller(context=context)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("preserved=value\n", output)
                self.assertNotIn("sensitive-native-input", result.stdout+result.stderr)

    def test_native_binding_preserves_service_repository_and_pr_validation(self):
        for invalid in ({"SERVICE": "other"}, {"ENVIRONMENT": "production"},
                {"SOURCE_REPOSITORY": "other/knoxx"}, {"PR_NUMBER": "0"},
                {"PR_NUMBER": "1; echo bad"}):
            with self.subTest(invalid=invalid):
                result, output = self.run_controller(**invalid)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("preserved=value\n", output)

    def run_deployment(self, service, environment, exit_code):
        step = next(s for s in self.workflow["jobs"]["deploy"]["steps"]
                    if s.get("name") == "Deploy through the restricted environment receiver")
        with tempfile.TemporaryDirectory(prefix="environment ssh ") as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            ssh = binary / "ssh"
            ssh.write_text("#!"+sys.executable+" -S\n"+SSH)
            ssh.chmod(0o700)
            (root/"artifact").mkdir()
            archive = b"synthetic image archive\x00with inert bytes\n"
            (root/"artifact/images.tar").write_bytes(archive)
            calls = root / "ssh-calls.json"
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]],
                cwd=root, env={"HOME": str(root), "PATH": str(binary)+os.pathsep+os.defpath,
                    "SERVICE": service, "ENVIRONMENT": environment, "SOURCE_SHA": "c"*40,
                    "DEPLOY_SSH_KEY": "synthetic private key", "DEPLOY_KNOWN_HOSTS": "synthetic known hosts",
                    "DEPLOY_TARGET": "synthetic@invalid", "SSH_CALLS": str(calls), "SSH_EXIT": str(exit_code)},
                capture_output=True, text=True, timeout=10, check=False)
            record = json.loads(calls.read_text())
            record["key_removed"] = not (root/".ssh/environment_key").exists()
            record["archive_sha256"] = hashlib.sha256(archive).hexdigest()
            return result, record

    def test_actual_ssh_transfer_has_liveness_bounds_and_retains_receiver_input(self):
        for service in ("knoxx", "axxium", "proxx"):
            for environment in ("testing", "staging"):
                with self.subTest(service=service, environment=environment):
                    result, record = self.run_deployment(service, environment, 0)
                    self.assertEqual(0, result.returncode, result.stderr)
                    args = record["args"]
                    options = [args[i+1] for i, value in enumerate(args) if value == "-o"]
                    self.assertEqual(["IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes"],
                        [x for x in options if not x.startswith(("ConnectTimeout=", "ServerAlive"))])
                    for required in ("ConnectTimeout=15", "ServerAliveInterval=15", "ServerAliveCountMax=3"):
                        self.assertIn(required, options)
                    self.assertEqual(["synthetic@invalid", f"receive {environment} {service} {'c'*40}"], args[-2:])
                    self.assertEqual(record["archive_sha256"], record["stdin_sha256"])
                    self.assertEqual(hashlib.sha256(b"synthetic private key\n").hexdigest(), record["key_sha256"])
                    self.assertEqual(hashlib.sha256(b"synthetic known hosts\n").hexdigest(), record["hosts_sha256"])
                    self.assertEqual(0o600, record["key_mode"])
                    self.assertEqual(0o700, record["directory_mode"])
                    self.assertTrue(record["key_removed"])

    def test_ssh_failure_propagates_and_removes_owned_key(self):
        for service in ("knoxx", "axxium", "proxx"):
            for environment in ("testing", "staging"):
                with self.subTest(service=service, environment=environment):
                    result, record = self.run_deployment(service, environment, 255)
                    self.assertEqual(255, result.returncode, result.stderr)
                    self.assertIn("synthetic ssh failure", result.stderr)
                    self.assertTrue(record["key_removed"])

    def test_deploy_job_has_total_deadline_even_when_peer_answers_keepalives(self):
        self.assertEqual("30", self.workflow["jobs"]["deploy"].get("timeout-minutes"))

    def run_readiness(self, service, environment, exit_code=0, body='{"ok":true}'):
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
                     "CURL_CALLS": str(calls), "CURL_EXIT": str(exit_code), "CURL_BODY": body},
                capture_output=True, text=True, timeout=10, check=False,
            )
            return result, json.loads(calls.read_text())

    def test_both_service_slots_bound_connect_and_complete_request(self):
        for service in ("knoxx", "axxium", "proxx"):
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
                    self.assertEqual(f"https://{environment}.{service}.promethean.rest" + ("/health" if service == "proxx" else "/api/auth/config"), args[-1])
                    self.assertEqual("promethean-${{ inputs.service }}-${{ inputs.environment }}",
                                     self.workflow["jobs"]["deploy"]["concurrency"]["group"])
                    self.assertEqual("false", self.workflow["jobs"]["deploy"]["concurrency"]["cancel-in-progress"])

    def test_proxx_public_readiness_requires_json_true(self):
        for body in ('{"ok":false}', '{"ok":"true"}', '{}', '<html>healthy placeholder</html>'):
            with self.subTest(body=body):
                result,_=self.run_readiness('proxx','testing',body=body)
                self.assertNotEqual(0,result.returncode)

    def test_readiness_timeout_exit_is_not_changed_to_success(self):
        result, _ = self.run_readiness("knoxx", "staging", exit_code=28)
        self.assertEqual(28, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
