#!/usr/bin/env python3
"""Exercise phase isolation, receipt admission and verifier failure paths."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
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

    def test_stack_defaults_and_merge_binding_leave_website_unchanged(self):
        self.assertEqual("false", self.entry["on"]["workflow_dispatch"]["inputs"]["include_website"]["default"])
        self.assertEqual("false", self.chain["on"]["workflow_call"]["inputs"]["include_website"]["default"])
        self.assertEqual("${{ github.event_name == 'workflow_dispatch' && inputs.include_website }}",
                         self.entry["jobs"]["deploy"]["with"]["include_website"])
        self.assertEqual("needs.authorize.outputs.should_deploy == 'true'", self.entry["jobs"]["deploy"]["if"])

    def test_backend_builder_defaults_pass_the_immutable_sdk_guard(self):
        builder = workflow("build-images.yml")
        resolve = next(s for s in builder["jobs"]["build"]["steps"]
                       if s.get("name") == "Resolve service definition")
        production_pin = re.search(r"'([0-9a-f]{40})'",
                                   self.entry["jobs"]["deploy"]["with"]["openplanner_ref"]).group(1)
        self.assertEqual("${{ inputs.openplanner_ref }}", resolve["env"]["OPENPLANNER_REF"])
        for trigger in ("workflow_call", "workflow_dispatch"):
            default = builder["on"][trigger]["inputs"]["openplanner_ref"]["default"]
            for label, reference, expected in (("default", default, 0),
                                               ("immutable override", "1" * 40, 0),
                                               ("moving branch", "main", 2),
                                               ("empty override", "", 2)):
                with self.subTest(trigger=trigger, reference=label), tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary) / "outputs"
                    result = subprocess.run(["bash", "-c", resolve["run"]], cwd=ROOT,
                        env={**os.environ, "SERVICE": "knoxx-backend", "OPENPLANNER_REF": reference,
                             "GITHUB_OUTPUT": str(output)}, text=True, capture_output=True)
                    self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
                    if expected:
                        self.assertFalse(output.exists(), "invalid SDK reference emitted build coordinates")
                        self.assertIn("requires an immutable openplanner_ref", result.stderr)
                    else:
                        self.assertIn("repo=open-hax/knoxx", output.read_text())
                        self.assertIn("context=source/backend", output.read_text())
            with self.subTest(trigger=trigger, pin="release default"):
                self.assertEqual(production_pin, default, "manual SDK default drifted from the release pin")

    def test_authorize_rejects_stack_website_optin_before_deployment(self):
        decide = self.entry["jobs"]["authorize"]["steps"][0]
        self.assertEqual("${{ inputs.include_website }}", decide["env"]["INCLUDE_WEBSITE"])
        for scope, include, status in (("stack", "true", 2), ("stack", "false", 0), ("website", "false", 0)):
            with self.subTest(scope=scope, include=include), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "outputs"
                result = subprocess.run(["bash", "-c", decide["run"]], cwd=ROOT,
                    env={**os.environ, "EVENT_NAME": "workflow_dispatch", "DEPLOY_SCOPE": scope,
                         "INCLUDE_WEBSITE": include, "GITHUB_OUTPUT": str(output)}, text=True, capture_output=True)
                self.assertEqual(status, result.returncode, result.stdout + result.stderr)
                if status:
                    self.assertFalse(output.exists(), "rejected opt-in must emit no deploy authorization")
                    self.assertIn("production qualification is unsupported", result.stderr)
                else:
                    self.assertEqual("should_deploy=true", output.read_text().strip())

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

    def test_stage_proof_guard_requires_exact_public_hostname(self):
        clause = '.publicHost == "staging.website.promethean.rest"'
        for mode, replacement in (("removed", "true"),
                                  ("changed", '.publicHost == "website.promethean.rest"')):
            with self.subTest(mode=mode):
                broken = copy.deepcopy(self.phases)
                proof = next(s for s in broken["jobs"]["stage-proof"]["steps"]
                             if "STAGE_RECEIPT" in s.get("env", {}))
                self.assertIn(clause, proof["run"])
                proof["run"] = proof["run"].replace(clause, replacement)
                self.assertIn("stage proof must bind observed health, source, digest and serving config",
                              chain_guard.website_path_errors(self.entry, self.chain, broken))

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
        for key in ("runtime_path", "state_path", "state_root", "reports_root", "compose_project", "compose_service", "service_compose", "website_port", "website_host"):
            self.assertNotEqual(prod[key], stage[key], key)
        self.assertEqual("/srv/open-hax/state", prod["state_root"])
        self.assertEqual("/srv/open-hax/state/staging", stage["state_root"])
        self.assertEqual("website-staging", stage["compose_project"])
        self.assertEqual("website-staging", stage["compose_service"])
        self.assertEqual("digitalocean/services/website/compose.staging.yaml", stage["service_compose"])
        self.assertEqual("8082", stage["website_port"])
        self.assertEqual("true", stage["website_require_tls"])
        self.assertEqual("/srv/open-hax/state/caddy/managed", stage["ingress_state"])

    def prepare_volume_sources(self, compose, environment):
        # Execute the authored Bash loop, rather than a test-side volume parser.
        command = re.search(r"(?m)^sed -nE .* compose\.yaml \\\n(?:.*\n)*?    done\n",
                            step("Deploy")["run"])
        self.assertIsNotNone(command, "deployment volume-preparation loop is missing")
        # Record mkdir arguments without creating host directories. An empty
        # source fails just as real mkdir does after an unterminated eval.
        recorder = '''mkdir() {
  printf 'mkdir'; printf '\\t%s' "$@"; printf '\\n';
  [ -n "${!#}" ] || { echo 'empty bind source' >&2; return 1; }
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "compose.yaml").write_text(compose)
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", recorder + command.group(0)],
                cwd=directory, env={"PATH": os.environ["PATH"], **environment},
                text=True, capture_output=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        return [line.split("\t")[2] for line in result.stdout.splitlines()
                if line.startswith("mkdir\t-p\t")]

    def test_authored_website_volume_preparation_skips_network_aliases(self):
        environment = {"WEBSITE_CONTENT_ROOT": "/fixture/published", "WEBSITE_MUSIC_ROOT": "/fixture/music"}
        for filename in ("compose.staging.yaml", "compose.yaml"):
            with self.subTest(compose=filename):
                compose = (ROOT / "digitalocean/services/website" / filename).read_text()
                self.assertEqual(["/fixture/published", "/fixture/music"],
                                 self.prepare_volume_sources(compose, environment))

    def test_volume_preparation_preserves_mounts_and_skips_nonmount_scalars(self):
        compose = '''services:
  fixture:
    volumes:
      - ${CONTENT_ROOT:?content must be set}:/published:ro
      - ${OPTIONAL_ROOT:-/fixture/fallback}:/optional:ro
      - ${CONTENT_ROOT}/nested:/nested:ro
      - /fixture/absolute:/absolute:ro
      - ./relative.conf:/relative:ro
      - durable-data:/named
    ports:
      - "127.0.0.1:${PORT:-8082}:8080"
    environment:
      - ${ENVIRONMENT_ONLY:-example}
      - HTTP_ORIGIN=http://example.com
    healthcheck:
      test: ["CMD", "true"]
    networks:
      default:
        aliases:
          - ${NETWORK_ALIAS:-fixture}
          - ${REQUIRED_ALIAS:?alias must be set}
'''
        self.assertEqual(["/fixture/content", "/fixture/fallback", "/fixture/content/nested", "/fixture/absolute"],
                         self.prepare_volume_sources(compose, {"CONTENT_ROOT": "/fixture/content"}))

    def test_compose_service_names_and_explicit_aliases_are_disjoint_between_phases(self):
        # Compose registers the service key as well as explicit aliases. A
        # project name alone cannot isolate DNS on the shared external network.
        def aliases(filename, environment):
            document = yaml.safe_load((ROOT / "digitalocean/services/website" / filename).read_text())
            names = set(document["services"])
            for service in document["services"].values():
                self.assertEqual("open-hax", document["networks"]["default"]["name"])
                for alias in service["networks"]["default"].get("aliases", []):
                    names.add(alias.replace("${WEBSITE_NETWORK_ALIAS:-website-staging}", environment)
                                   .replace("${WEBSITE_NETWORK_ALIAS:-website}", environment))
            return names
        production = aliases("compose.yaml", "website")
        staging = aliases("compose.staging.yaml", "website-staging")
        self.assertEqual({"website"}, production)
        self.assertEqual({"website-staging"}, staging)
        self.assertTrue(production.isdisjoint(staging))

    def test_sync_selects_stage_topology_before_transfer_and_does_not_recreate_unchanged_bytes(self):
        sync = step("Sync service definition")
        self.assertEqual("${{ steps.contract.outputs.service_compose }}", sync["env"]["SERVICE_COMPOSE"])
        real_rsync = shutil.which("rsync")
        self.assertIsNotNone(real_rsync, "the deployment sync test requires rsync")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "digitalocean/services/website"
            source.parent.mkdir(parents=True)
            shutil.copytree(ROOT / "digitalocean/services/website", source)
            remote = directory / "remote"
            remote.mkdir()
            binary = directory / "bin"
            binary.mkdir()
            self.write(binary / "ssh", "#!/usr/bin/env bash\nexit 0\n")
            self.write(binary / "scp", "#!/usr/bin/env bash\nexit 0\n")
            self.write(binary / "rsync", '''#!/usr/bin/env python3
import os,subprocess,sys,yaml
from pathlib import Path
a=sys.argv[1:]; source=Path(a[-2]); remote=Path(os.environ['STAGED_REMOTE'])
assert '--checksum' in a
assert source.resolve()!=Path(os.environ['SERVICE_DIR']).resolve()
assert set(yaml.safe_load((source/'compose.yaml').read_text())['services'])=={'website-staging'}
assert not (source/'compose.staging.yaml').exists()
# Replace only SSH transport; retain real rsync's checksum and itemization.
sys.exit(subprocess.run([os.environ['REAL_RSYNC'], *a[:-2], str(source)+'/', str(remote)+'/']).returncode)
''')
            output = directory / "outputs"
            for attempt in range(2):
                # Fresh checkouts carry new mtimes even when bytes are equal.
                for item in source.iterdir():
                    os.utime(item, (1700000000 + attempt, 1700000000 + attempt))
                output.write_text("")
                result = subprocess.run(["bash", "-c", sync["run"]], cwd=directory,
                    env={**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"],
                         "SERVICE": "website", "SERVICE_DIR": "digitalocean/services/website",
                         "SERVICE_COMPOSE": "digitalocean/services/website/compose.staging.yaml",
                         "RUNTIME_PATH": "/isolated/staging/runtime", "STATE_PATH": "/isolated/staging/state",
                         "STAGED_REMOTE": str(remote), "REAL_RSYNC": real_rsync,
                         "GITHUB_OUTPUT": str(output)}, text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual("definition_changed=" + ("1" if attempt == 0 else "0"), output.read_text().strip())
                self.assertEqual((source / "compose.staging.yaml").read_bytes(), (remote / "compose.yaml").read_bytes())

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
            for key in (None, "health", "image", "sourceSha", "servicesSha", "nginxSha", "composeProject", "phase", "publicHost"):
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
        for mode in ("ok", "missing-import", "missing-mount", "backup-copy-failure", "validation-failure", "reload-failure"):
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
                if mode == "backup-copy-failure":
                    self.write(binary / "cp", "#!/usr/bin/env bash\nexit 1\n")
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
                if mode == "backup-copy-failure":
                    self.assertFalse(candidate.exists())
                    self.assertEqual([], list(managed.glob(".website-staging.rollback.*")))
                    self.assertFalse((managed / "calls.log").exists())

    def test_verifier_requires_digest_alias_real_artifact_absence_and_stage_tls(self):
        for mode, success in (("ok", True), ("wrong-image", False), ("wrong-alias", False), ("bare-alias", False),
                              ("bare-alias-other-network", False), ("wrong-service", False),
                              ("html-fallback", False), ("wrong-locale", False), ("dns-absent", False), ("tls-failure", False)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                binary = directory / "bin"
                binary.mkdir()
                self.write(binary / "docker", '''#!/usr/bin/env python3
import json,os,sys
a=sys.argv[1:]; mode=os.environ['MODE']
if a[0]=='compose':
 assert a[-1]=='website-staging'
 print('stage-container')
elif a[0]=='inspect':
 f=a[a.index('--format')+1]
 if f=='{{.Config.Image}}': print('wrong' if mode=='wrong-image' else os.environ['WEBSITE_IMAGE'])
 elif f=='{{json .NetworkSettings.Networks}}':
  networks={'open-hax':{'Aliases':['wrong' if mode=='wrong-alias' else 'website-staging']}}
  if mode=='bare-alias': networks['open-hax']['Aliases'].append('website')
  if mode=='bare-alias-other-network': networks['other']={'Aliases':['website']}
  print(json.dumps(networks))
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
                    "WEBSITE_SERVICE_NAME": "website" if mode == "wrong-service" else "website-staging",
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
