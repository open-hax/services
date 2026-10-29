import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import caddy_host_recovery as recovery
from caddy_runtime_security import HOST_PROFILE, MASKED_REQUIRED, READONLY_REQUIRED, reviewed_runtime
from scripts.tests.test_caddy_host_bootstrap import models
from caddy_bootstrap_candidate import production_plan
from identity_ingress_transaction import IngressTransactionError
from scripts.tests.test_caddy_bootstrap_candidate import container


class Lease:
    def recheck(self):
        pass


class FakeDocker:
    def __init__(self, original, plan):
        self.original = copy.deepcopy(original)
        self.original['State'] = {'Running': False}
        self.original['NetworkSettings']['Networks'] = {}
        snapshot = '/srv/open-hax/state/caddy/nonroot-adoption-candidates/' + plan.incumbent_id
        mounts = [('/etc/caddy/identity-ingress', '/srv/open-hax/state/caddy/identity-ingress', False),
                  ('/etc/caddy/axxium-identity/did.json', '/srv/open-hax/services/caddy/axxium-did.json', False),
                  ('/etc/caddy/managed', '/srv/open-hax/state/caddy/managed', False),
                  ('/data', snapshot + '/data', True), ('/config', snapshot + '/config', True)]
        self.candidate = {'Id': 'c'*64, 'Image': plan.incumbent_image,
            'Config': {'User': '1000:1000', 'Labels': {'com.docker.compose.project': 'caddy-identity', 'com.docker.compose.service': 'caddy'},
                       'Cmd': recovery.CANDIDATE_COMMAND, 'Entrypoint': None, 'Env': []},
            'HostConfig': {**{k: v[0] for k, v in HOST_PROFILE.items()}, 'MaskedPaths': sorted(MASKED_REQUIRED), 'ReadonlyPaths': sorted(READONLY_REQUIRED), 'PortBindings': copy.deepcopy(original['HostConfig']['PortBindings'])},
            'Mounts': [{'Destination': d, 'Source': s, 'RW': rw} for d, s, rw in mounts],
            'NetworkSettings': {'Networks': {}}, 'State': {'Running': True}}
        self.candidate['Config']['Healthcheck'] = reviewed_runtime(models(plan)[1]['services']['caddy'])['healthcheck']
        self.candidate['HostConfig']['RestartPolicy'] = {'Name': 'unless-stopped', 'MaximumRetryCount': 0}
        self.calls = []
        self.validation = None

    def __call__(self, args, *, allow_missing=False):
        self.calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            if args[2] == 'axxium-axxium-1':
                x = {'Config': {'Env': ['AXXIUM_TRUST_PROXY=172.30.115.2']},
                     'NetworkSettings': {'Networks': {'axxium-identity-ingress': {'IPAddress': '172.30.115.3'}}}}
            elif args[2] == recovery.VALIDATION:
                if self.validation is None:
                    return SimpleNamespace(returncode=1, stdout=b'')
                x = self.validation
            else:
                x = self.original if args[2] == self.original['Id'] else self.candidate
            return SimpleNamespace(returncode=0, stdout=json.dumps([x]).encode())
        if args[:3] == ['docker', 'rm', '-f']:
            self.candidate = None
        if args[:3] == ['docker', 'network', 'connect']:
            name = args[-2]
            self.original['NetworkSettings']['Networks'][name] = {
                'IPAddress': '172.31.255.2' if name == 'caddy-dev-ingress' else '172.18.0.30',
                'IPAMConfig': {'IPv4Address': '172.31.255.2'} if name == 'caddy-dev-ingress' else {},
                'Aliases': ['caddy', 'caddy-caddy-1']}
        if args[:2] == ['docker', 'start']:
            self.original['State']['Running'] = True
        if args[0] == 'curl' and args[-1].endswith('/openid-configuration'):
            return SimpleNamespace(returncode=0, stdout=json.dumps({'issuer': 'https://axxium.promethean.rest/oidc',
                'jwks_uri': 'https://axxium.promethean.rest/oidc/jwks'}).encode())
        return SimpleNamespace(returncode=0, stdout=b'')


class HostRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.original = container()
        self.plan = production_plan(self.original)
        self.docker = FakeDocker(self.original, self.plan)
        self.driver = recovery.HostRecoveryDriver(Lease(), execute=self.docker, boundary=lambda: True)
        contract = {'command': recovery.CANDIDATE_COMMAND, 'entrypoint': None,
                    'environment_sha256': recovery.environment_digest([]), **reviewed_runtime(models(self.plan)[1]['services']['caddy'])}
        patcher = patch.object(self.driver, 'runtime_contract', return_value=contract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_production_boundary_is_unconditionally_closed(self):
        calls = []
        driver = recovery.HostRecoveryDriver(Lease(), execute=lambda *a, **kw: calls.append(a))
        with patch.object(recovery, 'guard_identity', return_value=self.plan.incumbent_id):
            self.assertFalse(driver.recovery_preflight(self.plan))
        self.assertEqual(calls, [])

    def test_rollback_removes_only_qualified_candidate_and_reuses_old_id(self):
        with patch.object(recovery, '_read_record', return_value={'version': 1, 'phase': 'stable', 'project': 'caddy'}):
            self.driver.rollback_to_same_incumbent(self.plan)
        self.assertIn(['docker', 'rm', '-f', 'c'*64], self.docker.calls)
        self.assertIn(['docker', 'start', self.plan.incumbent_id], self.docker.calls)
        connects = [x for x in self.docker.calls if x[:3] == ['docker', 'network', 'connect']]
        open_connect = next(x for x in connects if x[-2] == 'open-hax')
        dev_connect = next(x for x in connects if x[-2] == 'caddy-dev-ingress')
        self.assertNotIn('--ip', open_connect)
        self.assertEqual(dev_connect[dev_connect.index('--ip') + 1], '172.31.255.2')
        self.assertIn('caddy', open_connect)
        with patch.object(self.driver, 'verify_route_baseline') as routes, \
             patch.object(self.driver, 'baseline_caddy_sha', return_value=hashlib.sha256(b'').hexdigest()):
            self.driver.verify_incumbent_tls_routes_and_identity(self.plan)
        routes.assert_called_once()

    def test_adoption_checks_fixed_peers_trust_routes_and_oidc(self):
        self.docker.candidate['NetworkSettings']['Networks'] = {
            'open-hax': {'Aliases': ['caddy'], 'IPAddress': '172.18.0.25'},
            'caddy-dev-ingress': {'IPAddress': '172.31.255.2'},
            'axxium-identity-ingress': {'IPAddress': '172.30.115.2'}}
        with patch.object(self.driver, 'verify_route_baseline') as routes, \
             patch.object(self.driver, 'baseline_caddy_sha', return_value=hashlib.sha256(b'').hexdigest()):
            self.driver.verify_adoption_and_retained_incumbent(self.plan)
        routes.assert_called_once()
        self.assertTrue(any(x[0] == 'curl' and x[-1].endswith('/openid-configuration') for x in self.docker.calls))

    def test_adoption_rejects_untrusted_proxy_before_route_probe(self):
        self.docker.candidate['NetworkSettings']['Networks'] = {
            'open-hax': {'Aliases': ['caddy'], 'IPAddress': '172.18.0.25'},
            'caddy-dev-ingress': {'IPAddress': '172.31.255.2'},
            'axxium-identity-ingress': {'IPAddress': '172.30.115.2'}}
        original_inspect = self.driver.inspect
        def inspect(name, **kwargs):
            if name == 'axxium-axxium-1':
                return {'Config': {'Env': []}, 'NetworkSettings': {'Networks': {}}}
            return original_inspect(name, **kwargs)
        with patch.object(self.driver, 'inspect', side_effect=inspect), patch.object(self.driver, 'verify_route_baseline') as routes, \
             patch.object(self.driver, 'baseline_caddy_sha', return_value=hashlib.sha256(b'').hexdigest()):
            with self.assertRaisesRegex(IngressTransactionError, 'trust differs'):
                self.driver.verify_adoption_and_retained_incumbent(self.plan)
        routes.assert_not_called()

    def test_candidate_label_or_mount_drift_refuses_before_removal(self):
        self.docker.candidate['Config']['Labels']['com.docker.compose.project'] = 'other'
        with patch.object(recovery, '_read_record', return_value={'version': 1, 'phase': 'stable', 'project': 'caddy'}):
            with self.assertRaisesRegex(IngressTransactionError, 'ownership'):
                self.driver.rollback_to_same_incumbent(self.plan)
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))

    def test_candidate_command_entrypoint_or_environment_drift_refuses_before_removal(self):
        for key, value in (('Cmd', ['other']), ('Entrypoint', ['/bin/sh']), ('Env', ['EXTRA=unexpected'])):
            candidate = self.docker.candidate['Config']
            before = candidate[key]
            candidate[key] = value
            with self.assertRaisesRegex(IngressTransactionError, 'command, entrypoint or environment'):
                self.driver.checked_candidate(self.plan)
            candidate[key] = before
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))

    def test_candidate_and_validator_reject_security_drift_before_any_effect(self):
        validation = copy.deepcopy(self.docker.candidate)
        validation['Id'] = 'd'*64
        validation['Config']['Labels']['com.docker.compose.project'] = 'caddy-validation'
        validation['Config']['Entrypoint'] = ['caddy']
        validation['Config']['Cmd'] = ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']
        validation['HostConfig']['RestartPolicy'] = {'Name': 'no', 'MaximumRetryCount': 0}
        validation['HostConfig']['NetworkMode'] = 'none'
        validation['HostConfig']['PortBindings'] = {}
        self.docker.validation = validation
        mutations = [('Privileged', True), ('CapAdd', ['SYS_ADMIN']),
                     ('Devices', [{'PathOnHost': '/dev/example'}]), ('SecurityOpt', ['seccomp=unconfined']),
                     ('PidMode', 'host'), ('IpcMode', 'host'), ('UsernsMode', 'host'),
                     ('GroupAdd', ['0']), ('Runtime', 'unexpected'), ('AutoRemove', True),
                     ('PublishAllPorts', True), ('MaskedPaths', []), ('ReadonlyPaths', []), ('Privileged', 0)]
        for kind, obj, check in (('candidate', self.docker.candidate, self.driver.checked_candidate),
                                  ('validator', validation, self.driver.checked_validation)):
            for key, changed in mutations:
                with self.subTest(kind=kind, key=key, changed=changed):
                    before = obj['HostConfig'][key]
                    obj['HostConfig'][key] = changed
                    with self.assertRaisesRegex(IngressTransactionError, 'security setting differs'):
                        check(self.plan)
                    obj['HostConfig'][key] = before
            before = obj['HostConfig'].pop('Privileged')
            with self.assertRaisesRegex(IngressTransactionError, 'security setting differs'):
                check(self.plan)
            obj['HostConfig']['Privileged'] = before
        self.assertFalse(any(x[:2] == ['docker', 'start'] or x[:2] == ['docker', 'rm'] for x in self.docker.calls))

    def test_both_container_types_reject_health_and_restart_drift(self):
        validation = copy.deepcopy(self.docker.candidate)
        validation['Id'] = 'd'*64
        validation['Config']['Labels']['com.docker.compose.project'] = 'caddy-validation'
        validation['Config']['Entrypoint'] = ['caddy']
        validation['Config']['Cmd'] = ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']
        validation['HostConfig'].update({'NetworkMode': 'none', 'PortBindings': {},
                                         'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}})
        self.docker.validation = validation
        for obj, check in ((self.docker.candidate, self.driver.checked_candidate), (validation, self.driver.checked_validation)):
            old = copy.deepcopy(obj['Config']['Healthcheck'])
            for change in ({**old, 'Timeout': 1}, {**old, 'Retries': 3.0}, None):
                obj['Config']['Healthcheck'] = change
                with self.assertRaisesRegex(IngressTransactionError, 'healthcheck differs'):
                    check(self.plan)
            obj['Config']['Healthcheck'] = old
            restart = obj['HostConfig']['RestartPolicy']
            for change in ({'Name': 'always', 'MaximumRetryCount': 3}, None):
                obj['HostConfig']['RestartPolicy'] = change
                with self.assertRaisesRegex(IngressTransactionError, 'restart policy differs'):
                    check(self.plan)
            obj['HostConfig']['RestartPolicy'] = restart
        self.assertFalse(any(x[:2] == ['docker', 'start'] for x in self.docker.calls))

    def test_incumbent_static_configuration_drift_refuses_before_removal(self):
        self.docker.original['Config']['Env'] = ['changed=fixture']
        with self.assertRaisesRegex(IngressTransactionError, 'configuration changed'):
            self.driver.rollback_to_same_incumbent(self.plan)
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))

    def test_recovery_removes_only_verified_owned_validation_orphan(self):
        validation = copy.deepcopy(self.docker.candidate)
        validation['Id'] = 'd'*64
        validation['Config']['Labels']['com.docker.compose.project'] = 'caddy-validation'
        validation['Config']['Entrypoint'] = ['caddy']
        validation['Config']['Cmd'] = ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']
        validation['HostConfig'] = {**{k: v[0] for k, v in HOST_PROFILE.items()}, 'MaskedPaths': sorted(MASKED_REQUIRED), 'ReadonlyPaths': sorted(READONLY_REQUIRED), 'NetworkMode': 'none', 'PortBindings': {}, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}}
        self.docker.validation = validation
        with patch.object(recovery, '_read_record', return_value={'version': 1, 'phase': 'stable', 'project': 'caddy'}):
            self.driver.rollback_to_same_incumbent(self.plan)
        self.assertIn(['docker', 'rm', '-f', 'd'*64], self.docker.calls)
        self.assertIn(['docker', 'rm', '-f', 'c'*64], self.docker.calls)

    def test_unrelated_validation_name_cannot_be_removed(self):
        validation = copy.deepcopy(self.docker.candidate)
        validation['Id'] = 'd'*64
        validation['Config']['Labels']['com.docker.compose.project'] = 'caddy-validation'
        validation['Config']['Entrypoint'] = ['caddy']
        validation['Config']['Cmd'] = ['serve-unrelated']
        validation['HostConfig'] = {**{k: v[0] for k, v in HOST_PROFILE.items()}, 'MaskedPaths': sorted(MASKED_REQUIRED), 'ReadonlyPaths': sorted(READONLY_REQUIRED), 'NetworkMode': 'none', 'PortBindings': {}, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}}
        self.docker.validation = validation
        with self.assertRaisesRegex(IngressTransactionError, 'identity or isolation'):
            self.driver.rollback_to_same_incumbent(self.plan)
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))

    def test_validation_environment_drift_cannot_be_removed(self):
        validation = copy.deepcopy(self.docker.candidate)
        validation['Id'] = 'd'*64
        validation['Config']['Labels']['com.docker.compose.project'] = 'caddy-validation'
        validation['Config']['Entrypoint'] = ['caddy']
        validation['Config']['Cmd'] = ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']
        validation['Config']['Env'] = ['UNEXPECTED=value']
        validation['HostConfig'] = {**{k: v[0] for k, v in HOST_PROFILE.items()}, 'MaskedPaths': sorted(MASKED_REQUIRED), 'ReadonlyPaths': sorted(READONLY_REQUIRED), 'NetworkMode': 'none', 'PortBindings': {}, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}}
        self.docker.validation = validation
        with self.assertRaisesRegex(IngressTransactionError, 'environment differs'):
            self.driver.rollback_to_same_incumbent(self.plan)
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))

    def test_route_comparison_preserves_preexisting_tls_failure_and_rejects_new_failure(self):
        expected = {host: ('000' if host in ('opencode-dev.promethean.rest', 'shadow-dev.promethean.rest') else
                           '401' if host == 'knoxx-dev.promethean.rest' else '200') for host in recovery.ROUTES}
        with tempfile.TemporaryDirectory() as root:
            journal = Path(root)
            actual = dict(expected)
            bodies = {host: b'fixture body' for host in recovery.ROUTES}
            bodies['axxium.promethean.rest'] = b'{"service":"axxium","status":"ok","version":"old"}'
            def runner(args, **_kwargs):
                host = args[-1].split('/')[2]
                code = actual[host]
                body = b'' if code == '000' else bodies[host]
                return SimpleNamespace(returncode=35 if code == '000' else 0,
                    stdout=body + b'\n__INGRESS_META__' + code.encode() + b'\t\ttext/plain')
            driver = recovery.HostRecoveryDriver(Lease(), execute=runner, journal=journal, journal_owner=os.getuid())
            (journal / 'routes.json').write_text(json.dumps(driver.probe_routes()))
            (journal / 'routes.json').chmod(0o600)
            driver.verify_route_baseline()
            # A planned Axxium version change is not a route regression.
            bodies['axxium.promethean.rest'] = b'{"service":"axxium","status":"ok","version":"new"}'
            driver.verify_route_baseline()
            bodies['proxx.promethean.rest'] = b'unrelated upstream'
            with self.assertRaisesRegex(IngressTransactionError, 'differs'):
                driver.verify_route_baseline()
            bodies['proxx.promethean.rest'] = b'fixture body'
            actual['knoxx.promethean.rest'] = '000'
            with self.assertRaisesRegex(IngressTransactionError, 'differs'):
                driver.verify_route_baseline()


if __name__ == '__main__':
    unittest.main()
