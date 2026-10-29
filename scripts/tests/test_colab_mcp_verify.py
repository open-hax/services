"""Exercise the service gate's fail-closed HTTP/image decisions without a host."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "digitalocean/services/colab-mcp/verify.sh"
IMAGE = "ghcr.io/open-hax/cloud-cuckoo@sha256:" + "0" * 64
REVISION = "a" * 40
BASE = "https://knoxx.promethean.rest/colab"
ISSUER = "https://axxium.promethean.rest/oidc"
LOCAL_HEALTH = "http://172.31.255.1:18024/health"
RESOURCE_METADATA = "https://knoxx.promethean.rest/.well-known/oauth-protected-resource/colab/mcp"
OAUTH_METADATA = "https://knoxx.promethean.rest/.well-known/oauth-authorization-server/colab"
ISSUER_METADATA = ISSUER + "/.well-known/openid-configuration"


def fixture():
    health = {"service": "knoxx-colab-mcp", "ready": True, "identity_available": True}
    return {
        "image": IMAGE, "revision": REVISION,
        "running_image_id": "sha256:" + "2" * 64,
        "selected_image_id": "sha256:" + "2" * 64,
        "source": "https://github.com/open-hax/cloud-cuckoo",
        "responses": {
            LOCAL_HEALTH: [200, health], BASE + "/health": [200, health],
            RESOURCE_METADATA: [200, {"resource": BASE + "/mcp", "authorization_servers": [BASE],
                                      "scopes_supported": ["openid", "profile"]}],
            OAUTH_METADATA: [200, {
                "issuer": BASE, "authorization_endpoint": BASE + "/authorize",
                "token_endpoint": BASE + "/token", "registration_endpoint": BASE + "/register",
                "revocation_endpoint": BASE + "/revoke", "scopes_supported": ["openid", "profile"],
                "code_challenge_methods_supported": ["S256"], "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"]}],
            ISSUER_METADATA: [200, {
                "issuer": ISSUER, "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
                **{key: ISSUER + path for key, path in {
                    "authorization_endpoint": "/auth", "token_endpoint": "/token",
                    "introspection_endpoint": "/introspection", "revocation_endpoint": "/revoke",
                    "jwks_uri": "/jwks"}.items()}}],
            BASE + "/mcp": [401, {"error": "invalid_token"}],
        },
    }


MOCK = """#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
data=json.loads(Path(os.environ['PROBE_FIXTURE']).read_text())
args=sys.argv[1:]
with Path(os.environ['PROBE_TRACE']).open('a') as out:
 out.write(json.dumps({'command':Path(sys.argv[0]).name,'args':args})+'\\n')
if Path(sys.argv[0]).name=='docker':
 if args[:1]==['compose'] and args[-3:]==['ps','-q','gateway']:print('fixture-container')
 elif args[:3]==['image','inspect','--format']:
  if args[3]=='{{.Id}}':print(data['selected_image_id'])
  else:sys.exit(2)
 elif args[:2]==['inspect','--format']:
  fmt=args[2]
  if fmt=='{{.Config.Image}}':print(data['image'])
  elif fmt=='{{.Image}}':print(data['running_image_id'])
  elif 'image.source' in fmt:print(data['source'])
  elif 'image.revision' in fmt:print(data['revision'])
  else:sys.exit(2)
 else:sys.exit(2)
else:
 url=args[-1]
 if data.get('transport_failure')==url:sys.exit(7)
 status,body=data['responses'][url]
 print(json.dumps(body)+'\\n'+str(status),end='')
