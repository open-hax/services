#!/usr/bin/env python3
"""Restore primary AT sign-in on the existing Axxium service."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import axxium_ingress_caddy as caddy
import axxium_minimal_release as release
from axxium_oidc_ingress import _fence
from axxium_production_qualification import Refusal, strict_json, validate_plan as validate_custody

SOURCE = 'd2f8dc8425f86f51a8450a0cae0b79a042694c64'
IMAGE = 'sha256:265f085671493305eb76d2c4b46463340b915b4ad3934b0c8c33ed0c6c887f57'
SCHEMA = 'axxium-at-auth-production/v1'
OPERATION = SOURCE + '-at-auth'
INCUMBENT = release.STATE / 'operations' / release.SOURCE / 'candidate.compose.json'
ORIGIN = 'https://axxium.promethean.rest'
AUTHORITY = release.STATE / 'oidc/authority.json'
KEY_TARGET = '/run/axxium/actor-signing.pem'
WORK_HANDLE = 'work-pilot-inbox.axxium.promethean.rest'
START = '/api/auth/atproto/start?identity=' + WORK_HANDLE
MAILBOX = '/api/mailbox/mcp'
HEX = re.compile(r'[0-9a-f]{64}')


def require(condition, reason):
    if not condition:
        raise release.Refused(reason)


def validate_plan(plan):
    require(isinstance(plan, dict) and set(plan) == {
        'schema', 'host', 'source_sha', 'image_id', 'source_pr', 'artifact_id', 'artifact_sha256',
        'stage_run_id', 'stage_workflow_path', 'stage_controller_sha', 'expected', 'authority', 'ingress',
    } and plan['schema'] == SCHEMA and plan['source_sha'] == SOURCE and plan['image_id'] == IMAGE,
            'Unreviewed AT sign-in plan')
    validate_custody({**plan, 'schema': 'axxium-minimal-production/v1'})
    require(plan['source_pr'] == 83 and plan['authority'] == {
        'issuer': release.ISSUER, 'work_handle': WORK_HANDLE, 'incumbent_sha256': plan['authority'].get('incumbent_sha256'),
    } and HEX.fullmatch(plan['authority']['incumbent_sha256']), 'Unexpected primary authority')
    expected = set(release_plan_keys())
    snapshot = plan['expected']
    require(set(snapshot) == expected and snapshot['app_image_id'] == release.IMAGE,
            'Incumbent snapshot differs')
    for key, value in snapshot.items():
        if key == 'caddy_proxy_ip':
            release.ipaddress.IPv4Address(value)
        else:
            pattern = r'sha256:[0-9a-f]{64}' if key.endswith('_image_id') else HEX
            require(re.fullmatch(pattern, value), 'Invalid incumbent pin')
    require(set(plan['ingress']) == {'incumbent', 'fenced'}, 'Incomplete Caddy snapshots')
    for phase in plan['ingress'].values():
        require(set(phase) == {'root_sha256', 'adapted_sha256'} and all(HEX.fullmatch(v) for v in phase.values()),
                'Invalid Caddy snapshot')
    require(plan['ingress']['incumbent']['root_sha256'] == snapshot['caddyfile_sha256']
            and plan['ingress']['incumbent']['root_sha256'] != plan['ingress']['fenced']['root_sha256'],
            'Caddy phase pins differ')
    return plan


def release_plan_keys():
    return ('app_container_id','app_image_id','app_environment_sha256','app_writable_layer_sha256',
            'db_container_id','db_image_id','db_environment_sha256','pds_container_id','pds_image_id',
            'pds_environment_sha256','caddy_container_id','caddy_image_id','compose_sha256',
            'environment_file_sha256','caddyfile_sha256','managed_caddy_sha256','caddy_proxy_ip',
            'incumbent_overlay_sha256')


def compose_overlay(signing_key):
    return {'services': {'axxium': {'image': IMAGE,
            'environment': {'JWT_AUDIENCE': 'promethean+production', 'AXXIUM_SIGNING_KEY_FILE': KEY_TARGET},
            'volumes': [{'type': 'bind', 'source': str(signing_key), 'target': KEY_TARGET,
                         'read_only': True, 'bind': {'create_host_path': False}}]}}}


def verify_projection(incumbent, selected, signing_key):
    old, new = (document['services']['axxium'] for document in (incumbent, selected))
    for document in (incumbent, selected):
        document['services'].pop('axxium')
    require(incumbent == selected and old['image'] == release.IMAGE and new['image'] == IMAGE,
            'Unintended Compose image or dependency change')
    before, after = (service['environment'] for service in (old, new))
    require(before.get('JWT_AUDIENCE') == 'promethean' and 'AXXIUM_SIGNING_KEY_FILE' not in before
            and not any(k.startswith('AXXIUM_MAILBOX_') for k in after)
            and after == {**before, 'JWT_AUDIENCE': 'promethean+production', 'AXXIUM_SIGNING_KEY_FILE': KEY_TARGET},
            'Unintended Compose environment change')
    mounts = [volume for volume in new['volumes'] if volume['target'] == KEY_TARGET]
    require(mounts == compose_overlay(signing_key)['services']['axxium']['volumes']
            and [volume for volume in new['volumes'] if volume['target'] != KEY_TARGET] == old['volumes'],
            'Unintended Compose bind change')
    require({k: v for k, v in old.items() if k not in {'image', 'environment', 'volumes'}}
            == {k: v for k, v in new.items() if k not in {'image', 'environment', 'volumes'}},
            'Unintended Compose app setting change')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_at_redirect(status, location):
    target = urllib.parse.urlsplit(location or '')
    query = urllib.parse.parse_qs(target.query)
    if (status not in (302, 303) or (target.scheme, target.netloc, target.path, target.fragment)
            != ('https', 'axxium.promethean.rest', '/oauth/authorize', '')
            or query.get('client_id') != [ORIGIN + '/api/auth/atproto/client-metadata.json']
            or len(query.get('request_uri', [])) != 1 or not query['request_uri'][0]):
        raise release.KnownFailure('Primary AT sign-in did not reach the canonical PDS authorization endpoint')


class Host(release.NativeHost):
    release_source = SOURCE
    release_image = IMAGE
    changed_environment = release.NativeHost.changed_environment | {'JWT_AUDIENCE'}

    def __init__(self, plan_hash):
        super().__init__(plan_hash)
        self.signing_key = release.STATE / 'operations' / OPERATION / 'actor-signing.pem'
        self.key_hash = None
        self.overlay_hash = None
        self.caddy_snapshot = None
        self.at_verified = False

    def operation_name(self, plan):
        return OPERATION

    def unchanged_dependencies(self, plan):
        super().unchanged_dependencies(plan)
        release.controlled_file(INCUMBENT, secret=True)
        require(release.file_digest(INCUMBENT) == plan['expected']['incumbent_overlay_sha256'],
                'Retained authority Compose layer changed')
        require(release.digest(release.private_read(AUTHORITY)) == plan['authority']['incumbent_sha256'],
                'Incumbent authority changed')

    def compose_args(self, overlay=None):
        runtime = release.RUNTIME
        args = ['docker','compose','--project-name','axxium','--project-directory',str(runtime),
                '--env-file',str(runtime / '.env'),'-f',str(runtime / 'compose.yaml'),'-f',str(INCUMBENT)]
        return args if overlay is None else args + ['-f',str(overlay)]

    @staticmethod
    def compose_env():
        return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(Path.home())}

    def compose(self, *args, overlay=None):
        return self.command(self.compose_args(overlay)+list(args), timeout=660, effect=True, env=self.compose_env())

    def identity_preflight(self):
        options = ['config','--format','json']
        old = self.command(self.compose_args()+options, env=self.compose_env())
        new = self.command(self.compose_args('-')+options, data=json.dumps(compose_overlay(self.signing_key)).encode(),
                           env=self.compose_env())
        verify_projection(strict_json(old), strict_json(new), self.signing_key)

    def provision_identity(self, plan):
        self.unchanged_dependencies(plan)
        require(self.operation == self.signing_key.parent and 'before' in self.backups,
                'Signing key preparation requires the locked verified backup')
        key = self.command(['/usr/bin/openssl','genpkey','-algorithm','ED25519'])
        self.private_write(self.signing_key, key)
        self.key_hash = release.digest(key)
        self.identity_check()

    def identity_check(self):
        key = release.private_read(self.signing_key)
        if self.key_hash is None or release.digest(key) != self.key_hash:
            raise release.Uncertain('Prepared actor signing key differs')
        public_der = self.command(['/usr/bin/openssl','pkey','-pubout','-outform','DER'], data=key)
        if not public_der.startswith(bytes.fromhex('302a300506032b6570032100')) or len(public_der) != 44:
            raise release.Uncertain('Prepared actor signing key is not Ed25519')

    def route_candidates(self, plan):
        observed = caddy.readback(self, plan['expected']['caddy_container_id'], release.CADDY, release.MANAGED)
        old = plan['ingress']['incumbent']
        require(observed['root_sha256'] == old['root_sha256']
                and observed['active_sha256'] == observed['startup_sha256'] == old['adapted_sha256']
                and caddy.ROUTE_NAME not in observed['managed'], 'Live shared ingress differs')
        self.caddy_snapshot = observed
        fence = _fence(self.original)
        require(release.digest(fence) == plan['ingress']['fenced']['root_sha256'], 'Existing release fence differs')
        return fence, self.original

    def replace_caddy(self, plan, data):
        self.unchanged_dependencies(plan)
        selected = plan['ingress']['incumbent' if data == self.original else 'fenced']
        require(release.digest(data) == selected['root_sha256'], 'Unexpected Caddy write')
        self.caddy_snapshot = caddy.publish(self, plan['expected']['caddy_container_id'], release.CADDY, release.MANAGED,
                root_bytes=data, route_name=caddy.ROUTE_NAME, route_bytes=None, expected_before=self.caddy_snapshot,
                expected_adapted_sha256=selected['adapted_sha256'])

    def probe_profile(self, plan, image, name, values, *, candidate):
        values, mounts = super().probe_profile(plan, image, name, values, candidate=candidate)
        if candidate:
            self.identity_check()
            values.update(JWT_AUDIENCE='promethean+production', AXXIUM_SIGNING_KEY_FILE=KEY_TARGET)
            mounts += ['--mount', f'type=bind,src={self.signing_key},dst={KEY_TARGET},readonly']
        return values, mounts

    def start_candidate(self, plan):
        self.unchanged_dependencies(plan)
        self.identity_check()
        if self.candidate_id is None:
            self.original_layer(plan)
        path = self.operation / 'candidate.compose.json'
        raw = json.dumps(compose_overlay(self.signing_key), sort_keys=True).encode()
        if path.exists():
            require(release.private_read(path) == raw, 'Retained app-only Compose layer differs')
        else:
            self.private_write(path, raw)
        self.overlay_hash = release.digest(raw)
        self.compose('up','-d','--no-deps','--pull','never','axxium',overlay=path)
        current = self.inspect(release.APP)
        if current['Image'] != IMAGE:
            raise release.Uncertain('Runtime app-only image differs')
        self.candidate_id = current['Id']

    def verify_runtime(self, current, *, candidate):
        mounts = {row['Destination']: (row['Type'], row['Source'], row['RW']) for row in current['Mounts']}
        environment = dict(entry.split('=',1) for entry in current['Config']['Env'])
        expected_key = ('bind', str(self.signing_key), False) if candidate else None
        if (mounts.get(KEY_TARGET) != expected_key or
                mounts.get('/run/axxium/oidc') != ('bind', str(AUTHORITY.parent), False) or
                environment.get('AXXIUM_SIGNING_KEY_FILE') != (KEY_TARGET if candidate else None) or
                environment.get('JWT_AUDIENCE') != ('promethean+production' if candidate else 'promethean') or
                any(key.startswith('AXXIUM_MAILBOX_') for key in environment)):
            raise release.Uncertain('Runtime authority or app-only settings differ')

    def private_health(self, plan):
        super().private_health(plan)
        self.verify_runtime(self.inspect(release.APP), candidate=True)

    def verify_preserved_routes(self):
        for path in (MAILBOX, '/.well-known/oauth-protected-resource'+MAILBOX):
            if self.http(path)[0] != 404:
                raise release.KnownFailure('Mailbox route changed in the app-only phase')
        status, raw = self.http('/xrpc/com.atproto.server.describeServer')
        if status != 200 or strict_json(raw).get('did') != 'did:web:axxium.promethean.rest':
            raise release.KnownFailure('Preserved PDS authority differs')

    def public_health(self, plan):
        super().public_health(plan)
        self.verify_preserved_routes()
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            try:
                response = opener.open(ORIGIN + START, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                verify_at_redirect(response.status, response.headers.get('Location'))
        except (OSError, ValueError) as error:
            raise release.KnownFailure('Primary AT sign-in probe failed') from error
        self.at_verified = True

    def commit(self, plan):
        self.unchanged_dependencies(plan)
        self.identity_check()
        current = self.inspect(release.APP)
        if (current['Id'] != self.candidate_id or current['Image'] != IMAGE
                or current['State'].get('Health', {}).get('Status') != 'healthy' or not self.at_verified
                or caddy.readback(self, plan['expected']['caddy_container_id'], release.CADDY, release.MANAGED)
                != self.caddy_snapshot or self.caddy_snapshot['root_sha256'] != plan['expected']['caddyfile_sha256']):
            raise release.Uncertain('Terminal app-only state differs')
        self.verify_runtime(current, candidate=True)
        self.private_write(self.operation / 'deployment.json', json.dumps(self.receipt('healthy'), sort_keys=True).encode())

    def verify_rollback(self, plan, current):
        self.verify_runtime(current, candidate=False)
        super().public_health(plan)
        self.verify_preserved_routes()

    def receipt(self, status):
        return {**super().receipt(status), 'schema': 'axxium-at-auth-deployment/v1',
                'issuer': release.ISSUER, 'at_sign_in_start_verified': status == 'healthy' and self.at_verified,
                'actor_signing_sha256': self.key_hash if status == 'healthy' else None,
                'retained_overlay_sha256': self.overlay_hash if status == 'healthy' else None,
                'mailbox_route_state': 'closed', 'mailbox_messages_sent': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--plan-sha256', required=True)
    try:
        args = parser.parse_args()
        raw = release.private_read(args.plan)
        require(release.digest(raw) == args.plan_sha256, 'App-only plan digest differs')
        plan = validate_plan(strict_json(raw))
        result = release.execute_admitted(plan, Host(args.plan_sha256))
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'healthy' else 3
    except (release.Refused, Refusal, OSError, ValueError, TypeError, KeyError):
        print('App-only AT release refused. Use the protected operation journal for any recovery.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
