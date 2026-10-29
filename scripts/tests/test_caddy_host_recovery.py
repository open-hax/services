import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import caddy_host_recovery as recovery
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
            'Config': {'User': '1000:1000', 'Labels': {'com.docker.compose.project': 'caddy-identity', 'com.docker.compose.service': 'caddy'}},
            'HostConfig': {'PortBindings': copy.deepcopy(original['HostConfig']['PortBindings'])},
            'Mounts': [{'Destination': d, 'Source': s, 'RW': rw} for d, s, rw in mounts],
            'NetworkSettings': {'Networks': {}}, 'State': {'Running': True}}
        self.calls = []

    def __call__(self, args, *, allow_missing=False):
        self.calls.append(args)
        if args[:2] == ['docker', 'inspect']:
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
        return SimpleNamespace(returncode=0, stdout=b'')


class HostRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.original = container()
        self.plan = production_plan(self.original)
        self.docker = FakeDocker(self.original, self.plan)
        self.driver = recovery.HostRecoveryDriver(Lease(), execute=self.docker, boundary=lambda: True)

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
        with self.assertRaisesRegex(IngressTransactionError, 'route verification is not qualified'):
            self.driver.verify_incumbent_tls_routes_and_identity(self.plan)

    def test_candidate_label_or_mount_drift_refuses_before_removal(self):
        self.docker.candidate['Config']['Labels']['com.docker.compose.project'] = 'other'
        with patch.object(recovery, '_read_record', return_value={'version': 1, 'phase': 'stable', 'project': 'caddy'}):
            with self.assertRaisesRegex(IngressTransactionError, 'ownership'):
                self.driver.rollback_to_same_incumbent(self.plan)
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))

    def test_incumbent_static_configuration_drift_refuses_before_removal(self):
        self.docker.original['Config']['Env'] = ['changed=fixture']
        with self.assertRaisesRegex(IngressTransactionError, 'configuration changed'):
            self.driver.rollback_to_same_incumbent(self.plan)
        self.assertFalse(any(x[:3] == ['docker', 'rm', '-f'] for x in self.docker.calls))


if __name__ == '__main__':
    unittest.main()
