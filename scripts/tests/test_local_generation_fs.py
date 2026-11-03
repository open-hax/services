"""Synthetic owner fixtures only; no installed, service or shell effects."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import local_generation_fs as fs


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
        self.policy = fs.fixture_policy(self.root, self.revision)
        (self.root / "owner").write_bytes(fs.json_bytes({
            "schema": "services.local-owner/v1", "owner": "fixture",
            "policy_sha256": fs.digest(self.policy)}))
        (self.root / "owner").chmod(0o600)
        self.engine = fs.Engine(self.policy, self.revision)
        self.addCleanup(self.engine.close)

    def seed(self, generation, operation="apply"):
        return fs.fixture_seed(self.policy, generation, operation)

    def plan(self, generation, operation="apply"):
        result = self.engine.preview(self.seed(generation, operation))
        self.assertEqual(result["status"], "previewed", result)
        return result["plan"]

    def request(self, plan):
        now = int(time.time() * 1000)
        approval = {
            "schema": "services.local-approval/v1", "plan_sha256": fs.digest(plan),
            "services_revision": self.revision, "owner": "fixture",
            "root_sha256": fs.digest({"root": plan["root"], "anchor": plan["expected"]["anchor"]}),
            "operation": plan["operation"],
            "expected_generation": (plan["expected"]["selection"] or {}).get("generation"),
            "policy_sha256": fs.digest(self.policy), "issued_at": now,
            "expires_at": min(now + 60000, plan["expires_at"]),
            "approver": "fixture-owner", "nonce": plan["nonce"]}
        return {"schema": "services.local-request/v1", "action": plan["operation"],
                "plan": plan, "approval": approval, "plan_sha256": fs.digest(plan)}

    def apply(self, generation):
        result = self.engine.execute(self.request(self.plan(generation)))
        self.assertEqual(result["status"], "applied", result)
        return result

    def selection(self):
        path = self.root / "current"
        return json.loads(path.read_bytes()) if path.exists() else None

    def test_positive_apply_a_b_rollback_retained_a_and_effect_free_reads(self):
        before = fs.fixture_snapshot(self.root)
        plan = self.plan("a")
        checked = self.engine.execute({"schema": "services.local-request/v1",
            "action": "check", "plan": plan, "approval": None, "plan_sha256": fs.digest(plan)})
        self.assertEqual(checked["status"], "checked", checked)
        self.assertEqual(checked["target_writes"], 0)
        self.assertEqual(before, fs.fixture_snapshot(self.root))
        self.assertEqual(self.engine.execute(self.request(plan))["status"], "applied")
        a = fs.fixture_snapshot(self.root / "generations" / "a")
        self.apply("b")
        b = fs.fixture_snapshot(self.root / "generations" / "b")
        result = self.engine.execute(self.request(self.plan("a", "rollback")))
        self.assertEqual(result["status"], "rolled-back", result)
        self.assertEqual(self.selection()["generation"], "a")
        self.assertEqual(a, fs.fixture_snapshot(self.root / "generations" / "a"))
        self.assertEqual(b, fs.fixture_snapshot(self.root / "generations" / "b"))
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
            before = fs.fixture_snapshot(self.root)
            result = self.engine.execute(bad)
            self.assertEqual(result["status"], "denied", result)
            self.assertEqual(result["target_writes"], 0)
            self.assertEqual(before, fs.fixture_snapshot(self.root))

    def test_retained_target_metadata_drift_denies_without_writes(self):
        self.apply("a")
        self.apply("b")
        request = self.request(self.plan("a", "rollback"))
        path = self.root / "generations/a/payload/config.edn"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
        before = fs.fixture_snapshot(self.root)
        result = self.engine.execute(request)
        self.assertEqual(result["status"], "denied", result)
        self.assertEqual(before, fs.fixture_snapshot(self.root))

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
        for value in ["1793735800379596064", "1793735800379596065", "-1",
                      "-9223372036854775808", "9223372036854775807"]:
            self.assertTrue(fs.ns64(value))
        for value in ["-0", "01", "9223372036854775808", 1]:
            self.assertFalse(fs.ns64(value))
        self.assertTrue(fs.u64("18446744073709551615"))
        plan = self.plan("a")
        self.assertIsInstance(plan["expected"]["envelope"]["mtime_ns"], str)
        values = [None, "λ", "1793735800379596064", "1793735800379596065",
                  {"z": [0, True], "a": "-9223372036854775808"}]
        script = ('(require \'[cheshire.core :as j] \'[services.local-generation :as l]) '
                  '(println (j/generate-string (mapv l/canonical (j/parse-string (slurp *in*))))))')
        process = subprocess.run(["bb", "-cp", "src", "-e", script],
            input=json.dumps(values), text=True, capture_output=True,
            cwd=SCRIPTS.parent, timeout=15, check=True)
        self.assertEqual(json.loads(process.stdout), [fs.canonical(v).decode() for v in values])

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
                    trees = {g: fs.fixture_snapshot(fixture.root / "generations" / g) for g in ("a", "b")}
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
                        self.assertEqual(trees[generation], fs.fixture_snapshot(fixture.root / "generations" / generation), event)
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
