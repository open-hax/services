"""Exercise the real build shell with isolated tool doubles, not Docker/network.

The doubles model declared output paths only. These tests establish orchestration
and fail-closed packaging; they do not establish compiler or image correctness.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


BUILDER = Path(__file__).resolve().parents[1] / "build-service-environment.sh"
SOURCE_SHA = "1" * 40
SHELL = '<!doctype html><main id="root">application shell</main>\n'

# A copied executable gets its tool identity from argv[0]. No external command
# or inherited credential is used by any double.
TOOL_DOUBLE = r'''
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with Path(os.environ["TRACE_FILE"]).open("a") as out:
    out.write(json.dumps({"tool": tool, "args": args}) + "\n")

def put(path, text="built fixture\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

if tool == "git":
    if args[:1] != ["-C"] or args[2:] != ["rev-parse", "HEAD"]:
        raise SystemExit("unexpected git invocation")
    print(os.environ["SOURCE_SHA"])
elif tool == "pnpm":
    if args[:1] not in (["-C"], ["--dir"]):
        raise SystemExit("expected an explicit pnpm working directory")
    root, command = Path(args[1]), args[2:]
    if command == ["exec", "shadow-cljs", "compile", "server"]:
        code = int(os.environ.get("COMPILE_EXIT", "0"))
        if code:
            raise SystemExit(code)
        if os.environ.get("OMIT_BACKEND") != "server.js":
            put(root / "dist/server.js")
        if os.environ.get("OMIT_BACKEND") != "cljs-runtime":
            runtime = root / "dist/cljs-runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            kind = os.environ.get("RUNTIME_KIND", "javascript")
            if kind == "javascript":
                put(runtime / "runtime.js", "export const compiledFixture = true;\n")
            elif kind == "non-javascript":
                put(runtime / "runtime.js.map", "{}\n")
            elif kind == "symlink":
                target = root / "generated/runtime.js"
                put(target, "export const compiledFixture = true;\n")
                (runtime / "runtime.js").symlink_to(target)
            elif kind != "empty":
                raise SystemExit("unexpected runtime fixture: " + kind)
    elif root.name == "frontend" and command in (["build"], ["run", "build"]):
        for name in ("app.css", "cljs/app.js", "bridge/style.css"):
            if name != os.environ.get("OMIT_FRONTEND"):
                put(root / "dist" / name)
    elif "deploy" in command:
        put(Path(command[-1]) / "dist/index.js")
    elif not ("install" in command or command in (["typecheck"], ["test"])
              or (root.name == "openplanner" and command[-2:] == ["run", "build"])):
        raise SystemExit("unexpected pnpm invocation: " + repr(command))
elif tool == "npm":
    if args not in (["ci", "--ignore-scripts"], ["run", "typecheck"],
                    ["test"], ["run", "build"]):
        raise SystemExit("unexpected npm invocation")
elif tool == "docker":
    if args[:1] == ["save"]:
        print("isolated image archive fixture")
    elif args[:1] != ["build"]:
        raise SystemExit("unexpected docker invocation")
else:
    raise SystemExit("unexpected tool")
'''


class EnvironmentBuildTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="environment build ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.controller = self.root / "controller"
        self.script = self.controller / "scripts/build-service-environment.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(BUILDER, self.script)
        for name in ("digitalocean/services/knoxx/Dockerfile.backend-sdk",
                     "digitalocean/environments/KnoxxFrontend.Caddyfile",
                     "digitalocean/environments/Dockerfile.frontend"):
            self.put(self.controller / name, "isolated controller fixture\n")
        self.source = self.root / "workspace/knoxx"
        for name in ("backend", "frontend/dist", "contracts"):
            (self.source / name).mkdir(parents=True)
        (self.source.parent / "openplanner").mkdir()
        self.put(self.source / "frontend/index.html", SHELL)
        self.artifact = self.root / "artifact"
        self.trace = self.root / "trace.jsonl"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for tool in ("git", "pnpm", "npm", "docker"):
            executable = bin_dir / tool
            executable.write_text("#!" + sys.executable + " -S\n" + TOOL_DOUBLE)
            executable.chmod(0o700)
        self.environment = {
            "PATH": str(bin_dir) + os.pathsep + os.defpath,
            "HOME": str(self.root),
            "LANG": "C.UTF-8",
            "TRACE_FILE": str(self.trace),
            "SOURCE_SHA": SOURCE_SHA,
        }

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_builder(self, service="knoxx", sha=SOURCE_SHA, **environment):
        return subprocess.run(
            ["bash", str(self.script), service, sha, str(self.source), str(self.artifact)],
            env={**self.environment, **environment}, cwd=self.root,
            text=True, capture_output=True, timeout=15, check=False,
        )

    def calls(self, tool=None):
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []
        return [call for call in calls if tool is None or call["tool"] == tool]

    def allow_legacy_shell(self):
        # Isolate the compile/output guard tests from the original shell-path bug.
        self.put(self.source / "frontend/public/index.html", "wrong public shell\n")

    def test_cold_knoxx_build_compiles_before_packaging(self):
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        compile_index = next(i for i, call in enumerate(calls)
                             if call["args"][-4:] == ["exec", "shadow-cljs", "compile", "server"])
        first_docker = next(i for i, call in enumerate(calls) if call["tool"] == "docker")
        self.assertLess(compile_index, first_docker)
        self.assertTrue((self.source / "backend/dist/server.js").is_file())
        self.assertTrue((self.source / "backend/dist/cljs-runtime").is_dir())
        runtime = self.source / "backend/dist/cljs-runtime/runtime.js"
        self.assertTrue(runtime.is_file())
        self.assertFalse(runtime.is_symlink())
        self.assertEqual((self.source / "frontend/dist/index.html").read_text(), SHELL)
        digest = hashlib.sha256((self.artifact / "images.tar").read_bytes()).hexdigest()
        self.assertEqual((self.artifact / "images.sha256").read_text().strip(), digest)

    def test_application_shell_wins_over_public_shell(self):
        self.allow_legacy_shell()
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.source / "frontend/dist/index.html").read_text(), SHELL)

    def test_compiler_failure_stops_before_docker(self):
        self.allow_legacy_shell()
        result = self.run_builder(COMPILE_EXIT="41")
        self.assertEqual(result.returncode, 41, result.stderr)
        self.assertEqual(self.calls("docker"), [])

    def assert_missing_output_refused(self, **environment):
        self.allow_legacy_shell()
        result = self.run_builder(**environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("docker"), [])
        self.assertFalse((self.artifact / "images.tar").exists())

    def test_missing_server_bundle_is_refused(self):
        self.assert_missing_output_refused(OMIT_BACKEND="server.js")

    def test_missing_runtime_tree_is_refused(self):
        self.assert_missing_output_refused(OMIT_BACKEND="cljs-runtime")

    def test_empty_runtime_tree_is_refused(self):
        self.assert_missing_output_refused(RUNTIME_KIND="empty")

    def test_non_javascript_runtime_tree_is_refused(self):
        self.assert_missing_output_refused(RUNTIME_KIND="non-javascript")

    def test_symlink_only_runtime_tree_is_refused(self):
        self.assert_missing_output_refused(RUNTIME_KIND="symlink")

    def test_missing_application_css_is_refused(self):
        self.assert_missing_output_refused(OMIT_FRONTEND="app.css")

    def test_missing_application_javascript_is_refused(self):
        self.assert_missing_output_refused(OMIT_FRONTEND="cljs/app.js")

    def test_missing_bridge_css_is_refused(self):
        self.assert_missing_output_refused(OMIT_FRONTEND="bridge/style.css")

    def test_wrong_source_revision_stops_before_build_tools(self):
        result = self.run_builder(sha="2" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([call["tool"] for call in self.calls()], ["git"])

    def test_axxium_keeps_its_own_build_path(self):
        result = self.run_builder(service="axxium")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("pnpm"), [])
        self.assertEqual([call["args"] for call in self.calls("npm")],
                         [["ci", "--ignore-scripts"], ["run", "typecheck"], ["test"], ["run", "build"]])
        self.assertEqual([call["args"][0] for call in self.calls("docker")], ["build", "save"])


class EnvironmentPreparationTest(unittest.TestCase):
    """Real Node preparer and Compose config parsing; no Docker daemon or network."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="environment preparation ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.slots = self.root / "slots"
        self.script = BUILDER.with_name("prepare-service-environments.mjs")
        self.templates = BUILDER.parents[1] / "digitalocean/environments"
        self.environment = {"PATH": os.environ["PATH"], "TMPDIR": str(self.root)}
        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)

    def prepare(self, **environment):
        return subprocess.run(["node", str(self.script), str(self.slots), str(self.templates)],
                              env={**self.environment, **environment}, text=True,
                              capture_output=True, timeout=20)

    def env_file(self, phase="testing", service="knoxx"):
        return self.slots / phase / service / ".env"

    def assert_refused_unchanged(self, path, **environment):
        before = path.read_bytes()
        result = self.prepare(**environment)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, path.read_bytes())
        self.assertNotIn("PRIVATE-CONTROL-DO-NOT-PRINT", result.stdout + result.stderr)
        return result

    def test_generated_slots_recheck_without_rotating_secret_files(self):
        paths = [path for path in self.slots.rglob("*") if path.is_file() and path.name != "compose.yaml"]
        before = {str(path): path.read_bytes() for path in paths}
        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, {str(path): path.read_bytes() for path in paths})

    def test_comment_or_duplicate_cannot_mask_wrong_effective_public_origin(self):
        for phase in ("testing", "staging"):
            for service in ("knoxx", "axxium"):
                for mode in ("comment", "duplicate", "comment-only"):
                    with self.subTest(phase=phase, service=service, mode=mode):
                        path = self.env_file(phase, service)
                        original = path.read_text();key = service.upper() + "_PUBLIC_BASE_URL"
                        expected = f"{key}=https://{phase}.{service}.promethean.rest\n"
                        replacement = ("#" + expected if mode != "duplicate" else expected)
                        if mode != "comment-only": replacement += key + "=https://wrong.invalid\n"
                        path.write_text(original.replace(expected, replacement))
                        self.assert_refused_unchanged(path)
                        path.write_text(original)

    def test_identity_and_listen_ports_are_slot_specific(self):
        for phase in ("testing", "staging"):
            for service, key in (("knoxx", "KNOXX_AXXIUM_ORIGIN"), ("knoxx", "KNOXX_LISTEN_PORT"),
                                 ("axxium", "AXXIUM_LISTEN_PORT")):
                with self.subTest(phase=phase, key=key):
                    path = self.env_file(phase, service);original = path.read_text()
                    path.write_text(original + key + "=wrong\n")
                    self.assert_refused_unchanged(path)
                    path.write_text(original)

    def test_compose_quotes_comments_colons_and_interpolation_remain_supported(self):
        path = self.env_file()
        original = path.read_text()
        text = original.replace("KNOXX_PUBLIC_BASE_URL=https://testing.knoxx.promethean.rest",
            'KNOXX_PUBLIC_BASE_URL : "https://testing.knoxx.promethean.rest" # public')
        text = text.replace("KNOXX_AXXIUM_ORIGIN=https://testing.axxium.promethean.rest",
            "IDENTITY_HOST=testing.axxium.promethean.rest\nKNOXX_AXXIUM_ORIGIN=https://${IDENTITY_HOST}")
        text = text.replace("KNOXX_LISTEN_PORT=18881", "KNOXX_LISTEN_PORT='18881'")
        text += "UNRELATED_SECRET='PRIVATE-CONTROL-DO-NOT-PRINT\n${UNEXPANDED}# literal'\n"
        path.write_text(text)
        result = self.prepare(KNOXX_PUBLIC_BASE_URL="https://ambient.invalid")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(text.encode(), path.read_bytes())
        self.assertNotIn("PRIVATE-CONTROL-DO-NOT-PRINT", result.stdout + result.stderr)

    def test_ambient_values_cannot_hide_bad_file_and_malformed_syntax_is_safe(self):
        path = self.env_file();original = path.read_text()
        key = "KNOXX_AXXIUM_ORIGIN"
        path.write_text(original + key + "=wrong\n")
        self.assert_refused_unchanged(path, **{key: "https://testing.axxium.promethean.rest"})
        path.write_text(original + "UNRELATED_SECRET='PRIVATE-CONTROL-DO-NOT-PRINT\n")
        self.assert_refused_unchanged(path)

    def test_unavailable_or_invalid_parser_is_a_redacted_refusal(self):
        binary = self.root / "bin";binary.mkdir()
        docker = binary / "docker"
        for body in ("echo PRIVATE-CONTROL-DO-NOT-PRINT >&2; exit 71",
                     "echo PRIVATE-CONTROL-DO-NOT-PRINT"):
            with self.subTest(body=body):
                docker.write_text("#!/bin/sh\n" + body + "\n");docker.chmod(0o700)
                self.assert_refused_unchanged(self.env_file(), PATH=str(binary) + os.pathsep + self.environment["PATH"])

    def test_parser_timeout_is_bounded_and_does_not_rewrite_secrets(self):
        binary = self.root / "bin";binary.mkdir()
        docker = binary / "docker"
        docker.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(30)\n");docker.chmod(0o700)
        started = time.monotonic()
        self.assert_refused_unchanged(self.env_file(), PATH=str(binary) + os.pathsep + self.environment["PATH"])
        self.assertLess(time.monotonic() - started, 15)

    def test_parser_receives_no_ambient_values_or_credentials(self):
        binary = self.root / "bin";binary.mkdir()
        docker = binary / "docker"
        docker.write_text('''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
assert set(os.environ) <= {'PATH','LC_CTYPE'}
assert sys.argv[1] == 'compose' and '--env-file' in sys.argv and '-f' in sys.argv
assert sys.argv[sys.argv.index('-f')+1] == '-'
assert sys.argv[-3:] == ['config','--format','json']
path=Path(sys.argv[sys.argv.index('--env-file')+1]);phase,service=path.parts[-3:-1]
values={service.upper()+'_PUBLIC_BASE_URL':f'https://{phase}.{service}.promethean.rest',service.upper()+'_LISTEN_PORT':str((18881 if service=='knoxx' else 18871)+(phase=='staging'))}
if service=='knoxx':values['KNOXX_AXXIUM_ORIGIN']=f'https://{phase}.axxium.promethean.rest'
model=json.load(sys.stdin);assert set(model['services']['slot']['environment'])==set(values)
print(json.dumps({'services':{'slot':{'environment':values}}}))
''');docker.chmod(0o700)
        before = self.env_file().read_bytes()
        result = self.prepare(PATH=str(binary) + os.pathsep + self.environment["PATH"],
                              GH_TOKEN="PRIVATE-CONTROL-DO-NOT-PRINT", DOCKER_HOST="forbidden",
                              KNOXX_PUBLIC_BASE_URL="https://ambient.invalid")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, self.env_file().read_bytes())


if __name__ == "__main__":
    unittest.main()
