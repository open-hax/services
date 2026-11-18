"""Offline active-upgrade fixture laws; these tests never invoke Docker."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
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


class ActiveFixtureDiagnosticTests(unittest.TestCase):
    def test_actual_workflow_capture_tail_retains_failure_and_never_passes_an_empty_result(self):
        document = yaml.safe_load((ROOT / '.github/workflows/axxium-recovery-staging.yml').read_text())
        step = next(row for row in document['jobs']['active-mailbox-upgrade']['steps']
                    if 'fixture_status=(' in row.get('run', ''))
        tail = step['run'][step['run'].index('fixture_status=('):]
        safe = {'status': 'failed', 'phase': 'owner-signup', 'category': 'signup-http-refused', 'http_status': 500}
        for child, expected, passes in (
                ('false', {'status': 'failed', 'phase': 'launch', 'category': 'runner-no-report'}, False),
                ('true', {'status': 'failed', 'phase': 'launch', 'category': 'runner-no-report'}, False),
                ("(printf '%s\\n' '" + json.dumps(safe) + "'; exit 2)", safe, False),
                ("printf '%s\\n' '{\"status\":\"healthy\"}'", {'status': 'healthy'}, True)):
            with self.subTest(child=child), tempfile.TemporaryDirectory() as directory:
                command = 'set -euo pipefail\nset +e\n' + child + ' | tee "$RUNNER_TEMP/active-upgrade-result.json"\n' + tail
                result = subprocess.run(['bash', '-c', command],
                    env={'PATH': os.environ['PATH'], 'RUNNER_TEMP': directory}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, passes)
                self.assertEqual(json.loads((Path(directory) / 'active-upgrade-result.json').read_text()), expected)

    def test_observed_proxy_requires_owned_image_internal_bridge_and_exact_ip(self):
        client = fixture.Fixture({})
        image = 'sha256:' + 'c' * 64
        cid, address = 'a' * 64, '172.29.0.3'
        record = {'Id': cid, 'Image': image, 'State': {'Running': True},
                  'Config': {'Labels': {'codex.axxium-recovery-fixture': 'synthetic'}},
                  'HostConfig': {'PortBindings': {}},
                  'NetworkSettings': {'Networks': {'open-hax': {'IPAddress': address}}}}
        bridge = {'Internal': True, 'Labels': {'codex.axxium-recovery-fixture': 'synthetic'},
                  'Containers': {cid: {'IPv4Address': address + '/16'}}}
        with mock.patch.object(fixture.wire, 'inspect', return_value=record), \
                mock.patch.object(fixture.wire, 'image_id', return_value=image), \
                mock.patch.object(fixture.wire, 'cmd', return_value=json.dumps([bridge]).encode()):
            self.assertEqual(client.proxy_ip(), address)
            for wrong in ({**record, 'Image': 'sha256:' + 'd' * 64},
                          {**record, 'Config': {'Labels': {}}},
                          {**record, 'HostConfig': {'PortBindings': {'443/tcp': [{}]}}}):
                with self.subTest(record=wrong), mock.patch.object(fixture.wire, 'inspect', return_value=wrong):
                    with self.assertRaises(RuntimeError):
                        client.proxy_ip()
            for wrong in ({**bridge, 'Internal': False}, {**bridge, 'Labels': {}},
                          {**bridge, 'Containers': {cid: {'IPv4Address': '172.29.0.4/16'}}}):
                with self.subTest(bridge=wrong), mock.patch.object(fixture.wire, 'cmd', return_value=json.dumps([wrong]).encode()):
                    with self.assertRaises(RuntimeError):
                        client.proxy_ip()

    def exercise_profile(self, module=fixture):
        """Run the actual setup writer with private temporary files and no I/O seams."""
        owner, work = 'actor_' + '1' * 8 + '-1111-4111-8111-' + '1' * 12, 'actor_' + '2' * 8 + '-2222-4222-8222-' + '2' * 12
        cred, sid = '33333333-3333-4333-8333-333333333333', '44444444-4444-4444-8444-444444444444'
        client = module.Fixture({'incumbent': 'old', 'candidate': 'new'})
        client.environment, client.volumes = {'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': module.PLACEHOLDER}, []
        client.request = mock.Mock(side_effect=[{'actor': owner, 'token': 'owner-test'},
            {'actor': work, 'token': 'work-test'}, {'actor': work, 'cred': cred, 'token': 'signed-test', 'hash': 'a' * 64},
            {'sid': sid}])
        client.tool = mock.Mock(side_effect=[{}, {'message_id': 'a'}, {'message_id': 'b'}, {}])
        client.verify, client.identity_hash = mock.Mock(), mock.Mock(return_value='retained')
        client.proxy_ip = mock.Mock(return_value='172.29.0.3')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'compose.yaml').write_text(json.dumps({'services': {'axxium': {'environment': {}}}}))
            overlay = root / 'retained.json'
            plan = deepcopy(module.active.PLAN)
            plan['compose_chain'][-1]['path'] = str(overlay)
            with mock.patch.object(module.native, 'RUNTIME', root), mock.patch.object(module.active, 'PLAN', plan), \
                    mock.patch.object(module.native, 'NativeHost') as host, mock.patch.object(module.wire, 'cmd'), \
                    mock.patch.object(module.network, 'wait_healthy'), mock.patch.object(module.active, 'ledger_bytes', return_value={}):
                host.return_value.sql.return_value = b't\n'
                client.setup_accounts_and_state()
            base = json.loads((root / 'compose.yaml').read_bytes())['services']['axxium']['environment']
            retained = json.loads(overlay.read_bytes())['services']['axxium']['environment']
        return base, retained

    def test_exact_proxy_trust_is_present_in_both_selected_profile_inputs(self):
        base, retained = self.exercise_profile()
        self.assertEqual(base.get('AXXIUM_TRUST_PROXY'), '172.29.0.3')
        self.assertEqual(retained.get('AXXIUM_TRUST_PROXY'), '172.29.0.3')
        self.assertEqual(base, retained)

    def test_native_signup_and_mcp_contract_fixtures_use_actual_embedded_client(self):
        actor = 'actor_11111111-1111-4111-8111-111111111111'
        response = {'status': 200, 'body': {'ok': True, 'actor': {'id': actor}, 'token': 'synthetic-jwt'},
                    'cookie': 'axxium_session=synthetic-jwt; Secure; HttpOnly'}
        def run_client(value, reply):
            prelude = r'''(()=>{const response=''' + json.dumps(reply) + r''';
global.fetch=async()=>({status:response.status,headers:{get:n=>n==='set-cookie'?response.cookie:null},json:async()=>response.body});
require('node:http').request=(options,callback)=>{
 const {Readable}=require('node:stream'),{EventEmitter}=require('node:events');
 const request=new EventEmitter();request.setTimeout=()=>request;
 request.end=()=>{const reply=Readable.from([Buffer.from(JSON.stringify(response.body))]);
 reply.statusCode=response.status;reply.headers={};callback(reply);};return request;
};})();
'''
            return subprocess.run(['node', '-e', prelude + fixture.CLIENT], input=json.dumps(value),
                                  capture_output=True, text=True, timeout=10)
        result = run_client({'mode': 'signup', 'account': {'email': 'fixture@example.invalid', 'password': 'synthetic-password'}}, response)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {'actor': actor, 'token': 'synthetic-jwt'})
        for status in (400, 403, 409, 500):
            result = run_client({'mode': 'signup', 'account': {}}, {**response, 'status': status,
                                'body': {'error': 'private-response-do-not-print'}})
            self.assertEqual(result.returncode, 20)
            self.assertEqual(json.loads(result.stdout), {'fixture_client_code': 20, 'fixture_http_status': status})
            self.assertEqual(result.stderr, '')
        for reply, code in (({**response, 'body': {}}, 21), ({**response, 'cookie': None}, 22)):
            self.assertEqual(run_client({'mode': 'signup', 'account': {}}, reply).returncode, code)
        for reply, code in (({'status': 401, 'body': {}}, 40),
                            ({'status': 403, 'body': {'error': 'Mailbox origin refused'}}, 40),
                            ({'status': 403, 'body': {'error': 'Mailbox access refused', 'private': 'never-print'}}, 40),
                            ({'status': 200, 'body': {'error': {'message': 'private-response'}}}, 41),
                            ({'status': 200, 'body': {'result': {'isError': True, 'content': ['private-response']}}}, 42)):
            result = run_client({'token': 'fixture-test', 'sid': None, 'request': {}}, reply)
            self.assertEqual(result.returncode, code)
            expected = {'fixture_client_code': code}
            if code == 40:
                expected['fixture_http_status'] = reply['status']
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertEqual(result.stderr, '')

    def test_mcp_canonical_host_survives_real_http_serialization_without_network(self):
        # In-memory Duplex exercises node:http's real serializer. The old Fetch
        # path also uses the real Fetch implementation, with a no-network
        # dispatcher, so its dropped Host produces the native route's refusal.
        prelude = r'''
(()=>{
const assert=require('node:assert/strict'),http=require('node:http'),{Duplex}=require('node:stream');
const response=JSON.stringify({result:{structuredContent:{fixture:true}}});
let serialized=false;
const nativeFetch=global.fetch;
global.fetch=(url,options)=>nativeFetch(url,{...options,dispatcher:{dispatch(o,h){
 const headers=new Headers(o.headers),status=headers.get('host')==='axxium.promethean.rest'?200:403;
 const body=status===200?response:JSON.stringify({error:'Mailbox origin refused'});
 h.onConnect(()=>{});h.onHeaders(status,[Buffer.from('content-type'),Buffer.from('application/json')],()=>{},'');
 h.onData(Buffer.from(body));h.onComplete([]);return true;
}}});
http.globalAgent.createConnection=(options)=>{
 assert.equal(options.host,'127.0.0.1');assert.equal(Number(options.port),8787);
 let wire='',answered=false;
 const socket=new Duplex({read(){},write(chunk,encoding,done){
  wire+=chunk.toString();const end=wire.indexOf('\r\n\r\n');
  if(end>=0&&!answered){
   const lines=wire.slice(0,end).split('\r\n'),headers=Object.fromEntries(lines.slice(1).map(line=>{
    const i=line.indexOf(':');return [line.slice(0,i).toLowerCase(),line.slice(i+1).trim()];}));
   if(Buffer.byteLength(wire.slice(end+4))>=Number(headers['content-length'])){
    answered=true;assert.equal(lines[0],'POST /api/mailbox/mcp HTTP/1.1');
    assert.equal(headers.host,'axxium.promethean.rest');assert.equal(headers.origin,undefined);
    assert.equal(headers.authorization,'Bearer synthetic-fixture-token');
    assert.equal(headers.accept,'application/json, text/event-stream');
    assert.equal(headers['content-type'],'application/json');
    assert.deepEqual(JSON.parse(wire.slice(end+4)),JSON.parse(process.env.FIXTURE_PACKET).request);
    const sid=JSON.parse(process.env.FIXTURE_PACKET).sid;
    assert.equal(headers['mcp-session-id'],sid);
    assert.equal(headers['mcp-protocol-version'],sid?'2025-06-18':undefined);
    serialized=true;
    socket.push('HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nMcp-Session-Id: fixture-session\r\nContent-Length: '
      +Buffer.byteLength(response)+'\r\nConnection: close\r\n\r\n'+response);socket.push(null);
   }
  }done();}});
 socket.setNoDelay=socket.setKeepAlive=socket.setTimeout=()=>socket;
 socket.ref=socket.unref=()=>socket;return socket;
};
process.on('beforeExit',()=>assert.equal(serialized,true));
})();
'''
        for sid in (None, '22222222-2222-4222-8222-222222222222'):
            packet = {'token': 'synthetic-fixture-token', 'request': {'jsonrpc': '2.0', 'id': 1,
                      'method': 'initialize' if sid is None else 'tools/list', 'params': {}}}
            if sid:
                packet['sid'] = sid
            with self.subTest(sid=sid):
                result = subprocess.run(['node', '-e', prelude + fixture.CLIENT], input=json.dumps(packet),
                    env={**os.environ, 'FIXTURE_PACKET': json.dumps(packet)}, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(json.loads(result.stdout), {'sid': 'fixture-session', 'result': {'fixture': True}})

    def test_simulated_at_preserves_signed_native_claims_and_credential_uuid(self):
        prelude = r'''
(()=>{
const crypto=require('node:crypto'),fs=require('node:fs');
const pair=crypto.generateKeyPairSync('ed25519');
const claims={sub:'actor_11111111-1111-4111-8111-111111111111',cred_id:'22222222-2222-4222-8222-222222222222',
iss:'axxium',aud:'promethean+production',env:'production',iat:1900000000,exp:1900003600};
const header=Buffer.from(JSON.stringify({alg:'EdDSA',typ:'JWT',kid:'fixture-active'})).toString('base64url');
const payload=Buffer.from(JSON.stringify(claims)).toString('base64url'),input=header+'.'+payload;
const original=input+'.'+crypto.sign(null,Buffer.from(input),pair.privateKey).toString('base64url');
const packet={mode:'simulate-at',token:original,did:'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa'};
const read=fs.readFileSync;fs.readFileSync=(path,...args)=>path===0?Buffer.from(JSON.stringify(packet)):
path==='/run/axxium/actor-signing.pem'?pair.privateKey.export({type:'pkcs8',format:'pem'}):read(path,...args);
const write=process.stdout.write.bind(process.stdout);process.stdout.write=(text)=>{
 const result=JSON.parse(text),[h,p,s]=result.token.split('.'),next=JSON.parse(Buffer.from(p,'base64url'));
 if(h!==header||next.atproto_did!==packet.did||result.actor!==claims.sub||result.cred!==claims.cred_id)process.exit(10);
 delete next.atproto_did;if(JSON.stringify(next)!==JSON.stringify(claims))process.exit(11);
 if(!crypto.verify(null,Buffer.from(h+'.'+p),pair.publicKey,Buffer.from(s,'base64url')))process.exit(12);
 if(result.hash!==crypto.createHash('sha256').update(result.token).digest('hex'))process.exit(13);
 return write('{"verified":true}\n');};
})();
'''
        result = subprocess.run(['node', '-e', prelude + fixture.CLIENT], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{"verified":true}\n')

    def test_child_error_data_is_replaced_by_constant_category(self):
        client = fixture.Fixture({})
        client.owned = mock.Mock()
        response = subprocess.CompletedProcess([], 20, b'private-response', b'private-key')
        with mock.patch.object(fixture.subprocess, 'run', return_value=response):
            with self.assertRaises(fixture.ClientFailure) as result:
                client.request(fixture.native.APP, {'mode': 'signup', 'account': {}})
        self.assertEqual(result.exception.category, 'signup-http-refused')
        self.assertNotIn('private', str(result.exception))

    def test_numeric_http_metadata_is_bounded_and_native_uncertainty_stays_held(self):
        for status in (100, 401, 599, 99, 600, True, '401'):
            raw = json.dumps({'fixture_client_code': 40, 'fixture_http_status': status}).encode()
            failure = fixture.client_problem(raw, returncode=40)
            expected = status if type(status) is int and 100 <= status <= 599 else None
            self.assertEqual(failure.http_status, expected)
        malformed = fixture.client_problem(b'{"fixture_client_code":40,"fixture_http_status":401,"body":"private"}', returncode=40)
        self.assertIsNone(malformed.http_status)
        client = fixture.Fixture({})
        client.owned = mock.Mock()
        host = mock.Mock()
        def refused(*args, **kwargs):
            self.assertIs(kwargs['effect'], True)
            kwargs['output'].write(b'{"fixture_client_code":40,"fixture_http_status":401}')
            raise fixture.native.Uncertain('private-native-error')
        host.command.side_effect = refused
        with self.assertRaises(fixture.ClientUncertainty) as failure:
            client.request(fixture.native.APP, {'request': {'method': 'initialize'}}, host)
        self.assertNotIsInstance(failure.exception, fixture.native.KnownFailure)
        self.assertEqual(failure.exception.http_status, 401)
        self.assertEqual(failure.exception.category, 'mcp-http-refused')

    def test_expected_refusal_preserves_first_real_failure_and_later_terminal_scope(self):
        diagnostics = fixture.Diagnostics(enabled=True)
        def refusal():
            raise fixture.native.Refused('private-expected-denial')
        def terminal():
            raise RuntimeError('private-terminal-error')
        with mock.patch.object(fixture, 'DIAG', diagnostics), redirect_stderr(io.StringIO()):
            fixture.require_refusal('native-retry', refusal)
            fixture.require_refusal('native-recovery-refusal', refusal)
            self.assertIsNone(diagnostics.failed)
            with self.assertRaises(RuntimeError) as error:
                diagnostics.call('terminal-verification', terminal)
            first = diagnostics.failure_report(error.exception)
            self.assertEqual(first['phase'], 'terminal-verification')
            fixture.require_refusal('native-retry', refusal)
            self.assertEqual(diagnostics.failure_report(RuntimeError('private-later')), first)

    def test_real_guard_failure_produces_a_nonempty_safe_result_before_docker(self):
        script = ROOT / 'scripts/staging/axxium/active_upgrade_fixture.py'
        result = subprocess.run([sys.executable, '-B', str(script), '--candidate', '/fixture/no-candidate',
                                 '--incumbent', '/fixture/no-incumbent', '--scenario', 'healthy'],
                                env={'PATH': os.environ['PATH'], 'GITHUB_ACTIONS': 'false'},
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stdout.strip(), 'A failed fixture must leave a safe phase/category result')
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['phase'], 'environment-guard')
        self.assertEqual(report['category'], 'operation-refused')
        self.assertNotIn('/fixture/no-candidate', result.stdout)

    def test_nested_unknown_exception_and_native_details_never_leave_diagnostics(self):
        diagnostics = fixture.Diagnostics(enabled=True)
        output, error = io.StringIO(), io.StringIO()
        def fail():
            raise RuntimeError('private-token-key-account-row-do-not-print')
        with redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(fixture.run(lambda: diagnostics.call('owner-signup', fail), diagnostics), 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report['phase'], 'owner-signup')
        self.assertEqual(report['category'], 'operation-refused')
        self.assertNotIn('private-token-key-account-row', output.getvalue() + error.getvalue())
        with self.assertRaises(ValueError):
            diagnostics.call('untrusted-phase-do-not-print', lambda: None)


if __name__ == "__main__":
    unittest.main()
