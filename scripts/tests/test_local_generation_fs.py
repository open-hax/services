"""Synthetic owner fixtures only; no installed, service or shell effects."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.tests.test_local_generation_law import canonical

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import local_generation_fs as fs


def digest(value):
    return hashlib.sha256(("services.local/v1\n" + canonical(value)).encode()).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def native_identity(path):
    stat = path.lstat()
    return {"device": str(stat.st_dev), "inode": str(stat.st_ino),
            "mode": stat.st_mode, "uid": stat.st_uid, "gid": stat.st_gid,
            "size": stat.st_size, "mtime_ns": str(stat.st_mtime_ns),
            "ctime_ns": str(stat.st_ctime_ns),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}


def fixture_policy(root, revision):
    def pin(name, path):
        return {"id": name, "repo": "open-hax/services", "revision": revision,
                "digest": hashlib.sha256(path.read_bytes()).hexdigest()}
    chain = list(reversed(root.parents)) + [root]
    renderer = pin("fixture-edn-renderer-v1", Path(__file__))
    validator = pin("inert-edn-validator-v1", SCRIPTS / "local_generation_edn.py")
    return {"schema": "services.local-policy/v1", "owner": "fixture",
            "root": list(root.parts[1:]),
            "anchor": {"components": list(root.parts),
                       "identities": [native_identity(p) for p in chain]},
            "services": pin("local-generations-v1", SCRIPTS / "local_generation_fs.py"),
            "formats": [{"format": "edn", "renderer": renderer,
                         "validator": validator, "entry": "inert-edn-v1"}],
            "approvers": ["fixture-owner"], "max_approval_ttl_ms": 900000,
            "max_artifacts": 64, "max_file_bytes": 1048576, "max_total_bytes": 8388608}


def fixture_seed(policy, generation, operation):
    descriptor = json_bytes({"schema": "services.inert-edn/v1", "value": generation})
    payload = ("{:fixture/value " + json.dumps(generation, ensure_ascii=False) + "}\n").encode()
    binding = policy["formats"][0]
    artifact = {"path": ["config.edn"], "format": "edn", "mode": 384,
                "bytes_b64": base64.b64encode(payload).decode(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "renderer": binding["renderer"], "validator": binding["validator"],
                "descriptor_b64": base64.b64encode(descriptor).decode(),
                "descriptor_sha256": hashlib.sha256(descriptor).hexdigest()}
    current = Path("/", *policy["root"], "current")
    selected = json.loads(current.read_bytes()) if current.exists() else None
    now = int(time.time() * 1000)
    return {"owner": policy["owner"], "root": policy["root"], "services": policy["services"],
            "policy_sha256": digest(policy), "manifest": {"schema": "services.local-manifest/v1",
            "owner": "fixture", "generation": generation, "artifacts": [artifact]},
            "operation": operation, "expected_generation": (selected or {}).get("generation"),
            "created_at": now, "expires_at": now + 900000, "nonce": "n-" + os.urandom(16).hex()}


def fixture_snapshot(root):
    result = {}
    for path in [root] + sorted(root.rglob("*")):
        stat = path.lstat()
        result[str(path.relative_to(root))] = {
            "mode": stat.st_mode, "uid": stat.st_uid, "gid": stat.st_gid,
            "mtime_ns": str(stat.st_mtime_ns),
            "bytes": path.read_bytes() if path.is_file() else None,
            "xattrs": [(name, os.getxattr(path, name, follow_symlinks=False))
                       for name in sorted(os.listxattr(path, follow_symlinks=False))]}
    return result


class LocalGenerationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="services-generation-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "owned"
        self.root.mkdir(mode=0o700)
        for name in ("generations", "transactions"):
            (self.root / name).mkdir(mode=0o700)
        (self.root / "lock").touch(mode=0o600)
        self.unrelated = Path(self.tmp.name) / "unrelated"
        self.unrelated.write_bytes(b"not-services-owned")
        self.revision = "a" * 40
        self.policy = fixture_policy(self.root, self.revision)
        (self.root / "owner").write_bytes(json_bytes({
            "schema": "services.local-owner/v1", "owner": "fixture",
            "policy_sha256": digest(self.policy)}))
        (self.root / "owner").chmod(0o600)
        self.engine = fs.Engine(self.policy, self.revision)
        self.addCleanup(self.engine.close)

    def seed(self, generation, operation="apply"):
        return fixture_seed(self.policy, generation, operation)

    def plan(self, generation, operation="apply"):
        result = self.engine.preview(self.seed(generation, operation))
        self.assertEqual(result["status"], "previewed", result)
        return result["plan"]

    def request(self, plan):
        now = int(time.time() * 1000)
        approval = {
            "schema": "services.local-approval/v1", "plan_sha256": digest(plan),
            "services_revision": self.revision, "owner": "fixture",
            "root_sha256": digest({"root": plan["root"], "anchor": plan["expected"]["anchor"]}),
            "operation": plan["operation"],
            "expected_generation": (plan["expected"]["selection"] or {}).get("generation"),
            "policy_sha256": digest(self.policy), "issued_at": now,
            "expires_at": min(now + 60000, plan["expires_at"]),
            "approver": "fixture-owner", "nonce": plan["nonce"]}
        return {"schema": "services.local-request/v1", "action": plan["operation"],
                "plan": plan, "approval": approval, "plan_sha256": digest(plan)}

    def apply(self, generation):
        result = self.engine.execute(self.request(self.plan(generation)))
        self.assertEqual(result["status"], "applied", result)
        return result

    def selection(self):
        path = self.root / "current"
        return json.loads(path.read_bytes()) if path.exists() else None

    def test_positive_apply_a_b_rollback_retained_a_and_effect_free_reads(self):
        before = fixture_snapshot(self.root)
        plan = self.plan("a")
        checked = self.engine.execute({"schema": "services.local-request/v1",
            "action": "check", "plan": plan, "approval": None, "plan_sha256": digest(plan)})
        self.assertEqual(checked["status"], "checked", checked)
        self.assertEqual(checked["target_writes"], 0)
        self.assertEqual(before, fixture_snapshot(self.root))
        self.assertEqual(self.engine.execute(self.request(plan))["status"], "applied")
        a = fixture_snapshot(self.root / "generations" / "a")
        self.apply("b")
        b = fixture_snapshot(self.root / "generations" / "b")
        result = self.engine.execute(self.request(self.plan("a", "rollback")))
        self.assertEqual(result["status"], "rolled-back", result)
        self.assertEqual(self.selection()["generation"], "a")
        self.assertEqual(a, fixture_snapshot(self.root / "generations" / "a"))
        self.assertEqual(b, fixture_snapshot(self.root / "generations" / "b"))
        self.assertEqual(self.unrelated.read_bytes(), b"not-services-owned")

    def test_approval_fields_and_unknown_keys_deny_without_writes(self):
        plan = self.plan("a")
        request = self.request(plan)
        cases = [dict(request, approval=None), dict(request, unexpected=True)]
        for key in request["approval"]:
            bad = copy.deepcopy(request)
            bad["approval"][key] = None
            cases.append(bad)
        for bad in cases:
            before = fixture_snapshot(self.root)
            result = self.engine.execute(bad)
            self.assertEqual(result["status"], "denied", result)
            self.assertEqual(result["target_writes"], 0)
            self.assertEqual(before, fixture_snapshot(self.root))

    def test_retained_target_metadata_drift_denies_without_writes(self):
        self.apply("a")
        self.apply("b")
        request = self.request(self.plan("a", "rollback"))
        path = self.root / "generations/a/payload/config.edn"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
        before = fixture_snapshot(self.root)
        result = self.engine.execute(request)
        self.assertEqual(result["status"], "denied", result)
        self.assertEqual(before, fixture_snapshot(self.root))

    def test_final_check_current_replacement_is_not_a_vacuous_refusal(self):
        self.apply("a")
        request = self.request(self.plan("b"))
        fired = []
        def replace(event):
            if event == "before:transaction-mkdir" and not fired:
                fired.append(event)
                current = self.root / "current"
                temporary = self.root / "foreign"
                temporary.write_bytes(current.read_bytes())
                os.replace(temporary, current)
        self.engine._test_hook = replace
        result = self.engine.execute(request)
        self.assertEqual(result["status"], "denied", result)
        self.assertEqual(result["target_writes"], 0)
        self.assertTrue(fired)

    def test_wire_hash_parity_and_real_native_stat(self):
        plan = self.plan("a")
        self.assertIsInstance(plan["expected"]["envelope"]["mtime_ns"], str)
        values = [None, "λ", "1793735800379596064", "1793735800379596065",
                  {"z": [0, True], "a": "-9223372036854775808"}]
        script = ('(require \'[cheshire.core :as j] \'[services.local-generation :as l]) '
                  '(println (j/generate-string (mapv l/canonical (j/parse-string (slurp *in*)))))')
        process = subprocess.run(["bb", "-cp", "src", "-e", script],
            input=json.dumps(values), text=True, capture_output=True,
            cwd=SCRIPTS.parent, timeout=15, check=True)
        self.assertEqual(json.loads(process.stdout), [canonical(v) for v in values])

    def test_each_partial_effect_restores_or_holds_without_losing_retained_trees(self):
        for operation in ("apply", "rollback"):
            probe = LocalGenerationTest()
            probe.setUp()
            try:
                probe.apply("a")
                probe.apply("b")
                request = probe.request(probe.plan("c" if operation == "apply" else "a", operation))
                trace = []
                probe.engine._test_hook = trace.append
                self.assertIn(probe.engine.execute(request)["status"], ("applied", "rolled-back"))
            finally:
                probe.doCleanups()
            events = sorted({e for e in trace if e.startswith(("before:", "after:"))})
            self.assertGreater(len(events), 15)
            for event in events:
                fixture = LocalGenerationTest()
                fixture.setUp()
                try:
                    fixture.apply("a")
                    fixture.apply("b")
                    request = fixture.request(fixture.plan("c" if operation == "apply" else "a", operation))
                    old = fixture.selection()
                    trees = {g: fixture_snapshot(fixture.root / "generations" / g) for g in ("a", "b")}
                    fired = []
                    def fail(observed):
                        if observed == event and not fired:
                            fired.append(event)
                            raise OSError(5, "synthetic owned fixture fault")
                    fixture.engine._test_hook = fail
                    result = fixture.engine.execute(request)
                    self.assertTrue(fired, event)
                    self.assertIn(result["status"], ("denied", "failed-restored", "held"), (event, result))
                    self.assertEqual(old, fixture.selection(), (event, result))
                    for generation in trees:
                        self.assertEqual(trees[generation], fixture_snapshot(fixture.root / "generations" / generation), event)
                finally:
                    fixture.doCleanups()

    def test_actual_sigkill_fresh_process_recovery_without_original_request(self):
        self.apply("a")
        old = self.selection()
        for checkpoint in ("durable-intent", "after:current-rename"):
            request = self.request(self.plan("killed-" + checkpoint.replace(":", "-")))
            worker = ('import json, os, signal, sys\n'
                'from local_generation_fs import Engine\n'
                'p, r = json.load(sys.stdin)\n'
                'e = Engine(p, p["services"]["revision"])\n'
                'def stop(event):\n'
                '    if event == sys.argv[1]: os.kill(os.getpid(), signal.SIGKILL)\n'
                'e._test_hook = stop\n'
                'e.execute(r)\n')
            process = subprocess.run([sys.executable, "-c", worker, checkpoint],
                input=json.dumps([self.policy, request]), text=True, capture_output=True,
                env={"PATH": os.environ["PATH"], "PYTHONPATH": str(SCRIPTS),
                     "PYTHONDONTWRITEBYTECODE": "1"}, timeout=15)
            self.assertEqual(process.returncode, -9, process.stderr)
            recovery = ('import json, sys\nfrom local_generation_fs import Engine\n'
                'p = json.load(sys.stdin)\n'
                'with Engine(p, p["services"]["revision"]) as e:\n'
                '    print(json.dumps(e.recover()))\n')
            process = subprocess.run([sys.executable, "-c", recovery],
                input=json.dumps(self.policy), text=True, capture_output=True,
                env={"PATH": os.environ["PATH"], "PYTHONPATH": str(SCRIPTS),
                     "PYTHONDONTWRITEBYTECODE": "1"}, timeout=15, check=True)
            result = json.loads(process.stdout)
            self.assertEqual(result["status"], "failed-restored", result)
            self.assertEqual(old, self.selection())


if __name__ == "__main__":
    unittest.main()
