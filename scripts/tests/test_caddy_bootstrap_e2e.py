"""Root-owned, synthetic-filesystem end-to-end test; Docker is always fake."""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import axxium_identity_candidate
import caddy_host_bootstrap as boot
import caddy_host_recovery as recovery
import ingress_owner as owner
from caddy_bootstrap_candidate import bootstrap_under_lease, production_plan, recover_under_lease
from identity_ingress_transaction import IngressTransactionError
from ingress_pending import refuse_pending
from scripts.tests.test_caddy_bootstrap_candidate import container
from scripts.tests.test_caddy_host_bootstrap import models


class Lease:
    def recheck(self):
        pass


class SyntheticDocker:
    def __init__(self, old, plan, original, candidate_config, before, after, fault):
        self.old, self.plan = old, plan
        self.original, self.candidate_config = original, candidate_config
        self.before, self.after = before, after
        self.candidate = None
        self.validation = None
        self.fault = fault
        self.calls = []

    def result(self, value=b'', code=0):
        return SimpleNamespace(returncode=code, stdout=value)

    def __call__(self, args, *, allow_missing=False, input_bytes=None):
        self.calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            name = args[2]
            if name == self.plan.incumbent_id:
                return self.result(json.dumps([self.old]).encode())
            if name == 'axxium-axxium-1':
                return self.result(json.dumps([{'Config': {'Env': ['AXXIUM_TRUST_PROXY=172.30.115.2']},
                    'NetworkSettings': {'Networks': {'axxium-identity-ingress': {'IPAddress': '172.30.115.3'}}}}]).encode())
            if name in (recovery.NEW, 'c'*64) and self.candidate is not None:
                return self.result(json.dumps([self.candidate]).encode())
            if name in (recovery.VALIDATION, 'd'*64) and self.validation is not None:
                return self.result(json.dumps([self.validation]).encode())
            return self.result(code=1)
        if args[:3] == ['docker', 'ps', '-aq']:
            return self.result()
        if args[:2] == ['docker', 'exec']:
            if 'cat' in args:
                return self.result(self.candidate_config if args[-1].endswith('/identity-ingress/Caddyfile') else self.original)
            if input_bytes is not None and input_bytes != self.candidate_config:
                raise AssertionError('prevalidation did not receive reviewed candidate')
            return self.result()
        if args[:2] == ['docker', 'stop']:
            self.old['State']['Running'] = False
            return self.result()
        if args[:3] == ['docker', 'network', 'disconnect']:
            self.old['NetworkSettings']['Networks'].pop(args[-2])
            return self.result()
        if args[:3] == ['docker', 'network', 'connect']:
            name = args[-2]
            self.old['NetworkSettings']['Networks'][name] = {
                'IPAddress': '172.31.255.2' if name == 'caddy-dev-ingress' else '172.18.0.30',
                'IPAMConfig': {'IPv4Address': '172.31.255.2'} if name == 'caddy-dev-ingress' else {},
                'Aliases': ['caddy', 'caddy-caddy-1']}
            return self.result()
        if args[:2] == ['docker', 'start']:
            ident = args[-1]
            if ident == 'd'*64:
                self.validation['State'] = {'Running': False, 'Status': 'exited', 'ExitCode': 0}
            elif ident == 'c'*64:
                self.candidate['State'] = {'Running': True, 'Status': 'running'}
            else:
                self.old['State']['Running'] = True
            return self.result()
        if args[:2] == ['docker', 'rm'] and args[-1] == 'd'*64:
            self.validation = None
            return self.result()
        if args[:3] == ['docker', 'rm', '-f']:
            if self.fault == 'rollback-fails':
                raise RuntimeError('injected rollback failure')
            if self.candidate is None or args[3] != self.candidate['Id']:
                raise AssertionError('attempted removal of unrelated container')
            self.candidate = None
            return self.result()
        if args[:2] == ['docker', 'compose']:
            if 'config' in args:
                if any(x.endswith('compose.validation.yaml') for x in args):
                    isolated = copy.deepcopy(self.after)
                    isolated['services']['caddy'].pop('networks')
                    isolated['services']['caddy'].update({'network_mode': 'none', 'ports': [], 'container_name': recovery.VALIDATION,
                        'entrypoint': ['caddy'], 'command': ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile'], 'restart': 'no'})
                    return self.result(json.dumps(isolated).encode())
                return self.result(json.dumps(self.after if 'caddy-identity' in args else self.before).encode())
            if 'create' in args:
                mounts = [{'Destination': v['target'], 'Source': v['source'], 'RW': not v['read_only']}
                          for v in self.after['services']['caddy']['volumes']]
                if 'caddy-validation' in args:
                    self.validation = {'Id': 'd'*64, 'Image': self.plan.incumbent_image,
                        'Config': {'User': '1000:1000', 'Labels': {'com.docker.compose.project': 'caddy-validation',
                                  'com.docker.compose.service': 'caddy'}, 'Entrypoint': ['caddy'],
                                  'Cmd': ['validate', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile'],
                                  'Env': ['FIXTURE=value']}, 'HostConfig': {'NetworkMode': 'none', 'PortBindings': {}},
                        'Mounts': mounts, 'State': {'Running': False, 'Status': 'created'}}
                    if self.fault == 'validator-drift':
                        self.validation['Config']['Cmd'] = ['unexpected']
                    return self.result()
                self.candidate = {'Id': 'c'*64, 'Image': self.plan.incumbent_image,
                    'Config': {'User': '1000:1000', 'Labels': {'com.docker.compose.project': 'caddy-identity',
                                                               'com.docker.compose.service': 'caddy'},
                               'Cmd': recovery.CANDIDATE_COMMAND, 'Entrypoint': None, 'Env': ['FIXTURE=value']},
                    'HostConfig': {'PortBindings': copy.deepcopy(self.old['HostConfig']['PortBindings'])},
                    'Mounts': mounts, 'State': {'Running': False, 'Status': 'created'}, 'NetworkSettings': {'Networks': {
                        'open-hax': {'IPAddress': '172.18.0.31', 'Aliases': ['caddy']},
                        'caddy-dev-ingress': {'IPAddress': '172.31.255.2'},
                        'axxium-identity-ingress': {'IPAddress': '172.30.115.2'}}}}
                if self.fault == 'candidate-drift':
                    self.candidate['Config']['Cmd'] = ['unexpected']
                    return self.result()
                if self.fault == 'interrupt':
                    raise KeyboardInterrupt()
                if self.fault:
                    raise RuntimeError('injected failure after candidate creation')
                return self.result()
        if args[0] == 'curl':
            url = args[-1]
            if url.endswith('/openid-configuration'):
                return self.result(json.dumps({'issuer': 'https://axxium.promethean.rest/oidc',
                    'jwks_uri': 'https://axxium.promethean.rest/oidc/jwks'}).encode())
            host = url.split('/')[2]
            if host in ('opencode-dev.promethean.rest', 'shadow-dev.promethean.rest'):
                return self.result(b'\n__INGRESS_META__000\t\t', 35)
            status = b'401' if host == 'knoxx-dev.promethean.rest' else b'200'
            body = b'{"service":"axxium","status":"ok","version":"fixture"}' if host == 'axxium.promethean.rest' else b'fixture route'
            return self.result(body + b'\n__INGRESS_META__' + status + b'\t\ttext/plain')
        raise AssertionError('unexpected synthetic command: ' + repr(args))


@unittest.skipUnless(os.geteuid() == 0, 'root-owned fixture semantics require a root test process')
class BootstrapE2E(unittest.TestCase):
    def exercise(self, fault):
        with tempfile.TemporaryDirectory(prefix='caddy-bootstrap-e2e-') as tmp, ExitStack() as stack:
            root = Path(tmp)
            runtime, state, stage, selector, journal, journals = [root / x for x in ('runtime', 'state', 'stage', 'selector', 'journal', 'journals')]
            for path in (runtime, state, stage, selector, journal, journals):
                path.mkdir(mode=0o700)
            os.chown(journals, 1000, 1000)
            for name in ('data', 'config', 'managed', 'identity-ingress', 'nonroot-adoption-candidates'):
                (state / name).mkdir(mode=0o700)
            for name in ('identity-ingress', 'nonroot-adoption-candidates'):
                os.chown(state / name, 1000, 1000)
            (state / 'data/fixture-key').write_bytes(b'fixture-key')
            (state / 'config/fixture-config').write_bytes(b'fixture-config')
            original = ('axxium.promethean.rest, *.axxium.promethean.rest {\n' + axxium_identity_candidate.OLD + '\n\t}\n}\n').encode()
            candidate_config = axxium_identity_candidate.candidate(original, __import__('hashlib').sha256(original).hexdigest())
            (runtime / 'Caddyfile').write_bytes(original)
            (stage / 'Caddyfile').write_bytes(candidate_config)
            (stage / 'compose.bootstrap.yaml').write_bytes(b'fixture')
            (stage / 'compose.validation.yaml').write_bytes(boot.VALIDATION_OVERLAY)
            (state / 'identity-ingress/Caddyfile').write_bytes(candidate_config)
            os.chown(state / 'identity-ingress/Caddyfile', 1000, 1000)
            (selector / 'owner.json').write_text('{"version":1,"phase":"stable","project":"caddy"}')
            (selector / 'owner.json').chmod(0o644)
            old = container()
            old['Config'].update({'Cmd': ['caddy','run','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'], 'Env': ['FIXTURE=value']})
            old['State'] = {'Running': True}
            for mount in old['Mounts']:
                mount['Source'] = mount['Source'].replace('/srv/open-hax/services/caddy', str(runtime)).replace('/srv/open-hax/state/caddy', str(state))
            plan = production_plan(old, runtime=runtime, state=state)
            before, after = models(plan, str(runtime), str(state))
            fake = SyntheticDocker(old, plan, original, candidate_config, before, after, fault)
            for module in (boot, recovery):
                stack.enter_context(patch.object(module, 'RUNTIME', runtime))
                stack.enter_context(patch.object(module, 'STATE', state))
            stack.enter_context(patch.object(boot, 'STAGED', stage))
            stack.enter_context(patch.object(recovery, 'DIRECTORY', selector))
            stack.enter_context(patch.object(recovery, 'GUARD', selector / 'bootstrap-pending'))
            original_guard_identity = recovery.guard_identity
            stack.enter_context(patch.object(recovery, 'guard_identity', lambda: original_guard_identity(selector/'bootstrap-pending')))
            stack.enter_context(patch.object(boot, 'read_selection', lambda: owner.read_selection(selector)))
            stack.enter_context(patch.object(recovery, 'read_selection', lambda: owner.read_selection(selector)))
            stack.enter_context(patch.object(boot, 'refuse_pending', lambda owner: refuse_pending(journals, owner, bootstrap_guard=selector/'bootstrap-pending')))
            stack.enter_context(patch.object(boot, 'mark_bootstrap_pending', lambda lease, ident: owner.mark_bootstrap_pending(lease, ident, selector)))
            stack.enter_context(patch.object(boot, 'begin_transition', lambda lease: owner.begin_transition(lease, selector)))
            stack.enter_context(patch.object(boot, 'settle_transition', lambda lease, target, verify: owner.settle_transition(lease, target, verify, selector)))
            stack.enter_context(patch.object(recovery, '_read_record', lambda: owner._read_record(selector)))
            stack.enter_context(patch.object(recovery, '_publish', lambda lease, _dir, expected, value, uid: owner._publish(lease, selector, expected, value, uid)))
            stack.enter_context(patch.object(recovery, 'settle_transition', lambda lease, target, verify: owner.settle_transition(lease, target, verify, selector)))
            stack.enter_context(patch.object(recovery, 'clear_bootstrap_pending', lambda lease, ident, target: owner.clear_bootstrap_pending(lease, ident, target, selector)))
            driver = boot.HostBootstrapDriver(Lease(), journal, execute=fake, boundary=lambda: True, qualification=lambda: True)
            if fault in ('validator-drift', 'candidate-drift'):
                with self.assertRaisesRegex(IngressTransactionError, 'manual recovery'):
                    bootstrap_under_lease(driver.lease, journal, driver, plan, owner=0)
                self.assertTrue(owner.bootstrap_guard_pending(selector / 'bootstrap-pending'))
                blocked = 'd'*64 if fault == 'validator-drift' else 'c'*64
                self.assertFalse(any(call[:2] == ['docker', 'start'] and call[-1] == blocked for call in fake.calls))
                return
            if fault == 'interrupt':
                with self.assertRaises(KeyboardInterrupt):
                    bootstrap_under_lease(driver.lease, journal, driver, plan, owner=0)
                self.assertTrue((selector / 'bootstrap-pending').exists())
                self.assertEqual(recover_under_lease(driver.lease, journal, driver, owner=0), 'recovered-and-verified')
                self.assertEqual(owner.read_selection(selector)[0], 'caddy')
                self.assertTrue(old['State']['Running'])
                self.assertIsNone(fake.candidate)
                self.assertTrue((journal / '13-recovered-and-verified.json').exists())
            elif fault == 'rollback-fails':
                with self.assertRaisesRegex(IngressTransactionError, 'manual recovery'):
                    bootstrap_under_lease(driver.lease, journal, driver, plan, owner=0)
                self.assertTrue((selector / 'bootstrap-pending').exists())
                self.assertEqual(owner._read_record(selector)['phase'], 'transition')
                self.assertIsNotNone(fake.candidate)
                self.assertTrue((journal / '10-rollback-intent.json').exists())
                return
            elif fault:
                with self.assertRaisesRegex(IngressTransactionError, 'incumbent restored'):
                    bootstrap_under_lease(driver.lease, journal, driver, plan, owner=0)
                self.assertEqual(owner.read_selection(selector)[0], 'caddy')
                self.assertTrue(old['State']['Running'])
                self.assertIsNone(fake.candidate)
                self.assertTrue((journal / '11-rolled-back-and-verified.json').exists())
            else:
                self.assertEqual(bootstrap_under_lease(driver.lease, journal, driver, plan, owner=0), 'adopted-and-verified')
                self.assertEqual(owner.read_selection(selector)[0], 'caddy-identity')
                self.assertFalse(old['State']['Running'])
                self.assertIsNotNone(fake.candidate)
                self.assertTrue((journal / '09-adopted-and-verified.json').exists())
            self.assertTrue((selector / 'bootstrap-pending').exists())
            self.assertFalse(owner.bootstrap_guard_pending(selector / 'bootstrap-pending'))
            self.assertTrue((journal / '30-validation-container-intent.json').exists())

    def test_validator_configuration_drift_never_executes(self):
        self.exercise('validator-drift')

    def test_candidate_configuration_drift_never_executes(self):
        self.exercise('candidate-drift')

    def test_complete_synthetic_adoption(self):
        self.exercise(False)

    def test_complete_synthetic_rollback_after_candidate_creation(self):
        self.exercise(True)

    def test_crash_recovery_after_candidate_creation(self):
        self.exercise('interrupt')

    def test_failed_rollback_keeps_guard_and_recovery_evidence(self):
        self.exercise('rollback-fails')


if __name__ == '__main__':
    unittest.main()
