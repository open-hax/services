from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/select-proxx-production-artifact.py"
functions = runpy.run_path(SCRIPT)["main"].__globals__
PIN = json.loads(functions["PIN"].read_bytes())
SOURCE = PIN["source"]
ROLLBACK = "3ed25eec53a5bed6dc224f108323ea5d20420a22"
INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
IMAGE_TYPE = "application/vnd.oci.image.manifest.v1+json"


def content(data):
    raw = json.dumps(data).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest(), raw


class ProxxArtifactAdmissionTest(unittest.TestCase):
    def test_exact_pin_matches_and_normal_rollback_does_not(self):
        for source, expected in ((SOURCE, "matched=true\n"), (ROLLBACK, "matched=false\n")):
            with self.subTest(source=source):
                run = subprocess.run(["python3", SCRIPT, "match", source], capture_output=True, text=True)
                self.assertEqual(0, run.returncode, run.stderr)
                self.assertEqual(expected, run.stdout)
        run = subprocess.run(["python3", SCRIPT, "verify", ROLLBACK], capture_output=True, text=True)
        self.assertEqual(1, run.returncode)
        self.assertEqual("", run.stdout)

    def test_matching_verify_emits_only_the_confirmed_index(self):
        for registry_available in (True, False):
            stdout, stderr = StringIO(), StringIO()

            def verify_registry(pin):
                self.assertEqual(PIN, pin)
                if not registry_available:
                    raise subprocess.TimeoutExpired("docker buildx", 30)

            with self.subTest(registry_available=registry_available), patch.dict(functions, {
                "verify_build": lambda pin: self.assertEqual(PIN, pin),
                "verify_registry": verify_registry,
            }), patch("sys.argv", [str(SCRIPT), "verify", SOURCE]), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(0 if registry_available else 1, functions["main"]())
            self.assertEqual(f"image=ghcr.io/open-hax/proxx@{PIN['index']}\n" if registry_available else "",
                             stdout.getvalue())

    def test_pin_must_be_well_formed_even_for_nonmatching_source(self):
        original = functions["PIN"].read_bytes()
        with TemporaryDirectory() as directory:
            file = Path(directory) / "pin.json"
            for content in (
                original.replace(b'"schema":', b'"source": "duplicate", "schema":'),
                original.replace(b'"attempt": 1', b'"attempt": true'),
                original.replace(PIN["index"].encode(), b"mutable:tag"),
            ):
                file.write_bytes(content)
                with self.subTest(content=content[:30]), patch.dict(functions, {"PIN": file}):
                    with self.assertRaises(ValueError):
                        functions["read_pin"](ROLLBACK)

    def test_native_checkout_and_exact_push_are_both_required(self):
        source = f"Build proxx\tRecord resolved source commit\t2026-11-20T18:09:38Z Building proxx from open-hax/proxx@{SOURCE}\n"
        push = f"Build proxx\tBuild and push\t2026-11-20T18:11:38Z {SOURCE}: digest: {PIN['index']} size: 856\n"
        functions["verify_build_log"](PIN, (source + push).encode())
        for log in (
            source,
            push,
            source + push + push,
            source + push.replace(SOURCE + ":", "main:"),
            source + push.replace(PIN["index"], "sha256:" + "a" * 64),
            source.replace("Record resolved source commit", "Untrusted step") + push,
        ):
            with self.subTest(log=log), self.assertRaises(ValueError):
                functions["verify_build_log"](PIN, log.encode())

    def test_wrong_builder_attempt_or_incomplete_job_is_refused_before_logs(self):
        build = PIN["build"]
        run = {
            "id": build["run"], "run_attempt": build["attempt"], "head_sha": build["controller"],
            "head_branch": "main", "path": ".github/workflows/build-images.yml",
            "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
            "repository": {"full_name": "open-hax/services"},
            "head_repository": {"full_name": "open-hax/services"},
        }
        job = {"id": build["job"], "run_id": build["run"], "run_attempt": build["attempt"],
               "name": "Build proxx", "status": "completed", "conclusion": "success"}
        for replies in (
            [{**run, "head_branch": "candidate"}],
            [{**run, "run_attempt": build["attempt"] + 1}],
            [{**run, "head_sha": "a" * 40}],
            [{**run, "head_repository": {"full_name": "fork/services"}}],
            [run, {**job, "conclusion": "failure"}],
            [run, {**job, "name": "Build knoxx-frontend"}],
            [run, job, {"merge_base_commit": {"sha": "b" * 40}}],
        ):
            with self.subTest(replies=replies), patch.dict(functions, {
                "read_gh": lambda _: replies.pop(0),
                "read_cmd": lambda *_: self.fail("unqualified job must not open logs"),
            }), self.assertRaises(ValueError):
                functions["verify_build"](PIN)

    def test_registry_verifies_bytes_amd64_child_and_config(self):
        manifest, child = content({"schemaVersion": 2, "mediaType": IMAGE_TYPE,
                                   "config": {"digest": PIN["amd64_config"]}})
        descriptor = {"digest": manifest, "platform": {"os": "linux", "architecture": "amd64"}}
        index, top = content({"schemaVersion": 2, "mediaType": INDEX_TYPE, "manifests": [descriptor]})
        pin = {**PIN, "index": index, "amd64_manifest": manifest}
        self.verify_registry_fixtures(pin, {index: top, manifest: child})
        for wrong in (
            {index: top + b"\n", manifest: child},
            {index: top, manifest: child.replace(PIN["amd64_config"].encode(), b"invalid")},
        ):
            with self.assertRaises(ValueError):
                self.verify_registry_fixtures(pin, wrong)
        with self.assertRaises(ValueError):
            self.verify_registry_fixtures({**pin, "amd64_config": "sha256:" + "a" * 64},
                                          {index: top, manifest: child})
        duplicate, raw = content({"schemaVersion": 2, "mediaType": INDEX_TYPE,
                                  "manifests": [descriptor, descriptor]})
        with self.assertRaises(ValueError):
            self.verify_registry_fixtures({**pin, "index": duplicate}, {duplicate: raw, manifest: child})

    def verify_registry_fixtures(self, pin, fixtures):
        def from_registry(*args):
            self.assertEqual(("docker", "buildx", "imagetools", "inspect", "--raw"), args[:-1])
            repo, digest = args[-1].split("@", 1)
            self.assertEqual("ghcr.io/open-hax/proxx", repo)
            return fixtures[digest]

        with patch.dict(functions, {"read_cmd": from_registry}):
            functions["verify_registry"](pin)

    def test_authored_workflow_match_step_rejects_wrong_caller_and_ref(self):
        workflow = yaml.load((ROOT / ".github/workflows/deploy-stack-chain.yml").read_bytes(), Loader=yaml.BaseLoader)
        match = next(step["run"] for step in workflow["jobs"]["select-proxx-only"]["steps"] if step.get("id") == "match")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        valid = {"SOURCE": SOURCE,
                 "CALLER_WORKFLOW": "open-hax/services/.github/workflows/deploy-stack.yml@refs/heads/main",
                 "CALLER_REF_PROTECTED": "true", "GITHUB_REF": "refs/heads/main",
                 "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": head}
        cases = (
            ({}, "matched=true\n"),
            ({"SOURCE": ROLLBACK}, "matched=false\n"),
            ({"SOURCE": "main"}, None),
            ({"CALLER_WORKFLOW": "fork/services/.github/workflows/deploy-stack.yml@refs/heads/main"}, None),
            ({"CALLER_REF_PROTECTED": "false"}, None),
            ({"GITHUB_REF": "refs/heads/candidate"}, None),
            ({"GITHUB_EVENT_NAME": "pull_request"}, None),
            ({"GITHUB_SHA": "a" * 40}, None),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides), TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                run = subprocess.run(["bash", "-c", match], cwd=ROOT, capture_output=True, text=True,
                                     env={**os.environ, **valid, **overrides, "GITHUB_OUTPUT": str(output)})
                if expected is None:
                    self.assertNotEqual(0, run.returncode)
                    self.assertFalse(output.exists() and output.read_text())
                else:
                    self.assertEqual(0, run.returncode, run.stderr)
                    self.assertEqual(expected, output.read_text())


if __name__ == "__main__":
    unittest.main()
