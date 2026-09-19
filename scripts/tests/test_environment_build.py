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
            (root / "dist/cljs-runtime").mkdir(parents=True, exist_ok=True)
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


if __name__ == "__main__":
    unittest.main()
