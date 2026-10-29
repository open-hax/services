import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from caddy_bootstrap_candidate import production_plan
import caddy_host_bootstrap as bootstrap
from identity_ingress_transaction import IngressTransactionError
from scripts.tests.test_caddy_bootstrap_candidate import container
from caddy_state_candidate import copy_verified


class Lease:
    def recheck(self):
        pass


def models(plan, root='/srv/open-hax/services/caddy', state='/srv/open-hax/state/caddy'):
    snapshot = state + '/nonroot-adoption-candidates/' + plan.incumbent_id
    def vol(source, target, ro):
        return {'type': 'bind', 'source': source, 'target': target, 'read_only': ro}
    volumes = [vol(root+'/Caddyfile', '/etc/caddy/Caddyfile', True),
               vol(root+'/axxium-did.json', '/etc/caddy/axxium-identity/did.json', True),
               vol(state+'/managed', '/etc/caddy/managed', True),
               vol(state+'/data', '/data', False), vol(state+'/config', '/config', False)]
    service = {'image': 'caddy:2.8-alpine', 'environment': {'FIXTURE': 'value'}, 'restart': 'unless-stopped',
               'ports': [{'target': 80, 'published': '80', 'protocol': 'tcp'}, {'target': 443, 'published': '443', 'protocol': 'tcp'}],
               'volumes': volumes, 'networks': {'default': {'aliases': ['caddy']}, 'dev-ingress': {'ipv4_address': '172.31.255.2'}},
               'healthcheck': {'test': ['CMD', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], 'interval': '30s'}}
    before = {'services': {'caddy': service}}
    after = copy.deepcopy(before)
    new = after['services']['caddy']
    new['image'] = plan.incumbent_image
    new['user'] = '1000:1000'
    new['command'] = ['caddy', 'run', '--config', '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile']
    new['healthcheck']['test'][4] = '/etc/caddy/identity-ingress/Caddyfile'
    new['volumes'] = [v for v in new['volumes'] if v['target'] not in ('/data', '/config')]
    new['volumes'] += [vol(snapshot+'/data', '/data', False), vol(snapshot+'/config', '/config', False),
                       vol(state+'/identity-ingress', '/etc/caddy/identity-ingress', True)]
    new['networks']['axxium-identity-ingress'] = {'ipv4_address': '172.30.115.2'}
    after['networks'] = {'default': {'name': 'open-hax', 'external': True},
                         'dev-ingress': {'name': 'caddy-dev-ingress', 'external': True},
                         'axxium-identity-ingress': {'name': 'axxium-identity-ingress', 'external': True}}
    return before, after


