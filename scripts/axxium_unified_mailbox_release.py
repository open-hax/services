#!/usr/bin/env python3
"""Deploy the reviewed shared-ingress Axxium mailbox release under the native hold."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

import axxium_ingress_caddy as caddy
import axxium_minimal_release as release
from axxium_oidc_ingress import prepare_mailbox
from axxium_production_qualification import Refusal, strict_json, validate_plan as validate_custody


ORIGIN = 'https://axxium.promethean.rest'
RESOURCE_PATH = '/api/mailbox/mcp'
METADATA_PATH = '/.well-known/oauth-protected-resource' + RESOURCE_PATH
SCHEMA = 'axxium-unified-mailbox-production/v1'
SOURCE = 'd2f8dc8425f86f51a8450a0cae0b79a042694c64'
IMAGE = 'sha256:265f085671493305eb76d2c4b46463340b915b4ad3934b0c8c33ed0c6c887f57'
INCUMBENT_SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
INCUMBENT = release.STATE / 'operations' / INCUMBENT_SOURCE / 'candidate.compose.json'
SIGNING_TARGET = '/run/axxium/actor-signing.pem'
ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'digitalocean/services/axxium/mailbox-production.overlay.yaml'
INPUT = ROOT / 'production-mailbox-binding.json'
SEED = ROOT / 'production-mailbox-seed.edn'
PROVISIONER = Path(__file__).with_name('provision_axxium_mailbox_identity.py')
HEX = re.compile(r'[0-9a-f]{64}')
ACTOR = re.compile(r'actor_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
ATPROTO_DID = re.compile(r'did:plc:[a-z2-7]{24}')
ROOT_ACTOR = 'did:web:axxium.promethean.rest:actors:err'
CODEX_ACTOR = ROOT_ACTOR + ':hosts:stealth:codex'
FIXTURE_MAILBOX = 'did:plc:aaaaaaaaaaaaaaaaaaaaaaaa'
PREFLIGHT_BINDING = {
    'AXXIUM_IDENTITY_ROOT_DID': ROOT_ACTOR,
    'AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID': 'actor_00000000-0000-4000-8000-000000000000',
    'AXXIUM_MAILBOX_ACTOR_BINDINGS': json.dumps([[FIXTURE_MAILBOX, ROOT_ACTOR + ':work']]),
    'AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID': FIXTURE_MAILBOX,
    'AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS': CODEX_ACTOR,
}
WRITABLE = frozenset(('/var/lib/axxium/identity/ledger.edn', '/var/lib/axxium/mailbox/ledger.edn'))
CONFIG_PATHS = ('mailbox.env', 'oidc/authority.json', 'oidc/mailbox-webhook.key',
                'owner/identity.key', 'owner/foresight-tree.edn')


def require(condition, reason):
    if not condition:
        raise release.Refused(reason)


def digest_value(value):
    return isinstance(value, str) and HEX.fullmatch(value)


def at_auth_operation():
    return release.STATE / 'operations' / (SOURCE + '-at-auth')


def validate_plan(plan):
    keys = {'schema','host','source_sha','image_id','source_pr','artifact_id','artifact_sha256',
            'stage_run_id','stage_workflow_path','stage_controller_sha','expected','authority',
            'identity','at_auth','ingress','overlay_sha256'}
    require(isinstance(plan, dict) and set(plan) == keys and plan['schema'] == SCHEMA
            and plan['source_sha'] == SOURCE and plan['image_id'] == IMAGE and plan['source_pr'] == 83
            and plan['host'] == 'knoxx.promethean.rest', 'Unreviewed unified mailbox plan')
    validate_custody({**plan, 'schema': 'axxium-minimal-production/v1'})
    expected = {'app_container_id','app_image_id','app_environment_sha256','app_writable_layer_sha256',
                'db_container_id','db_image_id','db_environment_sha256','pds_container_id','pds_image_id',
                'pds_environment_sha256','caddy_container_id','caddy_image_id','compose_sha256',
                'environment_file_sha256','caddyfile_sha256','managed_caddy_sha256','caddy_proxy_ip',
                'incumbent_overlay_sha256'}
    require(isinstance(plan['expected'], dict) and set(plan['expected']) == expected
            and plan['expected']['app_image_id'] == IMAGE,
            'Incomplete app-only incumbent snapshot')
    for key, value in plan['expected'].items():
        if key == 'caddy_proxy_ip':
            ipaddress.IPv4Address(value)
        elif key.endswith('_image_id'):
            require(isinstance(value, str) and value.startswith('sha256:') and digest_value(value[7:]),
                    'Invalid incumbent image')
        else:
            require(digest_value(value), 'Invalid incumbent digest')
    require(plan['authority'] == {'issuer': release.ISSUER, 'client_id': 'axxium-mailbox-chatgpt-production',
                                 'callback_uri': 'https://chatgpt.com/connector_platform_oauth_redirect'},
            'Production Work authority differs')
    auth = plan['at_auth']
    pins = {'overlay_sha256','actor_signing_sha256','receipt_sha256'}
    require(isinstance(auth, dict) and set(auth) == pins | {'overlay_path'}
            and auth['overlay_path'] == str(at_auth_operation() / 'candidate.compose.json')
            and all(digest_value(auth[key]) for key in pins), 'Incomplete app-only AT release pins')
    keys = {'incumbent_authority_sha256','seed_sha256','work_client_id','work_atproto_did','work_actor_id'}
    identity = plan['identity']
    require(isinstance(identity, dict) and set(identity) == keys
            and identity['work_client_id'] == plan['authority']['client_id']
            and isinstance(identity['work_atproto_did'], str) and ATPROTO_DID.fullmatch(identity['work_atproto_did'])
            and isinstance(identity['work_actor_id'], str) and ACTOR.fullmatch(identity['work_actor_id'])
            and all(digest_value(identity[key]) for key in ('incumbent_authority_sha256','seed_sha256')),
            'Incomplete production identity binding')
    ingress = plan['ingress']
    require(isinstance(ingress, dict) and set(ingress) == {'incumbent','fenced','live'},
            'Incomplete immutable ingress selection')
    for phase in ingress.values():
        require(isinstance(phase, dict) and set(phase) == {'root_sha256','adapted_sha256'}
                and all(digest_value(value) for value in phase.values()), 'Invalid ingress digest')
    require(ingress['incumbent']['root_sha256'] == plan['expected']['caddyfile_sha256']
            and len({row['root_sha256'] for row in ingress.values()}) == 3
            and digest_value(plan['overlay_sha256']), 'Conflicting ingress or Compose pins')
    return plan


def verify_metadata(raw):
    data = strict_json(raw)
    if (not isinstance(data, dict) or data.get('resource') != ORIGIN + RESOURCE_PATH
            or data.get('authorization_servers') != [release.ISSUER]
            or set(data.get('scopes_supported', [])) != {'mailbox:events','mailbox:actors','mailbox:inbox'}):
        raise release.KnownFailure('Mailbox discovery differs from the production resource')


def validate_projection(current, candidate, image, identity_directory, signing_path):
    baseline, selected = (d['services']['axxium'] for d in (current, candidate))
    for document in (current, candidate):
        document['services'].pop('axxium')
    require(current == candidate, 'Mailbox Compose changed unrelated services or project settings')
    env, original = selected['environment'], baseline['environment']
    require(baseline['image'] == image and original.get('JWT_AUDIENCE') == 'promethean+production'
            and original.get('AXXIUM_SIGNING_KEY_FILE') == SIGNING_TARGET,
            'App-only signing environment differs')
    settings = {'AXXIUM_OIDC_CONFIG_FILE':'/run/axxium/oidc/authority.json',
                'AXXIUM_GITHUB_IDENTITY_KEY_FILE':'/run/axxium/owner/identity.key',
                'AXXIUM_GITHUB_IDENTITY_LEDGER_FILE':'/var/lib/axxium/identity/ledger.edn',
                'AXXIUM_IDENTITY_SEED_FILE':'/run/axxium/owner/foresight-tree.edn',
                'AXXIUM_IDENTITY_ROOT_DID':ROOT_ACTOR,
                'AXXIUM_MAILBOX_LEDGER_FILE':'/var/lib/axxium/mailbox/ledger.edn',
                'AXXIUM_MAILBOX_DIRECTORY':'true', 'AXXIUM_MAILBOX_STREAM_ENABLED':'true',
                'AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED':'false',
                'AXXIUM_MAILBOX_WEBHOOK_KEY_FILE':'/run/axxium/oidc/mailbox-webhook.key'}
    work = env['AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID']
    operator = env['AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID']
    require(ATPROTO_DID.fullmatch(work) and ACTOR.fullmatch(operator),
            'Resolved owner or Work actor differs')
    settings.update(AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID=work,
                    AXXIUM_IDENTITY_ROOT_OPERATOR_ACTOR_ID=operator,
                    AXXIUM_MAILBOX_ACTOR_BINDINGS=env['AXXIUM_MAILBOX_ACTOR_BINDINGS'],
                    AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS=env['AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS'],
                    AXXIUM_MAILBOX_BOUND_BOOTSTRAP_BINDINGS=None)
    require(strict_json(settings['AXXIUM_MAILBOX_ACTOR_BINDINGS']) == [[work,ROOT_ACTOR + ':work']]
            and settings['AXXIUM_MAILBOX_BOOTSTRAP_ACTOR_DIDS'] == CODEX_ACTOR,
            'Resolved mailbox actor policy differs')
    require(selected['image'] == image and env == {**original, **settings},
            'Resolved mailbox image or environment differs')
    def by_target(service):
        volumes = {row['target']: row for row in service['volumes']}
        require(len(volumes) == len(service['volumes']), 'Ambiguous Axxium mount targets')
        return volumes
    before, after = by_target(baseline), by_target(selected)
    signing = before.get(SIGNING_TARGET, {})
    require(signing.get('type') == 'bind' and signing.get('source') == str(signing_path)
            and signing.get('read_only') is True and signing.get('bind', {}).get('create_host_path') is False,
            'App-only signing bind differs')
    expected = {
        '/run/axxium/oidc': (identity_directory / 'oidc', True),
        '/run/axxium/owner/identity.key': (identity_directory / 'owner/identity.key', True),
        '/run/axxium/owner/foresight-tree.edn': (identity_directory / 'owner/foresight-tree.edn', True),
        '/var/lib/axxium/identity/ledger.edn': (release.STATE / 'identity/ledger.edn', False),
        '/var/lib/axxium/mailbox/ledger.edn': (release.STATE / 'mailbox/ledger.edn', False),
    }
    require(set(after) == set(before) | set(expected), 'Unexpected app bind addition or removal')
    for target, (path, readonly) in expected.items():
        row = after[target]
        require(row['type'] == 'bind' and row['source'] == str(path)
                and row.get('read_only', False) is readonly and row.get('bind', {}).get('create_host_path') is False,
                'Resolved mailbox mount differs')
    require(all(after[path] == row for path, row in before.items() if path not in expected)
            and {k:v for k,v in baseline.items() if k not in {'image','environment','volumes'}}
            == {k:v for k,v in selected.items() if k not in {'image','environment','volumes'}},
            'Mailbox Compose changed incumbent application settings')
    return selected


class Host(release.NativeHost):
    release_source = SOURCE
    release_image = IMAGE

    def __init__(self, plan, plan_path, plan_hash):
        super().__init__(plan_hash)
        self.plan_path = plan_path
        self.plan = plan
        self.target_operation = release.STATE / 'operations' / SOURCE
        self.secret_directory = self.target_operation / 'mailbox-identity'
        self.at_auth_overlay = Path(plan['at_auth']['overlay_path'])
        self.at_auth_signing = at_auth_operation() / 'actor-signing.pem'
        self.prepared = None
        self.phase = 'incumbent'
        self.before_caddy = None

    def unchanged_dependencies(self, plan):
        super().unchanged_dependencies(plan)
        release.controlled_file(INCUMBENT, secret=True)
        if (release.file_digest(INCUMBENT) != plan['expected']['incumbent_overlay_sha256']
                or release.file_digest(OVERLAY) != plan['overlay_sha256']):
            raise release.Uncertain('Retained Compose or mailbox overlay changed')
        operation = at_auth_operation()
        pins = plan['at_auth']
        if operation.resolve() != operation:
            raise release.Uncertain('App-only AT operation path differs')
        for file, pin in ((self.at_auth_overlay, 'overlay_sha256'),
                          (self.at_auth_signing, 'actor_signing_sha256')):
            if release.digest(release.private_read(file)) != pins[pin]:
                raise release.Uncertain('App-only AT overlay or signing key changed')
        raw = release.private_read(operation / 'deployment.json')
        if release.digest(raw) != pins['receipt_sha256']:
            raise release.Uncertain('App-only AT deployment receipt changed')
        receipt = strict_json(raw)
        expected = {'schema':'axxium-at-auth-deployment/v1', 'status':'healthy', 'source_sha':SOURCE,
                    'image_id':IMAGE, 'app_container_id':plan['expected']['app_container_id'],
                    'operation':str(operation), 'actor_signing_sha256':pins['actor_signing_sha256'],
                    'retained_overlay_sha256':pins['overlay_sha256']}
        if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
            raise release.Uncertain('App-only AT deployment differs from the reviewed incumbent')

    def provision(self, mode):
        args = [sys.executable, str(PROVISIONER), mode, '--plan', str(self.plan_path),
                '--input', str(INPUT), '--seed', str(SEED), '--operation', str(self.target_operation)]
        if mode == '--prepare':
            self.command(args + ['--lock-fd', str(self.lock_fd)], timeout=120, effect=True, pass_fds=(self.lock_fd,))
        else:
            self.command(args)

    def identity_preflight(self):
        self.verify_signing_bind(self.inspect(release.APP))
        self.provision('--preflight')
        self.mailbox_projection(self.plan, preflight=True)

    def identity_snapshot(self):
        return {name: release.digest(release.private_read(self.secret_directory / name)) for name in CONFIG_PATHS}

    def identity_check(self):
        if self.prepared is None or self.prepared != self.identity_snapshot():
            raise release.Uncertain('Prepared production identity material differs')

    def provision_identity(self, plan):
        self.unchanged_dependencies(plan)
        if self.operation != self.target_operation or self.lock_fd is None or 'before' not in self.backups:
            raise release.Uncertain('Identity preparation requires the locked backup')
        self.provision('--prepare')
        self.recheck_lock()
        self.prepared = self.identity_snapshot()
        self.private_write(self.operation / 'mailbox-identity-pins.json', json.dumps(self.prepared).encode())

    def route_candidates(self, plan):
        observed = caddy.readback(self, plan['expected']['caddy_container_id'], release.CADDY, release.MANAGED)
        selected = plan['ingress']['incumbent']
        require(observed['active_sha256'] == observed['startup_sha256'] == selected['adapted_sha256']
                and observed['root_sha256'] == selected['root_sha256']
                and caddy.ROUTE_NAME not in observed['managed'], 'Caddy incumbent differs from the shared ingress plan')
        self.before_caddy = observed
        fenced, live = prepare_mailbox(self.original, selected['root_sha256'])
        require(release.digest(fenced) == plan['ingress']['fenced']['root_sha256']
                and release.digest(live) == plan['ingress']['live']['root_sha256'], 'Caddy targets differ')
        return fenced, live

    def replace_caddy(self, plan, data):
        self.unchanged_dependencies(plan)
        root_sha = release.digest(data)
        phase, = (name for name, row in plan['ingress'].items() if row['root_sha256'] == root_sha)
        self.before_caddy = caddy.publish(
            self, plan['expected']['caddy_container_id'], release.CADDY, release.MANAGED,
            root_bytes=data, route_name=caddy.ROUTE_NAME, route_bytes=None, expected_before=self.before_caddy,
            expected_adapted_sha256=plan['ingress'][phase]['adapted_sha256'],
        )
        self.phase = phase

    def compose_command(self, overlay, *, preflight=False):
        runtime = release.RUNTIME
        command = ['docker','compose','--project-name','axxium','--project-directory',str(runtime),
                   '--env-file',str(runtime / '.env')]
        if overlay == OVERLAY and not preflight:
            self.identity_check()
            command += ['--env-file',str(self.secret_directory / 'mailbox.env')]
        command += ['-f',str(runtime / 'compose.yaml'),'-f',str(INCUMBENT),'-f',str(self.at_auth_overlay)]
        if overlay is not None:
            command += ['-f',str(overlay)]
        return command

    def compose_environment(self):
        return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(Path.home()),
                'AXXIUM_PRODUCTION_MAILBOX_IMAGE': self.plan['image_id']}

    def compose(self, *args, overlay=None):
        return self.command(self.compose_command(overlay) + list(args), timeout=660, effect=True,
                            env=self.compose_environment())

    def mailbox_projection(self, plan, *, preflight=False):
        env = self.compose_environment()
        if preflight:
            env.update(PREFLIGHT_BINDING, AXXIUM_PRODUCTION_MAILBOX_IDENTITY_PATH=str(self.secret_directory))
        def config(overlay):
            command = self.compose_command(overlay, preflight=preflight)
            return strict_json(self.command(command + ['config','--format','json'], env=env))
        return validate_projection(config(None), config(OVERLAY), plan['image_id'], self.secret_directory,
                                   self.at_auth_signing)

    def probe_profile(self, plan, image, name, values, *, candidate):
        if not candidate:
            values, mounts = super().probe_profile(plan, image, name, values, candidate=False)
            return values, mounts + ['--mount', f'type=bind,src={self.at_auth_signing},dst={SIGNING_TARGET},readonly']
        service = self.mailbox_projection(plan)
        values.update({key: value for key, value in service['environment'].items()
                       if value is not None and (key.startswith(('AXXIUM_MAILBOX_', 'AXXIUM_IDENTITY_',
                                                                 'AXXIUM_GITHUB_IDENTITY_'))
                                                 or key in ('AXXIUM_SIGNING_KEY_FILE','JWT_AUDIENCE'))})
        mounts = []
        for volume in service['volumes']:
            target = volume['target']
            require(volume['type'] == 'bind' and ',' not in volume['source'] and ',' not in target,
                    'Unexpected Axxium rehearsal mount type')
            if target in WRITABLE:
                path = self.operation / (name + '-' + Path(target).parent.name + '.edn')
                self.private_write(path, b'')
                mounts += ['--mount',f'type=bind,src={path},dst={target}']
            else:
                require(volume.get('read_only') is True, 'Unexpected writable production rehearsal bind')
                mounts += ['--mount',f'type=bind,src={volume["source"]},dst={target},readonly']
        return values, mounts

    def start_candidate(self, plan):
        self.unchanged_dependencies(plan)
        if self.candidate_id is None:
            self.original_layer(plan)
        self.mailbox_projection(plan)
        self.compose('up','-d','--no-deps','--pull','never','axxium',overlay=OVERLAY)
        selected = self.inspect(release.APP)
        if selected['Image'] != plan['image_id']:
            raise release.Uncertain('Candidate image differs after Compose start')
        self.candidate_id = selected['Id']

    def private_health(self, plan):
        super().private_health(plan)
        self.identity_check()
        service = self.mailbox_projection(plan)
        selected = self.inspect(release.APP)
        environment = dict(entry.split('=', 1) for entry in selected['Config']['Env'])
        mounts = {volume['Destination']: (volume['Source'], volume['RW']) for volume in selected['Mounts']}
        for key, value in service['environment'].items():
            if key.startswith(('AXXIUM_MAILBOX_', 'AXXIUM_IDENTITY_', 'AXXIUM_GITHUB_IDENTITY_')) or key in (
                    'JWT_AUDIENCE', 'AXXIUM_SIGNING_KEY_FILE', 'AXXIUM_OIDC_CONFIG_FILE'):
                if environment.get(key) != value:
                    raise release.Uncertain('Running mailbox environment differs')
        for volume in service['volumes']:
            if mounts.get(volume['target']) != (volume['source'], not volume.get('read_only', False)):
                raise release.Uncertain('Running mailbox bind differs')
        raw = self.command(['docker','exec',release.PROXY,'wget','-q','-O','-',
                            '--header','X-Forwarded-Proto: https','--header','Host: axxium.promethean.rest',
                            'http://axxium:8787'+METADATA_PATH])
        verify_metadata(raw)

    def verify_preserved_public_routes(self):
        for path, field, expected in (
                ('/api/auth/atproto/client-metadata.json', 'client_id', ORIGIN + '/api/auth/atproto/client-metadata.json'),
                ('/xrpc/com.atproto.server.describeServer', 'did', 'did:web:axxium.promethean.rest')):
            status, raw = self.http(path)
            if status != 200 or strict_json(raw).get(field) != expected:
                raise release.KnownFailure('Preserved production identity route differs')
        status, raw = self.http('/oidc/.well-known/openid-configuration')
        if status != 200:
            raise release.KnownFailure('Preserved OIDC discovery is unavailable')
        metadata = strict_json(raw)
        if metadata.get('jwks_uri') != release.ISSUER + '/jwks':
            raise release.KnownFailure('Preserved OIDC key endpoint differs')
        status, raw = self.http('/oidc/jwks')
        keys = strict_json(raw).get('keys', []) if status == 200 else []
        if not keys or any(key.get('kty') != 'RSA' or not key.get('kid')
                           or set(key) & {'d','p','q','dp','dq','qi','oth'} for key in keys):
            raise release.KnownFailure('Preserved public OIDC keyset differs')

    def public_health(self, plan):
        super().public_health(plan)
        self.verify_preserved_public_routes()
        status, raw = self.http(METADATA_PATH)
        if status != 200 or self.http(RESOURCE_PATH)[0] != 401:
            raise release.KnownFailure('Public mailbox discovery or authentication gate differs')
        verify_metadata(raw)

    def verify_rollback(self, plan, selected):
        self.verify_preserved_public_routes()
        self.verify_signing_bind(selected)
        if self.http(METADATA_PATH)[0] != 404 or self.http(RESOURCE_PATH)[0] != 404:
            raise release.Uncertain('Rollback did not close the production mailbox')
        authorities = [row for row in selected['Mounts'] if row['Destination'] == '/run/axxium/oidc']
        if (len(authorities) != 1 or authorities[0]['Type'] != 'bind' or authorities[0]['RW'] is not False
                or authorities[0]['Source'] != str(release.STATE / 'oidc')
                or release.file_digest(release.STATE / 'oidc/authority.json') != plan['identity']['incumbent_authority_sha256']):
            raise release.Uncertain('Rollback did not restore the existing OIDC authority bind')

    def verify_signing_bind(self, selected):
        mounts = [row for row in selected['Mounts'] if row['Destination'] == SIGNING_TARGET]
        if (len(mounts) != 1 or mounts[0]['Type'] != 'bind' or mounts[0]['RW'] is not False
                or mounts[0]['Source'] != str(self.at_auth_signing)):
            raise release.Uncertain('App-only actor signing key bind differs')

    def commit(self, plan):
        self.unchanged_dependencies(plan)
        self.identity_check()
        current = self.inspect(release.APP)
        observed = caddy.readback(self, plan['expected']['caddy_container_id'], release.CADDY, release.MANAGED)
        if (current['Id'] != self.candidate_id or current['Image'] != plan['image_id']
                or current['State'].get('Health', {}).get('Status') != 'healthy'
                or self.phase != 'live' or observed != self.before_caddy):
            raise release.Uncertain('Final mailbox or ingress selection differs')
        self.public_health(plan)
        self.private_write(self.operation / 'deployment.json', json.dumps(self.receipt('healthy'), sort_keys=True).encode())

    def receipt(self, status):
        return {**super().receipt(status), 'schema': 'axxium-unified-mailbox-deployment/v1',
                'mailbox_discovery_verified': status == 'healthy', 'mailbox_messages_sent': False,
                'connector_identity_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--plan-sha256', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        raw = release.private_read(args.plan)
        require(release.digest(raw) == args.plan_sha256, 'Reviewed plan digest differs')
        plan = validate_plan(strict_json(raw))
        host = Host(plan, args.plan, args.plan_sha256)
        if args.check:
            with host.lock():
                host.preflight(plan)
            result = {'status':'preflight_passed','source_sha':SOURCE,'image_id':IMAGE}
        else:
            result = release.execute_admitted(plan, host)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] in {'healthy','preflight_passed'} else 3
    except (release.Refused, Refusal, OSError, ValueError, KeyError, TypeError):
        print('Unified Axxium mailbox release refused. Preserve the native operation journal.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