"""


class ColabMcpVerifyTests(unittest.TestCase):
    def run_gate(self, data=None, env_changes=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for command in ("docker", "curl"):
                path = bin_dir / command
                path.write_text(MOCK)
                path.chmod(0o755)
            state = root / "state"
            state.mkdir(mode=0o700)
            config = root / "fixture.json"
            config.write_text(json.dumps(data or fixture()))
            trace = root / "trace.jsonl"
            env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                   "PROBE_FIXTURE": str(config), "PROBE_TRACE": str(trace),
                   "COLAB_MCP_IMAGE": IMAGE, "COLAB_MCP_SOURCE_REVISION": REVISION,
                   "COLAB_MCP_STATE_DIR": str(state), **(env_changes or {})}
            result = subprocess.run(["bash", str(VERIFY)], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=20)
            calls = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
            return result, calls

    def test_contract_ready_fixture_passes_without_credentials_or_grants(self):
        result, calls = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no user OAuth grant was exercised", result.stdout)
        requests = [call["args"] for call in calls if call["command"] == "curl"]
        self.assertEqual({args[-1] for args in requests}, set(fixture()["responses"]))
        for args in requests:
            self.assertNotIn("Authorization", " ".join(args))
            self.assertIn("--max-time", args)
            self.assertEqual(args[0], "--disable")
            if "--request" in args:
                self.assertEqual(args[-1], BASE + "/mcp")
                self.assertEqual(json.loads(args[args.index("--data") + 1])["method"], "initialize")
        self.assertFalse(any("exec" in call["args"] for call in calls))

    def test_pending_identity_cannot_pass_even_with_valid_discovery(self):
        data = fixture()
        data["responses"][LOCAL_HEALTH][1]["identity_available"] = False
        result, _ = self.run_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity is pending", result.stderr)

    def test_scope_pkce_and_endpoint_drift_fail(self):
        for field, value in [("scopes_supported", ["colab"]),
                             ("code_challenge_methods_supported", ["plain"]),
                             ("token_endpoint", "https://other.invalid/token")]:
            with self.subTest(field=field):
                data = fixture()
                data["responses"][OAUTH_METADATA][1][field] = value
                result, _ = self.run_gate(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("pinned scopes, endpoints or PKCE", result.stderr)

    def test_resource_and_upstream_identity_drift_fail(self):
        for url, field, value in [(RESOURCE_METADATA, "resource", BASE + "/other"),
                                  (ISSUER_METADATA, "issuer", "https://other.invalid/oidc"),
                                  (ISSUER_METADATA, "jwks_uri", ISSUER + "/../jwks")]:
            with self.subTest(field=field):
                data = fixture()
                data["responses"][url][1][field] = value
                result, _ = self.run_gate(data)
                self.assertNotEqual(result.returncode, 0)

    def test_upstream_absence_and_transport_failure_are_distinct(self):
        data = fixture()
        data["responses"][ISSUER_METADATA][0] = 404
        result, _ = self.run_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Axxium issuer discovery returned HTTP 404", result.stderr)
        data = fixture()
        data["transport_failure"] = ISSUER_METADATA
        result, _ = self.run_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Axxium issuer discovery transport failed", result.stderr)

    def test_anonymous_success_or_missing_route_cannot_pass(self):
        for status in (200, 403, 404, 503):
            with self.subTest(status=status):
                data = fixture()
                data["responses"][BASE + "/mcp"][0] = status
                result, _ = self.run_gate(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("instead of 401", result.stderr)

    def test_image_and_source_mismatch_stop_before_http(self):
        for field, value in [("image", "ghcr.io/open-hax/cloud-cuckoo@sha256:" + "1" * 64),
                             ("revision", "b" * 40), ("source", "https://other.invalid/repo")]:
            with self.subTest(field=field):
                data = fixture()
                data[field] = value
                result, calls = self.run_gate(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call["command"] == "curl" for call in calls))

    def test_mutable_selected_image_is_refused_before_docker(self):
        result, calls = self.run_gate(env_changes={"COLAB_MCP_IMAGE": "ghcr.io/open-hax/cloud-cuckoo:latest"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_matching_reference_with_different_image_id_is_refused(self):
        data = fixture()
        data["running_image_id"] = "sha256:" + "3" * 64
        result, calls = self.run_gate(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("running image ID differs", result.stderr)
        self.assertFalse(any(call["command"] == "curl" for call in calls))


if __name__ == "__main__":
    unittest.main()
