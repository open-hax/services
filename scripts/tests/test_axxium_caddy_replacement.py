"""Exercise the generic Caddy preflight without a Docker daemon."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "digitalocean/services/caddy/check-replacement.sh"


class AxxiumCaddyReplacementTest(unittest.TestCase):
    def test_only_unoccupied_ingress_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            docker = directory / "docker"
            docker.write_text(
                '#!/bin/bash\n'
                '[[ "$*" == "container ls --all --filter label=com.docker.compose.project=axxium '
                '--format {{.Names}}" ]] || exit 2\n'
                '[[ "$DOCKER_RESULT" != error ]] || exit 3\n'
                'if [[ "$DOCKER_RESULT" == incumbent ]]; then printf "axxium-pds-1\\n"; fi\n'
            )
            docker.chmod(0o700)
            runtime = directory / "caddy"
            runtime.mkdir()
            asset = runtime / "axxium-did.json"
            for scenario, docker_result, has_asset, expected in (
                ("fresh host", "empty", False, 0),
                ("retained Axxium project", "incumbent", False, 1),
                ("recovery with a dangling DID mount", "empty", True, 1),
                ("cannot read Docker inventory", "error", False, 3),
            ):
                with self.subTest(scenario=scenario):
                    if has_asset:
                        asset.symlink_to(runtime / "missing.json")
                    result = subprocess.run(
                        ["bash", str(GUARD), str(runtime)],
                        env={**os.environ, "PATH": f"{temp}:{os.defpath}", "DOCKER_RESULT": docker_result},
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(expected, result.returncode, result.stderr)
                    asset.unlink(missing_ok=True)

    def test_missing_docker_is_allowed_only_for_initial_host_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            environment = {**os.environ, "PATH": temp}
            command = ["/bin/bash", str(GUARD), str(runtime)]
            before_bootstrap = [*command, "--before-bootstrap"]
            self.assertEqual(0, subprocess.run(before_bootstrap, env=environment, timeout=5).returncode)
            strict = subprocess.run(command, env=environment, capture_output=True, timeout=5)
            self.assertEqual(127, strict.returncode)
            (runtime / "axxium-did.json").symlink_to(runtime / "missing.json")
            held = subprocess.run(before_bootstrap, env=environment, capture_output=True, timeout=5)
            self.assertEqual(1, held.returncode)


class AxxiumCaddyWorkflowTest(unittest.TestCase):
    def test_opted_in_stack_checks_ingress_before_bootstrap_and_before_service_deploy(self):
        workflows = ROOT / ".github/workflows"
        chain = yaml.load((workflows / "deploy-stack-chain.yml").read_text(), Loader=yaml.BaseLoader)["jobs"]
        self.assertEqual(chain["provision-host"]["with"]["include_ingress"], "${{ inputs.include_ingress }}")
        self.assertIn("provision-host", chain["deploy-proxx"]["needs"])
        self.assertIn("deploy-proxx", chain["deploy-knoxx"]["needs"])
        self.assertEqual(chain["deploy-caddy"]["needs"], "deploy-knoxx")

        host = yaml.load((workflows / "digitalocean-host.yml").read_text(), Loader=yaml.BaseLoader)
        bootstrap = next(step for step in host["jobs"]["host"]["steps"] if step.get("name") == "Bootstrap host")
        self.assertEqual(bootstrap["if"], "steps.target.outputs.operation == 'bootstrap'")
        self.assertEqual(bootstrap["env"]["INCLUDE_INGRESS"], "${{ inputs.include_ingress }}")

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            trace = directory / "trace"
            for name in ("ssh", "scp"):
                command = directory / name
                command.write_text(
                    '#!/bin/bash\n'
                    'printf "%s %s\\n" "${0##*/}" "$*" >> "$TRACE"\n'
                    'case "$*" in\n'
                    '  *--before-bootstrap*) exit "$BEFORE" ;;\n'
                    '  */caddy) exit "$AFTER" ;;\n'
                    'esac\n'
                )
                command.chmod(0o700)
            for scenario, opt_in, before, after, expected in (
                ("incumbent", "true", "1", "1", 1),
                ("fresh host", "true", "0", "0", 0),
                ("incumbent found after Docker installation", "true", "0", "1", 1),
                ("ingress excluded", "false", "1", "1", 0),
            ):
                with self.subTest(scenario=scenario):
                    trace.write_text("")
                    result = subprocess.run(
                        ["bash", "-c", bootstrap["run"]], cwd=ROOT,
                        env={**os.environ, "PATH": f"{temp}:{os.defpath}", "TRACE": str(trace),
                             "INCLUDE_INGRESS": opt_in, "BEFORE": before, "AFTER": after},
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(expected, result.returncode, result.stderr)
                    operations = trace.read_text().splitlines()
                    if before == "1" and opt_in == "true":
                        self.assertEqual(len(operations), 1)
                        self.assertIn("--before-bootstrap", operations[0])
                    elif opt_in == "true":
                        self.assertIn("--before-bootstrap", operations[0])
                        self.assertTrue(operations[1].startswith("scp "))
                        self.assertEqual(operations[-2], "ssh production-droplet bash -s")
                        self.assertTrue(operations[-1].endswith("/caddy"))
                    else:
                        self.assertTrue(operations[0].startswith("scp "))
                        self.assertEqual(operations[-1], "ssh production-droplet bash -s")
