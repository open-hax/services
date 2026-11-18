"""Real active-host transaction in a disposable, synthetic, internal-only VM."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import axxium_active_mailbox_release as active
import axxium_ingress_caddy as ingress
import axxium_minimal_release as native
from axxium_oidc_ingress import MAILBOX, OLD, prepare_active_mailbox
import fixture_netns
import full_recovery_probe as wire
import network_fixture as network
from prepare_active_images import role_plan, runtime_id, verify_role

ROOT = Path('/srv/open-hax')
DID = 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa'
OWNER = 'did:web:axxium.promethean.rest:actors:fixture'
WORK = OWNER + ':work'
PLACEHOLDER = 'actor_00000000-0000-4000-8000-000000000000'
POSTGRES = 'sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
CADDY = 'sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17'
ORIGINAL_PLAN = deepcopy(active.PLAN)

SAFE_PHASES = frozenset({'launch', 'arguments', 'environment-guard', 'archive-custody', 'image-resolution',
    'topology-setup', 'fixture-key-generation', 'account-setup', 'owner-signup', 'work-signup', 'simulate-at',
    'credential-binding', 'credential-readback', 'proxy-trust', 'profile-restart', 'mcp-initialize', 'mcp-request',
    'mailbox-register', 'mailbox-send', 'mailbox-ack', 'mailbox-read', 'mailbox-delivery', 'plan-projection',
    'native-transaction', 'native-retry', 'native-recovery-refusal', 'candidate-health', 'public-health',
    'ingress-http', 'terminal-verification'})
SAFE_CATEGORIES = frozenset({'operation-refused', 'input-malformed', 'filesystem-refused', 'topology-refused',
    'native-known-failure', 'native-uncertain', 'native-refused', 'client-command-refused',
    'client-transport-refused', 'signup-http-refused', 'signup-response-refused', 'signup-cookie-refused',
    'simulated-claims-refused', 'simulated-signing-refused', 'mcp-http-refused', 'mcp-rpc-refused',
    'mcp-tool-refused', 'unclassified'})
CLIENT_CATEGORIES = {20: 'signup-http-refused', 21: 'signup-response-refused', 22: 'signup-cookie-refused',
                     30: 'simulated-claims-refused', 31: 'simulated-signing-refused',
                     40: 'mcp-http-refused', 41: 'mcp-rpc-refused', 42: 'mcp-tool-refused',
                     59: 'client-transport-refused'}
NATIVE_PHASES = frozenset({'none', 'fence_public_api', 'stop_incumbent', 'verified_before_backup',
    'provision_identity', 'candidate_rollback_rehearsal', 'start_candidate', 'private_candidate_health',
    'stop_candidate_for_after_backup', 'verified_after_backup', 'current_state_rollback_rehearsal',
    'restart_verified_candidate', 'final_identity_and_state', 'publish_oidc_route', 'public_oidc_health',
    'healthy_receipt', 'rollback', 'transaction'})


class ClientFailure(native.KnownFailure):
    def __init__(self, category, http_status=None):
        self.category = category if category in SAFE_CATEGORIES else 'unclassified'
        self.http_status = http_status if self.category in {'signup-http-refused', 'mcp-http-refused'} \
            and type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__('Isolated fixture client refused')


class ClientUncertainty(native.Uncertain):
    def __init__(self, category, http_status=None):
        safe = ClientFailure(category, http_status)
        self.category, self.http_status = safe.category, safe.http_status
        super().__init__('Isolated fixture client outcome uncertain')


def client_problem(raw, *, returncode=None, uncertain=False):
    category = CLIENT_CATEGORIES.get(returncode, 'client-command-refused')
    status = None
    try:
        data = json.loads(raw)
        if (isinstance(data, dict) and set(data) <= {'fixture_client_code', 'fixture_http_status'}
                and type(data.get('fixture_client_code')) is int and data['fixture_client_code'] in CLIENT_CATEGORIES
                and (returncode is None or returncode == data['fixture_client_code'])):
            category = CLIENT_CATEGORIES[data['fixture_client_code']]
            status = data.get('fixture_http_status')
    except (ValueError, TypeError):
        pass
    return (ClientUncertainty if uncertain else ClientFailure)(category, status)


class ExpectedKnownFailure(native.KnownFailure):
    pass


class ExpectedUncertainty(native.Uncertain):
    pass


class Diagnostics:
    """Emit only registered constants/booleans; never exception or child text."""
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.scopes = []
        self.failed = None
        self.native_phase = 'none'
        self.booleans = {}

    def category(self, error):
        if isinstance(error, (ClientFailure, ClientUncertainty)):
            return error.category
        for kind, category in ((native.Uncertain, 'native-uncertain'), (native.KnownFailure, 'native-known-failure'),
                               (native.Refused, 'native-refused'), (network.FixtureError, 'topology-refused'),
                               (OSError, 'filesystem-refused'), ((ValueError, KeyError, TypeError), 'input-malformed')):
            if isinstance(error, kind):
                return category
        return 'operation-refused' if isinstance(error, RuntimeError) else 'unclassified'

    def remember(self, error):
        if self.failed is None and not isinstance(error, (ExpectedKnownFailure, ExpectedUncertainty)):
            self.failed = {'phase': self.scopes[-1] if self.scopes else 'launch',
                           'phase_path': list(self.scopes), 'category': self.category(error),
                           'native_phase': self.native_phase}
            if isinstance(error, (ClientFailure, ClientUncertainty)) and error.http_status is not None:
                self.failed['http_status'] = error.http_status

    @contextmanager
    def phase(self, name):
        if name not in SAFE_PHASES:
            raise ValueError('Unknown fixture diagnostic phase')
        if not self.enabled:
            yield
            return
        self.scopes.append(name)
        print(json.dumps({'fixture_phase': name}), file=sys.stderr, flush=True)
        try:
            yield
        except Exception as error:
            self.remember(error)
            raise
        finally:
            self.scopes.pop()

    def call(self, name, function, *args, **kwargs):
        with self.phase(name):
            return function(*args, **kwargs)

    def boolean(self, name, value):
        if name not in {'owner_binding_current', 'work_session_current'} or type(value) is not bool:
            raise ValueError('Unknown fixture diagnostic boolean')
        self.booleans[name] = value
        if self.enabled:
            print(json.dumps({'fixture_check': name, 'passed': value}), file=sys.stderr, flush=True)

    def failure_report(self, error):
        self.remember(error)
        return {'status': 'failed', **(self.failed or {'phase': 'launch', 'phase_path': [],
                'category': 'unclassified', 'native_phase': self.native_phase}), 'checks': dict(self.booleans)}


DIAG = Diagnostics()


def require_refusal(phase, operation, *args):
    """Catch only the expected negative observation inside its diagnostic scope."""
    with DIAG.phase(phase):
        try:
            operation(*args)
        except native.Refused:
            return
        raise RuntimeError('Expected native refusal was not observed')


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    network.private_write(path, data)


def audit_projection(original, projected):
    """Account for every synthetic leaf; production source/custody/paths stay fixed."""
    changed = []

    def permitted(path):
        if path == ('image_id',):
            return True
        if len(path) == 2 and path[0] == 'expected':
            return path[1] in original['expected']
        if len(path) == 2 and path[0] == 'incumbent':
            return path[1] in {'image_id', 'app_container_id', 'db_container_id', 'pds_container_id',
                               'caddy_container_id', 'caddyfile_sha256', 'receipt_sha256'}
        if len(path) == 2 and path[0] == 'identity':
            return path[1] in {'actor_signing_sha256', 'work_actor_id', 'work_atproto_did'}
        if len(path) == 3 and path[0] in {'compose_chain', 'retained_config'}:
            return path[2] in {'bytes', 'sha256'}
        return (len(path) == 3 and path[0] == 'ingress' and path[1] in {'live', 'fenced'}
                and path[2] in {'root_sha256', 'adapted_sha256'})

    def walk(before, after, path=()):
        require(type(before) is type(after), 'Fixture projection shape differs')
        if isinstance(before, dict):
            require(set(before) == set(after), 'Fixture projection keys differ')
            for key in before:
                walk(before[key], after[key], (*path, key))
        elif isinstance(before, list):
            require(len(before) == len(after), 'Fixture projection length differs')
            for i, (a, b) in enumerate(zip(before, after)):
                walk(a, b, (*path, i))
        elif before != after:
            require(permitted(path), 'Fixture changes nonallowlisted production input')
            changed.append({'path': list(path), 'production': before, 'synthetic': after})
    walk(original, projected)
    return changed


def check_semantics(a, b, acked_b, inbox, delivery_a, delivery_b):
    require([row['message_id'] for row in inbox['messages']] == ([] if acked_b else [b])
            and inbox['cursor'] == (2 if acked_b else 1), 'Retained inbox ACK semantics differ')
    for result, acknowledged in ((delivery_a, True), (delivery_b, acked_b)):
        require(result['accepted'] == 'accepted' and result['acknowledged'] is acknowledged
                and result['execution'] == 'unknown', 'Retained delivery semantics differ')
    require(a != b, 'Fixture messages must be distinct')


KEYS = r"""const c=require('node:crypto');const ed=c.generateKeyPairSync('ed25519');
const rsa=c.generateKeyPairSync('rsa',{modulusLength:3072});
process.stdout.write(JSON.stringify({pem:ed.privateKey.export({type:'pkcs8',format:'pem'}),
jwk:{...rsa.privateKey.export({format:'jwk'}),alg:'RS256',use:'sig',kid:'fixture-oidc'}}));"""

CLIENT = r"""const fs=require('node:fs'),c=require('node:crypto'),http=require('node:http');
const fail=(code,status)=>{const out={fixture_client_code:code};
 if(Number.isInteger(status)&&status>=100&&status<=599)out.fixture_http_status=status;
 fs.writeSync(1,JSON.stringify(out));process.exit(code);};
