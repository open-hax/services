#!/usr/bin/env python3
"""Exercise phase isolation, receipt admission and verifier failure paths."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
spec = importlib.util.spec_from_file_location("deploy_chain", ROOT / "scripts/check-deploy-chain.py")
chain_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chain_guard)


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def step(name):
    return next(s for s in workflow("deploy-digitalocean.yml")["jobs"]["deploy"]["steps"] if s.get("name") == name)


class WebsiteDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.entry = workflow("deploy-stack.yml")
        self.chain = workflow("deploy-stack-chain.yml")
        self.phases = workflow("deploy-website-phases.yml")

    def test_current_graph_is_scoped_and_closed(self):
        self.assertEqual([], chain_guard.website_path_errors(self.entry, self.chain, self.phases))
        self.assertEqual("digitalocean-production-host", self.entry["jobs"]["deploy"]["concurrency"]["group"])
        self.assertEqual("false", self.entry["jobs"]["deploy"]["concurrency"]["cancel-in-progress"])

    def test_unscoped_application_build_or_unlocked_phase_is_rejected(self):
        broken = copy.deepcopy(self.chain)
        del broken["jobs"]["build-proxx"]["if"]
        self.assertTrue(chain_guard.website_path_errors(self.entry, broken, self.phases))
        broken = copy.deepcopy(self.phases)
        broken["jobs"]["validate"]["steps"][0]["run"] = "true"
        self.assertTrue(chain_guard.website_path_errors(self.entry, self.chain, broken))

    def test_candidate_and_proof_dependencies_cannot_drift(self):
        for field, value in (("image", "ghcr.io/open-hax/website:main"), ("source_sha", "main")):
            broken = copy.deepcopy(self.phases)
            broken["jobs"]["production"]["with"][field] = value
            self.assertTrue(chain_guard.website_path_errors(self.entry, self.chain, broken))
        broken = copy.deepcopy(self.phases)
        broken["jobs"]["production"]["needs"] = "stage"
        self.assertTrue(chain_guard.website_path_errors(self.entry, self.chain, broken))

    def test_knoxx_deployment_rejects_mutable_tags_for_every_built_image(self):
        broken = copy.deepcopy(self.chain)
        broken["jobs"]["deploy-knoxx"]["with"]["image"] = "${{ needs.build-knoxx-backend.outputs.image }}"
        self.assertTrue(chain_guard.website_path_errors(self.entry, broken, self.phases))
        for key in ("KNOXX_FRONTEND_IMAGE", "KNOXX_DEVTOOLS_IMAGE"):
            broken = copy.deepcopy(self.chain)
            extra = json.loads(broken["jobs"]["deploy-knoxx"]["with"]["extra_env"])
            extra[key] = extra[key].split("@", 1)[0]
            broken["jobs"]["deploy-knoxx"]["with"]["extra_env"] = json.dumps(extra)
            self.assertTrue(chain_guard.website_path_errors(self.entry, broken, self.phases), key)

    def test_false_lock_and_invalid_phase_fail_before_host_operations(self):
        for environment, service, lock in (("staging", "website", "false"), ("staging", "knoxx", "true")):
            result, _ = self.contract(environment, service, lock)
            self.assertNotEqual(0, result.returncode)

    def contract(self, environment, service="website", lock="true"):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "outputs"
            result = subprocess.run(["bash", "-c", step("Load and validate host contract")["run"]],
                cwd=ROOT, env={**os.environ, "DEPLOY_ENVIRONMENT": environment, "SERVICE": service,
                              "CALLER_HOLDS_PRODUCTION_HOST_LOCK": lock, "GITHUB_OUTPUT": str(output)},
                text=True, capture_output=True)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            return result, values

    def test_stage_contract_has_distinct_paths_project_port_and_hostname(self):
        production, prod = self.contract("production")
        staging, stage = self.contract("staging")
        self.assertEqual(0, production.returncode, production.stderr)
        self.assertEqual(0, staging.returncode, staging.stderr)
        for key in ("runtime_path", "state_path", "state_root", "reports_root", "compose_project", "website_port", "website_host"):
            self.assertNotEqual(prod[key], stage[key], key)
        self.assertEqual("/srv/open-hax/state", prod["state_root"])
        self.assertEqual("/srv/open-hax/state/staging", stage["state_root"])
        self.assertEqual("website-staging", stage["compose_project"])
        self.assertEqual("8082", stage["website_port"])
        self.assertEqual("true", stage["website_require_tls"])
        self.assertEqual("/srv/open-hax/state/caddy/managed", stage["ingress_state"])

    def test_observed_receipt_rejects_failed_health_or_any_candidate_mismatch(self):
        image = "ghcr.io/open-hax/website:" + "a" * 40 + "@sha256:" + "b" * 64
        source = "a" * 40
        services = "c" * 40
        proof = next(s for s in self.phases["jobs"]["stage-proof"]["steps"] if "STAGE_RECEIPT" in s.get("env", {}))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            nginx = directory / "digitalocean/services/website/nginx.conf"
            nginx.parent.mkdir(parents=True)
            shutil.copy(ROOT / "digitalocean/services/website/nginx.conf", nginx)
            import hashlib
            receipt = {"service": "website", "phase": "staging", "health": "success", "image": image,
                       "sourceSha": source, "servicesSha": services, "nginxSha": hashlib.sha256(nginx.read_bytes()).hexdigest(),
                       "composeProject": "website-staging", "publicHost": "staging.website.promethean.rest"}
            for key in (None, "health", "image", "sourceSha", "servicesSha", "nginxSha", "composeProject", "phase"):
                candidate = {**receipt, **({key: "wrong"} if key else {})}
                result = subprocess.run(["bash", "-c", proof["run"]], cwd=directory,
                    env={**os.environ, "STAGE_RECEIPT": json.dumps(candidate), "IMAGE": image,
                         "SOURCE_SHA": source, "SERVICES_SHA": services}, text=True, capture_output=True)
                self.assertEqual(key is None, result.returncode == 0, key)

    def test_missing_production_qualification_is_an_actual_failure(self):
        command = self.phases["jobs"]["production-qualification"]["steps"][0]["run"]
        result = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("unsupported", result.stdout)

    def test_staging_ingress_preserves_other_routes_and_rolls_back_failures(self):
        ingress = step("Install website staging ingress")
        self.assertEqual("inputs.service == 'website' && inputs.environment == 'staging'", ingress["if"])
        command = ingress["run"].split("<<'REMOTE'\n", 1)[1].rsplit("\nREMOTE", 1)[0]
        for mode in ("ok", "missing-import", "missing-mount", "validation-failure", "reload-failure"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                runtime = directory / "runtime"
                runtime.mkdir()
                managed = directory / "managed"
                managed.mkdir()
                target = managed / "website-staging.caddy"
                target.write_text("old\n")
                sibling = managed / "federation.caddy"
                sibling.write_text("preserved federation route\n")
                candidate = runtime / "website-staging.caddy.candidate"
                candidate.write_text("new staged route\n")
                import hashlib
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                binary = directory / "bin"
                binary.mkdir()
                self.write(binary / "docker", '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:]; mode=os.environ['MODE']; managed=Path(os.environ['MANAGED'])
if a[0]=='compose': print('caddy-container')
elif a[0]=='inspect': print(json.dumps([{'Source':'wrong' if mode=='missing-mount' else str(managed),'Destination':'/etc/caddy/managed','RW':False}]))
elif 'cat' in a: print('no managed import' if mode=='missing-import' else 'import /etc/caddy/managed/*.caddy')
elif 'caddy' in a:
 op=a[a.index('caddy')+1]
 with (managed/'calls.log').open('a') as f: f.write(op+'\\n')
 changed=(managed/'website-staging.caddy').read_text()!='old\\n'
 if changed and ((mode=='validation-failure' and op=='validate') or (mode=='reload-failure' and op=='reload')): sys.exit(1)
else: sys.exit(1)
''')
                result = subprocess.run(["bash", "-euo", "pipefail", "-s", "--", str(runtime),
                                         str(directory), str(managed), digest], input=command,
                    env={**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"], "MODE": mode, "MANAGED": str(managed)},
                    text=True, capture_output=True)
                self.assertEqual(mode == "ok", result.returncode == 0, result.stdout + result.stderr)
                self.assertEqual("new staged route\n" if mode == "ok" else "old\n", target.read_text())
                self.assertEqual("preserved federation route\n", sibling.read_text())
                if mode in ("validation-failure", "reload-failure"):
                    self.assertTrue((managed / "calls.log").read_text().endswith("validate\nreload\n"))

    def test_verifier_requires_digest_alias_real_artifact_absence_and_stage_tls(self):
        for mode, success in (("ok", True), ("wrong-image", False), ("wrong-alias", False),
                              ("html-fallback", False), ("wrong-locale", False), ("dns-absent", False), ("tls-failure", False)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                binary = directory / "bin"
                binary.mkdir()
                self.write(binary / "docker", '''#!/usr/bin/env python3
import json,os,sys
a=sys.argv[1:]; mode=os.environ['MODE']
if a[0]=='compose': print('stage-container')
elif a[0]=='inspect':
 f=a[a.index('--format')+1]
 if f=='{{.Config.Image}}': print('wrong' if mode=='wrong-image' else os.environ['WEBSITE_IMAGE'])
 elif f=='{{json .NetworkSettings.Networks}}': print(json.dumps({'open-hax':{'Aliases':['wrong' if mode=='wrong-alias' else 'website-staging']}}))
 else: print('false')
else: sys.exit(1)
''')
                self.write(binary / "getent", '''#!/usr/bin/env python3
import os
if os.environ['MODE']!='dns-absent': print('157.245.125.134 STREAM stage')
''')
                self.write(binary / "curl", '''#!/usr/bin/env python3
import os,sys
a=sys.argv[1:]; url=next(x for x in a if x.startswith(('http://','https://'))); mode=os.environ['MODE']
if url.startswith('https://') and mode=='tls-failure': sys.exit(60)
if '-w' in a:
 fmt=a[a.index('-w')+1]
 if fmt=='%{content_type}': print('text/html',end='')
 else: print('200' if mode=='html-fallback' or '/published/' not in url else '404',end='')
else:
 loc=next((x for x in ('es','fr','de','ja') if '/'+x+'/' in url),'en')
 if mode=='wrong-locale': loc='en'
 print('<html lang="'+loc+'"><div id="root"></div><script src="/cljs/app.js"></script><link href="/app.css"></html>')
''')
                content = directory / "content"
                content.mkdir(mode=0o755)
                environment = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"], "MODE": mode,
                    "WEBSITE_IMAGE": "ghcr.io/open-hax/website:" + "a" * 40 + "@sha256:" + "b" * 64,
                    "WEBSITE_SOURCE_SHA": "a" * 40, "WEBSITE_PHASE": "staging", "WEBSITE_PROJECT": "website-staging",
                    "WEBSITE_NETWORK_ALIAS": "website-staging", "WEBSITE_REQUIRE_PUBLIC_TLS": "true",
                    "WEBSITE_LOOPBACK_PORT": "8082", "WEBSITE_CONTENT_ROOT": str(content), "WEBSITE_CONTENT_URL_PREFIX": "/published/",
                    "WEBSITE_CONTENT_BUDGET_MB": "2048", "WEBSITE_PUBLIC_HOST": "staging.website.promethean.rest", "WEBSITE_HOST_ADDRESS": "157.245.125.134"}
                result = subprocess.run(["bash", str(ROOT / "digitalocean/services/website/verify.sh")],
                                        cwd=directory, env=environment, text=True, capture_output=True)
                self.assertEqual(success, result.returncode == 0, result.stdout + result.stderr)

    @staticmethod
    def write(path, content):
        path.write_text(content)
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