class BootstrapDriverTest(unittest.TestCase):
    def setUp(self):
        self.plan = production_plan(container())
        self.before, self.after = models(self.plan)

    def test_default_production_gate_refuses_before_docker_or_file_access(self):
        calls = []
        with tempfile.TemporaryDirectory() as root:
            driver = bootstrap.HostBootstrapDriver(Lease(), Path(root), execute=lambda *a, **kw: calls.append(a))
            self.assertFalse(driver.preflight(self.plan))
        self.assertEqual(calls, [])

    def test_qualification_alone_refuses_even_if_writer_boundary_is_proven(self):
        calls = []
        with tempfile.TemporaryDirectory() as root:
            driver = bootstrap.HostBootstrapDriver(Lease(), Path(root), execute=lambda *a, **kw: calls.append(a), boundary=lambda: True)
            self.assertFalse(driver.preflight(self.plan))
        self.assertEqual(calls, [])
        self.assertFalse(bootstrap.deployment_qualified())

    def test_created_container_drift_or_preexecution_blocks_start(self):
        driver = bootstrap.HostBootstrapDriver(Lease(), Path('/unused'), boundary=lambda: True, qualification=lambda: True)
        for inspected in (None, {'Id': 'e'*64, 'State': {'Running': False, 'Status': 'created'}},
                          {'Id': 'c'*64, 'State': {'Running': True, 'Status': 'running'}},
                          {'Id': 'c'*64, 'State': {'Running': False, 'Status': 'exited'}}):
            with self.assertRaises(IngressTransactionError):
                driver.require_created_container({'Id': 'c'*64}, lambda *a, **kw: inspected, self.plan)
        with self.assertRaisesRegex(IngressTransactionError, 'identity is missing'):
            driver.require_created_container({'Id': 'short'}, lambda *a, **kw: None, self.plan)

    def test_reviewed_only_model_is_accepted(self):
        bootstrap.validate_candidate_models(self.before, self.after, self.plan)

    def test_unrelated_mutations_fail_closed(self):
        mutations = [
            lambda x: x['services']['caddy']['environment'].update({'FIXTURE': 'changed'}),
            lambda x: x['services']['caddy']['ports'].append({'target': 123, 'published': '123'}),
            lambda x: x['services']['caddy'].update({'user': '0:0'}),
            lambda x: x['services']['caddy']['volumes'][0].update({'source': '/unexpected'}),
            lambda x: x['services']['caddy']['networks']['dev-ingress'].update({'ipv4_address': '172.31.255.3'}),
            lambda x: x['networks']['dev-ingress'].update({'external': False}),
            lambda x: x['services']['caddy']['healthcheck'].update({'interval': '1s'}),
        ]
        for mutation in mutations:
            altered = copy.deepcopy(self.after)
            mutation(altered)
            with self.assertRaises(IngressTransactionError):
                bootstrap.validate_candidate_models(self.before, altered, self.plan)

    def test_unmodeled_compose_security_options_are_refused_even_if_legacy_matches(self):
        for key, value in (('privileged', True), ('cap_add', ['SYS_ADMIN']),
                           ('devices', ['/dev/example']), ('security_opt', ['seccomp=unconfined']),
                           ('pid', 'host'), ('read_only', True), ('arbitrary_future_option', True)):
            before, after = copy.deepcopy(self.before), copy.deepcopy(self.after)
            before['services']['caddy'][key] = value
            after['services']['caddy'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(IngressTransactionError, 'Unsupported Compose'):
                bootstrap.validate_candidate_models(before, after, self.plan)

    def test_snapshot_verification_detects_post_validation_changes(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, snapshot = root / 'source', root / 'snapshot'
            (source / 'data').mkdir(parents=True)
            (source / 'config').mkdir()
            (source / 'data/key').write_bytes(b'fixture secret')
            (source / 'config/state').write_bytes(b'fixture state')
            copy_verified(source, snapshot, uid=os.getuid(), gid=os.getgid())
            bootstrap.verify_quiescent_snapshot(source, snapshot, owner=os.getuid(), group=os.getgid())
            (snapshot / 'data/key').write_bytes(b'changed')
            with self.assertRaisesRegex(IngressTransactionError, 'Snapshot bytes'):
                bootstrap.verify_quiescent_snapshot(source, snapshot, owner=os.getuid(), group=os.getgid())

    def test_fixed_docker_stop_disconnect_snapshot_and_start_sequence(self):
        calls = []
        def runner(args, **_kwargs):
            calls.append(args)
        with tempfile.TemporaryDirectory() as root:
            driver = bootstrap.HostBootstrapDriver(Lease(), Path(root), execute=runner, boundary=lambda: True, qualification=lambda: True)
            old = container()
            old['State'] = {'Running': True}
            with patch.object(driver, 'checked_incumbent', return_value=old):
                driver.stop_incumbent(self.plan)
            old['State']['Running'] = False
            with patch.object(driver, 'checked_incumbent', return_value=old), \
                 patch.object(driver, 'adoption_preflight', return_value=True), \
                 patch.object(driver, 'checked_validation', side_effect=[None, {'Id': 'd'*64}, {'Id': 'd'*64, 'State': {'Running': False, 'Status': 'created'}}, {'Id': 'd'*64, 'State': {'Running': False, 'Status': 'exited', 'ExitCode': 0}}, None]), \
                 patch.object(bootstrap, 'copy_verified') as copied, \
                 patch.object(bootstrap, 'verify_quiescent_snapshot') as snapshot_checked:
                driver.snapshot_quiescent_and_verify(self.plan)
                driver.release_incumbent_endpoints(self.plan)
            copied.assert_called_once()
            snapshot_checked.assert_called_once()
            self.assertIn(['docker', 'stop', '--time', '30', self.plan.incumbent_id], calls)
            self.assertEqual(len([x for x in calls if x[:3] == ['docker', 'network', 'disconnect']]), 2)
            self.assertTrue(any(x[:3] == ['docker', 'compose', '--project-name'] and 'create' in x and
                                '/etc/open-hax/ingress/candidate/compose.validation.yaml' in x for x in calls))
            with patch.object(driver, 'adoption_preflight', return_value=True), \
                 patch.object(driver, 'baseline_caddy_sha', return_value=hashlib.sha256(b'fixture').hexdigest()), \
                 patch.object(driver, 'checked_candidate', side_effect=[{'Id': 'c'*64}, {'Id': 'c'*64, 'State': {'Running': False, 'Status': 'created'}}, {'Id': 'c'*64, 'State': {'Running': True}}]), \
                 patch.object(Path, 'read_bytes', return_value=b'fixture'):
                driver.start_nonroot_candidate(self.plan)
            self.assertTrue(any(x[:3] == ['docker', 'compose', '--project-name'] and 'create' in x and '--pull' in x for x in calls))


if __name__ == '__main__':
    unittest.main()