(async()=>{const d=JSON.parse(fs.readFileSync(0,'utf8'));
if(d.mode==='signup'){
 const r=await fetch('http://127.0.0.1:8787/api/auth/signup',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(d.account)});
 if(r.status!==200)fail(20,r.status);const j=await r.json();
 const token=r.headers.get('set-cookie')?.split(';')[0].split('=').slice(1).join('=');
 if(!j.actor?.id)fail(21);if(!token)fail(22);
 process.stdout.write(JSON.stringify({actor:j.actor.id,token}));return;
}
if(d.mode==='simulate-at'){
 const [h,p]=d.token.split('.');let claims;try{claims=JSON.parse(Buffer.from(p,'base64url'));}catch{fail(30);}
 claims.atproto_did=d.did;
 const payload=Buffer.from(JSON.stringify(claims)).toString('base64url'),input=h+'.'+payload;
 let token;try{const key=c.createPrivateKey(fs.readFileSync('/run/axxium/actor-signing.pem'));
 token=input+'.'+c.sign(null,Buffer.from(input),key).toString('base64url');}catch{fail(31);}
 process.stdout.write(JSON.stringify({actor:claims.sub,cred:claims.cred_id,token,hash:c.createHash('sha256').update(token).digest('hex')}));return;
}
const headers={host:'axxium.promethean.rest',accept:'application/json, text/event-stream','content-type':'application/json',authorization:'Bearer '+d.token};
if(d.sid){headers['mcp-session-id']=d.sid;headers['mcp-protocol-version']='2025-06-18';}
// Fetch removes a supplied Host. Keep the native canonical-origin guard while
// connecting only to this already ownership-checked container's loopback.
const body=JSON.stringify(d.request);headers['content-length']=Buffer.byteLength(body);
const r=await new Promise((resolve,reject)=>{
 const request=http.request({hostname:'127.0.0.1',port:8787,path:'/api/mailbox/mcp',method:'POST',headers},resolve);
 request.on('error',reject);request.setTimeout(30000,()=>request.destroy(new Error('Fixture client timeout')));request.end(body);
});
if(r.statusCode!==200){r.resume();fail(40,r.statusCode);}
const chunks=[];for await(const chunk of r)chunks.push(chunk);
const j=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(j.error)fail(41);if(j.result?.isError)fail(42);
process.stdout.write(JSON.stringify({sid:r.headers['mcp-session-id'],result:j.result?.structuredContent??j.result}));
})().catch(()=>fail(59));"""


def bind(source, target, readonly=True):
    return {'type': 'bind', 'source': str(source), 'target': target,
            'read_only': readonly, 'bind': {'create_host_path': False}}


class Fixture:
    def __init__(self, images):
        self.images = images
        self.token = self.sid = self.a = self.b = None
        self.acked_b = False
        self.checks = {'candidate': 0, 'incumbent': 0, 'clone_candidate': 0, 'clone_incumbent': 0}

    def owned(self, container):
        record = wire.inspect(container)
        labels = record['Config'].get('Labels') or {}
        probe = container.startswith('axxium-verify-')
        require((not probe and container == native.APP and labels.get('codex.axxium-recovery-fixture') == 'synthetic')
                or (probe and labels.get('open-hax.axxium-operation') == active.SOURCE + '-active-mailbox'),
                'Fixture MCP target is not owned')
        require(record['Image'] in set(self.images.values()) and not record['HostConfig'].get('PortBindings')
                and set(record['NetworkSettings']['Networks']) == ({'axxium_database'} if probe else set(network.NETWORKS)),
                'Fixture MCP target scope differs')
        for name in record['NetworkSettings']['Networks']:
            info = json.loads(wire.cmd(['docker', 'network', 'inspect', name]))[0]
            require(info.get('Internal') is True and info.get('Labels', {}).get('codex.axxium-recovery-fixture') == 'synthetic',
                    'Fixture network ownership differs')
        return record

    def request(self, container, value, host=None):
        def perform():
            self.owned(container)  # Recheck before every synthetic MCP/read/setup effect.
            args = ['docker', 'exec', '-i', container, 'node', '-e', CLIENT]
            if host is None:
                try:
                    result = subprocess.run(args, input=encode(value), stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, timeout=120)
                except (OSError, subprocess.TimeoutExpired):
                    raise ClientFailure('client-command-refused') from None
                if result.returncode:
                    raise client_problem(result.stdout, returncode=result.returncode)
                raw = result.stdout
            else:
                # Preserve native effect/lock/timeout behavior. Only the private
                # client output is captured so a failure can retain bounded
                # status metadata; an uncertain native outcome stays uncertain.
                with tempfile.TemporaryFile() as output:
                    try:
                        host.command(args, data=encode(value), effect=True, output=output)
                    except (native.Uncertain, native.KnownFailure) as error:
                        output.seek(0)
                        raise client_problem(output.read(4096), uncertain=isinstance(error, native.Uncertain)) from None
                    output.seek(0)
                    raw = output.read()
            return json.loads(raw)
        method = value.get('request', {}).get('method')
        tool = value.get('request', {}).get('params', {}).get('name')
        phase = {'register_mailbox': 'mailbox-register', 'send_message': 'mailbox-send',
                 'acknowledge_message': 'mailbox-ack', 'read_inbox': 'mailbox-read',
                 'get_delivery': 'mailbox-delivery'}.get(tool, 'mcp-initialize' if method == 'initialize' else 'mcp-request')
        return perform() if value.get('mode') else DIAG.call(phase, perform)

    def tool(self, container, name, arguments=None, host=None):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments or {}}}
        return self.request(container, {'token': self.token, 'sid': self.sid, 'request': request}, host)['result']

    def verify(self, container, host=None):
        record = self.owned(container)
        inbox = self.tool(container, 'read_inbox', host=host)  # No explicit after: ACK filter must apply.
        a = self.tool(container, 'get_delivery', {'message_id': self.a}, host)
        b = self.tool(container, 'get_delivery', {'message_id': self.b}, host)
        check_semantics(self.a, self.b, self.acked_b, inbox, a, b)
        role = next(role for role, image in self.images.items() if record['Image'] == image)
        self.checks[('clone_' if container.startswith('axxium-verify-') else '') + role] += 1

    def profile(self, root):
        identity = active.IDENTITY
        material = json.loads(DIAG.call('fixture-key-generation', wire.cmd, ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
                                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                                       '--entrypoint', 'node', self.images['incumbent'], '-e', KEYS]))
        private(active.SIGNING, material['pem'].encode())
        authority = {'issuer': native.ISSUER, 'clients': [{'id': 'axxium-mailbox-chatgpt-production',
            'secret': secrets.token_urlsafe(48), 'redirect-uris': ['https://chatgpt.com/connector_platform_oauth_redirect'],
            'refresh-enabled?': True, 'device-enabled?': False}],
            'cookie-keys': [secrets.token_urlsafe(48), secrets.token_urlsafe(48)], 'jwks': {'keys': [material['jwk']]},
            'mailbox-client-id': 'axxium-mailbox-chatgpt-production', 'mailbox-resource': 'https://axxium.promethean.rest/api/mailbox/mcp',
            'mailbox-actor-messaging?': True, 'mailbox-owner-consent?': True, 'mailbox-inbox?': True}
        private(identity / 'oidc/authority.json', encode(authority))
        for name in ('oidc/mailbox-webhook.key', 'owner/identity.key'):
            import base64
            private(identity / name, base64.b64encode(secrets.token_bytes(32)) + b'\n')
        policy = '{:policy/repos #{} :policy/permissions {} :policy/actions #{} :policy/forbid #{}}'
        seed = ('{:root {:id "' + OWNER + '" :name "Fixture owner" :policy ' + policy + '}'
                ' :nodes [{:id "' + WORK + '" :parent "' + OWNER + '" :name "Fixture Work" :kind :agent :policy ' + policy + '}]}\n')
        private(identity / 'owner/foresight-tree.edn', seed.encode())
        for path in active.LEDGERS.values():
            private(path, b'')
        self.environment = {'JWT_AUDIENCE': 'promethean+production', 'AXXIUM_SIGNING_KEY_FILE': '/run/axxium/actor-signing.pem',
            'AXXIUM_SIGNING_KID': 'fixture-active', 'AXXIUM_OIDC_CONFIG_FILE': '/run/axxium/oidc/authority.json',
            'AXXIUM_GITHUB_IDENTITY_KEY_FILE': '/run/axxium/owner/identity.key',
            'AXXIUM_GITHUB_IDENTITY_LEDGER_FILE': '/var/lib/axxium/identity/ledger.edn',
            'AXXIUM_IDENTITY_SEED_FILE': '/run/axxium/owner/foresight-tree.edn',
            'AXXIUM_IDENTITY_ROOT_DID': OWNER, 'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': PLACEHOLDER,
            'AXXIUM_MAILBOX_LEDGER_FILE': '/var/lib/axxium/mailbox/ledger.edn', 'AXXIUM_MAILBOX_DIRECTORY': 'true',
            'AXXIUM_MAILBOX_ACTOR_BINDINGS': json.dumps([[DID, WORK]]), **active.PLAN['transport'],
            'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': DID, 'AXXIUM_MAILBOX_WEBHOOK_KEY_FILE': '/run/axxium/oidc/mailbox-webhook.key'}
        self.volumes = [bind(root / 'state/axxium/google-client.json', '/run/axxium/google-client.json'),
            bind(active.SIGNING, '/run/axxium/actor-signing.pem'), bind(identity / 'oidc', '/run/axxium/oidc'),
            bind(identity / 'owner/identity.key', '/run/axxium/owner/identity.key'),
            bind(identity / 'owner/foresight-tree.edn', '/run/axxium/owner/foresight-tree.edn'),
            *(bind(path, target, False) for target, key in active.TARGETS.items() for path in [active.LEDGERS[key]])]
        # Preserve all four physical layers, with explicitly synthetic bytes.
        for row in active.PLAN['compose_chain'][1:]:
            private(Path(row['path']), encode({'services': {'axxium': {}}}))
        private(identity / 'mailbox.env', b'# synthetic active fixture\n')
        private(active.INCUMBENT_OPERATION / 'mailbox-identity-pins.json', b'{"synthetic":true}\n')
        caddyfile = network.CADDYFILE.replace(OLD, MAILBOX) + b'\nimport /etc/caddy/managed/*.caddy\n'
        return {'environment': self.environment, 'volumes': self.volumes, 'caddyfile': caddyfile}

    def proxy_ip(self):
        """Trust only the exact current fixture Caddy on its owned internal bridge."""
        record = wire.inspect(native.PROXY)
        require(record['State']['Running'] is True
                and record['Config'].get('Labels', {}).get('codex.axxium-recovery-fixture') == 'synthetic'
                and record['Image'] == wire.image_id('caddy@' + CADDY)
                and not record['HostConfig'].get('PortBindings')
                and set(record['NetworkSettings']['Networks']) == {'open-hax'}, 'Fixture proxy scope differs')
        bridge = json.loads(wire.cmd(['docker', 'network', 'inspect', 'open-hax']))[0]
        address = str(ipaddress.IPv4Address(record['NetworkSettings']['Networks']['open-hax']['IPAddress']))
        require(bridge.get('Internal') is True
                and bridge.get('Labels', {}).get('codex.axxium-recovery-fixture') == 'synthetic'
                and bridge.get('Containers', {}).get(record['Id'], {}).get('IPv4Address', '').split('/')[0] == address,
                'Fixture proxy bridge ownership differs')
        return address

    def setup_accounts_and_state(self):
        accounts = []
        for name in ('owner', 'work'):
            account = DIAG.call('owner-signup' if name == 'owner' else 'work-signup', self.request, native.APP, {'mode': 'signup', 'account': {
                'email': 'active-fixture-' + name + '@example.invalid', 'password': secrets.token_urlsafe(36)}})
            require(re.fullmatch(r'actor_[0-9a-f-]{36}', account['actor']), 'Synthetic account ID differs')
            accounts.append(account)
        owner, work = accounts
        proof = DIAG.call('simulate-at', self.request, native.APP, {'mode': 'simulate-at', 'token': work['token'], 'did': DID})
        require(proof['actor'] == work['actor'] and re.fullmatch(r'[0-9a-f-]{36}', proof['cred'])
                and re.fullmatch(r'[0-9a-f]{64}', proof['hash']), 'Synthetic AT proof fields differ')
        self.owner_actor, self.work_actor = owner['actor'], work['actor']
        self.token, self.cred = proof['token'], proof['cred']
        host = native.NativeHost()
        DIAG.call('credential-binding', host.sql, 'axxium', "INSERT INTO provider_bindings(provider,subject,actor_id) VALUES "
                 "('google','synthetic-fixture-owner','" + self.owner_actor + "'),('atproto','" + DID + "','" + self.work_actor + "');"
                 "UPDATE sessions SET token_hash='" + proof['hash'] + "' WHERE id='" + self.cred + "' AND actor_id='" + self.work_actor + "';")
        with DIAG.phase('credential-readback'):
            owner_current = host.sql('axxium', "SELECT EXISTS(SELECT 1 FROM actors a JOIN entities e ON e.id=a.entity_id "
                "JOIN provider_bindings p ON p.actor_id=a.id WHERE a.id='" + self.owner_actor + "' AND a.status='active' "
                "AND e.kind='human' AND p.provider='google' AND p.subject='synthetic-fixture-owner')")
            work_current = host.sql('axxium', "SELECT EXISTS(SELECT 1 FROM actors a JOIN sessions s ON s.actor_id=a.id "
                "JOIN provider_bindings p ON p.actor_id=a.id WHERE a.id='" + self.work_actor + "' AND a.status='active' "
                "AND s.id='" + self.cred + "' AND s.token_hash='" + proof['hash'] + "' AND s.expires_at>now() "
                "AND p.provider='atproto' AND p.subject='" + DID + "')")
            DIAG.boolean('owner_binding_current', owner_current.strip() == b't')
            DIAG.boolean('work_session_current', work_current.strip() == b't')
        self.environment['AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID'] = self.owner_actor
        self.environment['AXXIUM_TRUST_PROXY'] = DIAG.call('proxy-trust', self.proxy_ip)
        # Finish fixture setup before capturing the incumbent. No transaction
        # has begun, and only the two setup-owned app/profile files change.
        base = json.loads((native.RUNTIME / 'compose.yaml').read_bytes())
        base['services']['axxium']['environment'].update(self.environment)
        (native.RUNTIME / 'compose.yaml').write_bytes(encode(base))
        last = Path(active.PLAN['compose_chain'][-1]['path'])
        last.write_bytes(encode({'services': {'axxium': {'environment': self.environment, 'volumes': self.volumes}}}))
        prefix = ['docker', 'compose', '--project-name', 'axxium', '--project-directory', str(native.RUNTIME),
                  '--env-file', str(native.RUNTIME / '.env'), '-f', str(native.RUNTIME / 'compose.yaml')]
        with DIAG.phase('profile-restart'):
            wire.cmd(prefix + ['up', '-d', '--no-deps', '--pull', 'never', 'axxium'])
            network.wait_healthy(native.APP)
        initialized = self.request(native.APP, {'token': self.token, 'request': {
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'isolated-fixture', 'version': '1'}}}})
        self.sid = initialized['sid']
        require(isinstance(self.sid, str) and re.fullmatch(r'[0-9a-f-]{36}', self.sid), 'Native mailbox session unavailable')
        self.tool(native.APP, 'register_mailbox')
        self.a, self.b = [self.tool(native.APP, 'send_message', {'to': DID, 'idempotency_key': 'isolated-setup-' + name,
                         'body': {'fixture': name}})['message_id'] for name in ('a', 'b')]
        self.tool(native.APP, 'acknowledge_message', {'message_id': self.a})
        self.verify(native.APP)
        self.database_hash = self.identity_hash()
        self.initial_ledgers = active.ledger_bytes(active.LEDGERS)

    def identity_hash(self):
        host = native.NativeHost()
        rows = []
        actors = "('" + self.owner_actor + "','" + self.work_actor + "')"
        for table, where in (('actors', 'id IN ' + actors), ('provider_bindings', 'actor_id IN ' + actors),
                             ('sessions', 'actor_id IN ' + actors)):
            rows += host.sql('axxium', 'SELECT row_to_json(t)::text FROM ' + table + ' t WHERE ' + where).splitlines()
        return native.digest(b'\n'.join(sorted(rows)))


class Host(active.Host):
    """Only wire routing, real parser assertions and post-effect fault injection."""
    def __init__(self, plan_hash, fixture, scenario):
        super().__init__(plan_hash)
        self.fixture, self.scenario = fixture, scenario
        self.probing = False
        self.injected = False

    def http(self, path):
        return DIAG.call('ingress-http', wire.container_http, native.PROXY, path)

    def probe(self, *args, **kwargs):
        self.probing = True
        try:
            return super().probe(*args, **kwargs)
        finally:
            self.probing = False

    def command(self, args, **kwargs):
        if self.probing and args[:4] == ['docker', 'stop', '--time', '610']:
            self.fixture.verify(args[4], self)
        try:
            return super().command(args, **kwargs)
        except Exception as error:
            if DIAG.enabled and not (args and args[-1] == CLIENT):
                DIAG.remember(error)
            raise

    def record(self, name, status):
        super().record(name, status)
        DIAG.native_phase = name if name in NATIVE_PHASES else 'none'

    def start_candidate(self, plan):
        super().start_candidate(plan)
        if self.scenario == 'uncertain':
            require(self.inspect(native.APP)['State']['Running'], 'Actual candidate start not established')
            self.injected = True
            raise ExpectedUncertainty('Synthetic uncertainty after actual candidate start')

    def private_health(self, plan):
        return DIAG.call('candidate-health', self._private_health, plan)

    def _private_health(self, plan):
        super().private_health(plan)
        self.fixture.verify(native.APP, self)
        if self.scenario == 'known-failure' and not self.injected:
            self.fixture.tool(native.APP, 'acknowledge_message', {'message_id': self.fixture.b}, self)
            self.fixture.acked_b = True
            self.fixture.verify(native.APP, self)
            self.fixture.failure_ledgers = active.ledger_bytes(active.LEDGERS)
            self.injected = True
            raise ExpectedKnownFailure('Synthetic known failure after real candidate ACK')

    def public_health(self, plan):
        return DIAG.call('public-health', self._public_health, plan)

    def _public_health(self, plan):
        super().public_health(plan)
        self.fixture.verify(native.APP, self)


def project_plan(fixture):
    plan = deepcopy(ORIGINAL_PLAN)
    observer = native.NativeHost()
    plan['image_id'] = fixture.images['candidate']
    expected = plan['expected']
    for name, prefix in ((native.APP, 'app'), (native.DB, 'db'), (native.PDS, 'pds'), (native.PROXY, 'caddy')):
        selected = wire.inspect(name)
        expected[prefix + '_container_id'], expected[prefix + '_image_id'] = selected['Id'], selected['Image']
        if prefix != 'caddy':
            expected[prefix + '_environment_sha256'] = observer.environment_hash(selected)
    expected['caddy_proxy_ip'] = wire.inspect(native.PROXY)['NetworkSettings']['Networks']['open-hax']['IPAddress']
    expected['app_writable_layer_sha256'] = native.digest(json.dumps(sorted(wire.cmd(['docker', 'diff', native.APP]).decode().splitlines()), separators=(',', ':')).encode())
    for key, path in (('compose_sha256', native.RUNTIME / 'compose.yaml'), ('environment_file_sha256', native.RUNTIME / '.env'),
                      ('caddyfile_sha256', native.CADDY)):
        expected[key] = native.file_digest(path)
    expected['managed_caddy_sha256'] = observer.managed_digest()
    for group in ('compose_chain', 'retained_config'):
        for row in plan[group]:
            data = Path(row['path']).read_bytes()
            row.update(bytes=len(data), sha256=native.digest(data))
    expected['incumbent_overlay_sha256'] = plan['compose_chain'][1]['sha256']
    plan['identity'].update(work_actor_id=fixture.work_actor, work_atproto_did=DID,
                            actor_signing_sha256=native.file_digest(active.SIGNING))
    for prefix in ('app', 'db', 'pds', 'caddy'):
        plan['incumbent'][prefix + '_container_id'] = expected[prefix + '_container_id']
    plan['incumbent'].update(image_id=fixture.images['incumbent'], caddyfile_sha256=expected['caddyfile_sha256'])
    receipt = {'schema': 'axxium-unified-mailbox-deployment/v1', 'status': 'healthy',
               **{key: plan['incumbent'][key] for key in ('source_sha', 'image_id', 'app_container_id', 'plan_sha256')}}
    raw = encode(receipt)
    private(active.INCUMBENT_OPERATION / 'deployment.json', raw)
    plan['incumbent']['receipt_sha256'] = native.digest(raw)
    with observer.lock():
        live = ingress.readback(observer, expected['caddy_container_id'], native.CADDY, native.MANAGED)
        require(live['startup_sha256'] == live['active_sha256'], 'Synthetic ingress startup differs')
        fenced, _ = prepare_active_mailbox(native.CADDY.read_bytes(), expected['caddyfile_sha256'])
        adapted = ingress._adapted(observer, expected['caddy_container_id'], '/dev/stdin', data=fenced)
    plan['ingress'] = {'live': {'root_sha256': live['root_sha256'], 'adapted_sha256': live['active_sha256']},
                       'fenced': {'root_sha256': native.digest(fenced), 'adapted_sha256': ingress._config_hash(adapted)}}
    differences = audit_projection(ORIGINAL_PLAN, plan)
    require(plan['image_id'] != plan['incumbent']['image_id'], 'Fixture requires distinct real image roles')
    active.PLAN, active.PLAN_BYTES = plan, encode(plan)
    active.PLAN_SHA256, active.IMAGE = native.digest(active.PLAN_BYTES), plan['image_id']
    active.Host.release_image = plan['image_id']
    return plan, differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--incumbent', type=Path, required=True)
    parser.add_argument('--scenario', choices=('healthy', 'known-failure', 'uncertain'), required=True)
    with DIAG.phase('arguments'):
        args = parser.parse_args()
    with DIAG.phase('environment-guard'):
        require(os.getuid() == 1000 and os.environ.get('GITHUB_ACTIONS') == 'true'
                and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
                and os.environ.get('DOCKER_HOST') == 'unix:///var/run/docker.sock' and not os.environ.get('DOCKER_CONTEXT'),
                'Disposable hosted VM and local Unix Docker required')
        os.umask(0o077)
        fixture_netns.require_isolated()
    with DIAG.phase('archive-custody'):
        bindings = {role: verify_role(role, getattr(args, role)) for role in ('candidate', 'incumbent')}
    with DIAG.phase('image-resolution'):
        images = {role: runtime_id(role, wire.image_id('promethean-axxium:' + role_plan(role)['source_sha'])) for role in bindings}
    fixture = Fixture(images)
    with DIAG.phase('topology-setup'):
        network.setup(ROOT, images['incumbent'], wire.image_id('postgres@' + POSTGRES), wire.image_id('caddy@' + CADDY),
                      prepare_app=fixture.profile)
    DIAG.call('account-setup', fixture.setup_accounts_and_state)
    plan, differences = DIAG.call('plan-projection', project_plan, fixture)
    private(ROOT / 'state/active-fixture-projection.json', encode({'scope': 'synthetic fixture only', 'differences': differences}))
    material_paths = [active.SIGNING, *(Path(row['path']) for row in plan['retained_config'])]
    material_hashes = {str(path): native.file_digest(path) for path in material_paths}
    host = Host(active.PLAN_SHA256, fixture, args.scenario)
    if args.scenario == 'uncertain':
        try:
            DIAG.call('native-transaction', active.execute_admitted, plan, host)
        except native.Uncertain:
            require(host.injected and native.PENDING.is_file(), 'Actual uncertain effect did not retain its hold')
        else:
            raise RuntimeError('Uncertainty was not observed')
        hold = native.PENDING.read_bytes()
        journal = {p.name: native.file_digest(p) for p in host.operation.glob('*.json')}
        retry = Host(active.PLAN_SHA256, fixture, 'healthy')
        require_refusal('native-retry', active.execute_admitted, plan, retry)
        for method in (retry.recovery_preflight, retry.recovery_claim):
            require_refusal('native-recovery-refusal', method, plan)
        require(native.PENDING.read_bytes() == hold and journal == {p.name: native.file_digest(p) for p in host.operation.glob('*.json')},
                'Uncertain hold or journal changed on retry')
        status = 'held'
    else:
        result = DIAG.call('native-transaction', active.execute_admitted, plan, host)
        status = result['status']
        require(status == ('healthy' if args.scenario == 'healthy' else 'rolled_back') and not native.PENDING.exists(),
                'Native terminal result differs')
        selected = wire.inspect(native.APP)
        require(selected['Image'] == images['candidate' if status == 'healthy' else 'incumbent'], 'Final fixture image differs')
        fixture.verify(native.APP)
        if args.scenario == 'known-failure':
            require(host.injected and fixture.acked_b, 'Known failure occurred before real candidate ACK')
            active.preserve_ledgers(fixture.failure_ledgers, active.ledger_bytes(active.LEDGERS))
    active.preserve_ledgers(fixture.initial_ledgers, active.ledger_bytes(active.LEDGERS))
    require(fixture.checks['clone_candidate'] > 0 and fixture.checks['clone_incumbent'] > 0,
            'Both real clone image parsers must be exercised')
    require(material_hashes == {str(path): native.file_digest(path) for path in material_paths},
            'Retained signing or identity material changed')
    require(fixture.identity_hash() == fixture.database_hash, 'Retained account or session identity changed')
    print(json.dumps({'status': status, 'scenario': args.scenario, 'source_sha': active.SOURCE,
        'images': images, 'archive_bindings': bindings, 'native_parser_checks': fixture.checks,
        'synthetic_setup_messages': 2, 'synthetic_setup_acknowledgements': 1,
        'candidate_acknowledgement': fixture.acked_b, 'production_fingerprints_match': False,
        'production_qualified': False, 'provider_login_verified': False, 'webhook_enrollment': 'disabled'}, sort_keys=True))


def run(operation=None, diagnostics=None):
    diagnostics = DIAG if diagnostics is None else diagnostics
    diagnostics.enabled = True
    try:
        (main if operation is None else operation)()
        return 0
    except Exception as error:
        print(json.dumps(diagnostics.failure_report(error), sort_keys=True), flush=True)
        print('Isolated active-upgrade fixture failed; retain its private state and native journal.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(run())
