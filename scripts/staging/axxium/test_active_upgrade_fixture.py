"""Offline active-upgrade fixture laws; these tests never invoke Docker."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml

import active_upgrade_fixture as fixture
import network_fixture
import prepare_active_images as images

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/tests'))
from test_axxium_production_qualification import SOURCE, create_archive


class ActiveFixtureLaws(unittest.TestCase):
    def test_projection_refuses_nonallowlisted_source_custody_path_and_transport_changes(self):
        original = deepcopy(fixture.active.PLAN)
        self.assertEqual(fixture.audit_projection(original, original), [])
        for field, value in (("source_sha", "f" * 40), ("artifact_id", 1),
                             ("image_config_digest", "sha256:" + "a" * 64), ("schema", "other")):
            changed = deepcopy(original)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                fixture.audit_projection(original, changed)
        for field in ("compose_chain", "retained_config"):
            changed = deepcopy(original)
            changed[field][0]["path"] = "/unexpected"
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                fixture.audit_projection(original, changed)
        changed = deepcopy(original)
        changed["transport"]["AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED"] = "true"
        with self.assertRaises(RuntimeError):
            fixture.audit_projection(original, changed)

    def test_actual_ack_semantics_require_default_inbox_and_both_delivery_states(self):
        a, b = "synthetic-a", "synthetic-b"
        inbox = {"messages": [{"message_id": b}], "cursor": 1}
        delivery = {"accepted": "accepted", "acknowledged": True, "execution": "unknown"}
        fixture.check_semantics(a, b, False, inbox, delivery, {**delivery, "acknowledged": False})
        for altered in ({"messages": [{"message_id": a}, {"message_id": b}], "cursor": 1},
                        {"messages": [], "cursor": 1}, {"messages": [{"message_id": b}], "cursor": 3}):
            with self.subTest(altered=altered), self.assertRaises(RuntimeError):
                fixture.check_semantics(a, b, False, altered, delivery, {**delivery, "acknowledged": False})
        fixture.check_semantics(a, b, True, {"messages": [], "cursor": 2}, delivery, delivery)

    def test_prestart_profile_cannot_run_before_existing_safety_preflight(self):
        hook = mock.Mock()
        with mock.patch.object(network_fixture, "preflight", side_effect=network_fixture.FixtureError("guard")):
            with self.assertRaises(network_fixture.FixtureError):
                network_fixture.setup(network_fixture.ROOT, "old", "db", "proxy", prepare_app=hook)
        hook.assert_not_called()

    def test_mcp_target_requires_owned_internal_networks_and_no_ports(self):
        image = 'sha256:' + 'a' * 64
        client = fixture.Fixture({'incumbent': image, 'candidate': 'sha256:' + 'b' * 64})
        record = {'Image': image, 'Config': {'Labels': {'codex.axxium-recovery-fixture': 'synthetic'}},
                  'HostConfig': {'PortBindings': {}},
                  'NetworkSettings': {'Networks': {'open-hax': {}, 'axxium_database': {}}}}
        network = {'Internal': True, 'Labels': {'codex.axxium-recovery-fixture': 'synthetic'}}
        with mock.patch.object(fixture.wire, 'inspect', return_value=record), \
                mock.patch.object(fixture.wire, 'cmd', return_value=json.dumps([network]).encode()):
            self.assertEqual(client.owned(fixture.native.APP), record)
        variants = []
        for field, value in (('Image', 'sha256:' + 'c' * 64),
                             ('HostConfig', {'PortBindings': {'8787/tcp': [{'HostPort': '8787'}]}}),
                             ('Config', {'Labels': {}}), ('NetworkSettings', {'Networks': {'external': {}}})):
            variants.append({**record, field: value})
        for changed in variants:
            with self.subTest(changed=changed), mock.patch.object(fixture.wire, 'inspect', return_value=changed), \
                    mock.patch.object(fixture.wire, 'cmd') as effect:
                with self.assertRaises(RuntimeError):
                    client.owned(fixture.native.APP)
                effect.assert_not_called()
        for changed in ({**network, 'Internal': False}, {**network, 'Labels': {}}):
            with self.subTest(network=changed), mock.patch.object(fixture.wire, 'inspect', return_value=record), \
                    mock.patch.object(fixture.wire, 'cmd', return_value=json.dumps([changed]).encode()):
                with self.assertRaises(RuntimeError):
                    client.owned(fixture.native.APP)

    def test_uncertainty_is_injected_only_after_successful_real_start_seam(self):
        host = fixture.Host(fixture.active.PLAN_SHA256, mock.Mock(), 'uncertain')
        calls = []
        with mock.patch.object(fixture.active.Host, 'start_candidate', side_effect=lambda p: calls.append('native-start')), \
                mock.patch.object(host, 'inspect', side_effect=lambda _: calls.append('actual-readback') or {'State': {'Running': True}}):
            with self.assertRaises(fixture.native.Uncertain):
                host.start_candidate(fixture.active.PLAN)
        self.assertEqual(calls, ['native-start', 'actual-readback'])
        self.assertTrue(host.injected)
        denied = fixture.Host(fixture.active.PLAN_SHA256, mock.Mock(), 'uncertain')
        with mock.patch.object(fixture.active.Host, 'start_candidate', side_effect=fixture.native.KnownFailure('start failed')):
            with self.assertRaises(fixture.native.KnownFailure):
                denied.start_candidate(fixture.active.PLAN)
        self.assertFalse(denied.injected)

    def test_known_failure_follows_real_health_parser_and_new_ack(self):
        client = mock.Mock()
        client.b = 'fixture-b'
        client.acked_b = False
        calls = []
        client.verify.side_effect = lambda *args: calls.append('native-parser')
        client.tool.side_effect = lambda *args: calls.append('native-ack')
        host = fixture.Host(fixture.active.PLAN_SHA256, client, 'known-failure')
        state = {'mailbox': b'canonical fixture frames with current ACK', 'identity': b'identity'}
        with mock.patch.object(fixture.active.Host, 'private_health', side_effect=lambda p: calls.append('native-health')), \
                mock.patch.object(fixture.active, 'ledger_bytes', return_value=state):
            with self.assertRaises(fixture.native.KnownFailure):
                host.private_health(fixture.active.PLAN)
        self.assertEqual(calls, ['native-health', 'native-parser', 'native-ack', 'native-parser'])
        self.assertTrue(client.acked_b)
        self.assertEqual(client.failure_ledgers, state)

    def test_clone_parser_check_precedes_the_native_stop_without_replacing_probe(self):
        client = mock.Mock()
        calls = []
        client.verify.side_effect = lambda *args: calls.append('parser')
        host = fixture.Host(fixture.active.PLAN_SHA256, client, 'healthy')
        host.probing = True
        args = ['docker', 'stop', '--time', '610', 'axxium-verify-' + 'a' * 24]
        with mock.patch.object(fixture.active.Host, 'command', side_effect=lambda *a, **k: calls.append('native-stop') or b''):
            self.assertEqual(host.command(args, effect=True), b'')
        self.assertEqual(calls, ['parser', 'native-stop'])

    def test_embedded_native_clients_parse_without_execution(self):
        for source in (fixture.KEYS, fixture.CLIENT):
            result = subprocess.run(['node', '--check'], input=source, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class ActiveImageCustodyTests(unittest.TestCase):
    def test_both_fixed_role_chains_are_distinct_and_cross_role_runtime_refuses(self):
        candidate, old = images.role_plan('candidate'), images.role_plan('incumbent')
        self.assertEqual(candidate['source_sha'], '61b1cea021b13fac539232af23d6714f2287f8a4')
        self.assertEqual(old['source_sha'], 'd2f8dc8425f86f51a8450a0cae0b79a042694c64')
        for role, plan in (('candidate', candidate), ('incumbent', old)):
            self.assertEqual(len({plan[k] for k in ('image_id', 'image_manifest_digest', 'image_config_digest')}), 3)
            self.assertEqual(images.runtime_id(role, plan['image_config_digest']), plan['image_config_digest'])
        with self.assertRaises(ValueError):
            images.runtime_id('candidate', old['image_config_digest'])
        with self.assertRaises(ValueError):
            images.role_plan('unknown')

    def test_actual_archive_verifier_binds_manifest_config_and_extracts_exact_tar(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'small.zip'
            image = create_archive(archive, nested=True)
            with zipfile.ZipFile(archive) as package:
                raw_tar = package.read('images.tar')
            with tarfile.open(fileobj=io.BytesIO(raw_tar)) as package:
                blobs = {row.name: package.extractfile(row).read() for row in package if row.isfile()}
            index = json.loads(blobs['blobs/sha256/' + image[7:]])
            descriptor = next(row for row in index['manifests'] if row['platform'] == {'architecture': 'amd64', 'os': 'linux'})
            manifest = json.loads(blobs['blobs/sha256/' + descriptor['digest'][7:]])
            plan = {'source_sha': SOURCE, 'image_id': image, 'image_manifest_digest': descriptor['digest'],
                    'image_config_digest': manifest['config']['digest'],
                    'artifact_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'images_tar_sha256': hashlib.sha256(raw_tar).hexdigest()}
            with mock.patch.object(images, 'role_plan', return_value=plan):
                binding = images.extract_role('candidate', archive, Path(directory) / 'verified.tar')
            self.assertEqual(binding['image_config_digest'], plan['image_config_digest'])
            self.assertEqual((Path(directory) / 'verified.tar').read_bytes(), raw_tar)
            for key in ('artifact_sha256', 'images_tar_sha256', 'image_manifest_digest', 'image_config_digest'):
                wrong = {**plan, key: ('sha256:' if key.startswith('image_') and key.endswith('digest') else '') + '0' * 64}
                output = Path(directory) / ('denied-' + key)
                with self.subTest(key=key), mock.patch.object(images, 'role_plan', return_value=wrong):
                    with self.assertRaises(ValueError):
                        images.extract_role('candidate', archive, output)
                self.assertFalse(output.exists())


class ActiveFixtureWorkflowTests(unittest.TestCase):
    def test_historical_job_bytes_and_new_hosted_matrix_boundaries(self):
        raw = (ROOT / '.github/workflows/axxium-recovery-staging.yml').read_bytes()
        old_job = raw[raw.index(b'  synthetic-integration:'):raw.index(b'\n  active-mailbox-upgrade:')]
        self.assertEqual(hashlib.sha256(old_job).hexdigest(), 'a67549471ffbed6a3088cd75c59df269af895c9e47f2444bb6fe91d48b22a9cd')
        document = yaml.safe_load(raw)
        job = document['jobs']['active-mailbox-upgrade']
        self.assertEqual(job['runs-on'], 'ubuntu-latest')
        self.assertNotIn('environment', job)
        self.assertEqual(job['strategy']['matrix']['scenario'], ['healthy', 'known-failure', 'uncertain'])
        self.assertIs(job['strategy']['fail-fast'], False)
        self.assertTrue(all(value == 'read' for value in document['permissions'].values()))
        text = '\n'.join(step.get('run', '') for step in job['steps'])
        for required in ('test "$RUNNER_ENVIRONMENT" = github-hosted', 'test ! -e /srv/open-hax',
                         'unix:///var/run/docker.sock', 'exec unshare --net --fork setpriv', '--reuid=1000',
                         '14366915569/zip', 'staging-artifact-14133886771.zip', 'prepare_active_images.py'):
            self.assertIn(required, text)
        self.assertNotIn('secrets.', str(job))
        self.assertNotIn('workflow_dispatch', text)

    def test_actual_workflow_installed_import_closure_is_self_contained(self):
        document = yaml.safe_load((ROOT / '.github/workflows/axxium-recovery-staging.yml').read_text())
        step = next(row for row in document['jobs']['active-mailbox-upgrade']['steps'] if 'fixture_source=$(mktemp' in row.get('run', ''))
        import re
        loops = re.findall(r'for file in (.*?); do\n(.*?)\n\s*done', step['run'].replace('\\\n', ' '), re.S)
        self.assertEqual(len(loops), 3)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for words, body in loops:
                subdir = ('scripts/staging/axxium' if '/scripts/staging/axxium/$file' in body else
                          'digitalocean/services/axxium' if '/digitalocean/services/axxium/$file' in body else 'scripts')
                for file in shlex.split(words):
                    target = root / subdir / file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((ROOT / subdir / file).read_bytes())
            program = "import sys;sys.path.insert(0,sys.argv[1]+'/scripts/staging/axxium');import active_upgrade_fixture;print('isolated fixture imports passed')"
            result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(root)],
                                    cwd=directory, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'isolated fixture imports passed\n')


if __name__ == "__main__":
    unittest.main()
