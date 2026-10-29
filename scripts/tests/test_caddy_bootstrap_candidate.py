import copy
import tempfile
from pathlib import Path
import unittest

from caddy_bootstrap_candidate import bootstrap_under_lease, production_plan
from identity_ingress_transaction import IngressTransactionError


class Lease:
    def recheck(self):
        pass


class Driver:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError('injected failure')
        return True

    def __getattr__(self, name):
        return lambda _plan: self.call(name)


def container():
    mounts = [('/etc/caddy/Caddyfile', '/srv/open-hax/services/caddy/Caddyfile', False),
              ('/etc/caddy/axxium-identity/did.json', '/srv/open-hax/services/caddy/axxium-did.json', False),
              ('/etc/caddy/managed', '/srv/open-hax/state/caddy/managed', False),
              ('/data', '/srv/open-hax/state/caddy/data', True),
              ('/config', '/srv/open-hax/state/caddy/config', True)]
    return {'Id': 'a'*64, 'Image': 'sha256:' + 'b'*64,
            'Config': {'User': '', 'Labels': {'com.docker.compose.project': 'caddy', 'com.docker.compose.service': 'caddy'}},
            'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '', 'HostPort': '80'}],
                                            '443/tcp': [{'HostIp': '', 'HostPort': '443'}]}},
            'NetworkSettings': {'Networks': {'open-hax': {'IPAddress': '172.18.0.9'},
                                                'caddy-dev-ingress': {'IPAddress': '172.31.255.2'}}},
            'Mounts': [{'Destination': d, 'Source': s, 'RW': rw} for d, s, rw in mounts]}


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = production_plan(container())

    def run_bootstrap(self, driver, journal='j'):
        path = self.root / journal
        path.mkdir(mode=0o700)
        return bootstrap_under_lease(Lease(), path, driver, self.plan)

    def test_topology_refuses_unknown_ports_mounts_networks_and_owners(self):
        for mutation in (
            lambda c: c['HostConfig']['PortBindings'].update({'444/tcp': []}),
            lambda c: c['Mounts'].append({'Destination': '/extra', 'Source': '/x', 'RW': True}),
            lambda c: c['NetworkSettings']['Networks'].update({'other': {'IPAddress': '10.1.1.2'}}),
            lambda c: c['Config']['Labels'].update({'com.docker.compose.project': 'other'}),
        ):
            value = copy.deepcopy(container())
            mutation(value)
            with self.assertRaises(IngressTransactionError):
                production_plan(value)

    def test_adoption_must_be_proven_before_stopping(self):
        driver = Driver()
        driver.adoption_preflight = lambda _plan: False
        with self.assertRaisesRegex(IngressTransactionError, 'handoff is not qualified'):
            self.run_bootstrap(driver)
        self.assertNotIn('stop_incumbent', driver.calls)
        self.assertEqual(list((self.root / 'j').iterdir()), [])

    def test_success_checks_adoption_and_retains_journal(self):
        driver = Driver()
        self.assertEqual(self.run_bootstrap(driver), 'adopted-and-verified')
        self.assertLess(driver.calls.index('snapshot_quiescent_and_verify'), driver.calls.index('release_incumbent_endpoints'))
        self.assertLess(driver.calls.index('verify_candidate_tls_routes_peer'), driver.calls.index('adopt_without_removing_incumbent'))
        self.assertIn('verify_adoption_and_retained_incumbent', driver.calls)
        self.assertTrue((self.root / 'j/09-adopted-and-verified.json').exists())

    def test_each_effect_failure_runs_verified_incumbent_rollback(self):
        names = ['begin_owner_transition', 'stop_incumbent', 'snapshot_quiescent_and_verify', 'release_incumbent_endpoints',
                 'start_nonroot_candidate', 'verify_candidate_tls_routes_peer',
                 'adopt_without_removing_incumbent', 'verify_adoption_and_retained_incumbent', 'commit_owner_selection']
        for i, name in enumerate(names):
            driver = Driver(fail=name)
            with self.assertRaisesRegex(IngressTransactionError, 'incumbent restored'):
                self.run_bootstrap(driver, str(i))
            self.assertEqual(driver.calls[-3:], ['rollback_to_same_incumbent', 'verify_incumbent_tls_routes_and_identity', 'restore_owner_selection'])
            self.assertTrue((self.root / str(i) / '11-rolled-back-and-verified.json').exists())

    def test_rollback_failure_leaves_pending_for_manual_recovery(self):
        driver = Driver(fail='start_nonroot_candidate')
        driver.rollback_to_same_incumbent = lambda _plan: (_ for _ in ()).throw(RuntimeError('failure'))
        with self.assertRaisesRegex(IngressTransactionError, 'manual recovery'):
            self.run_bootstrap(driver)
        self.assertTrue((self.root / 'j/00-pending.json').exists())
        self.assertFalse((self.root / 'j/11-rolled-back-and-verified.json').exists())

    def test_process_interruption_leaves_intent_for_explicit_recovery(self):
        driver = Driver()
        driver.release_incumbent_endpoints = lambda _plan: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_bootstrap(driver)
        self.assertTrue((self.root / 'j/04-release-intent.json').exists())
        self.assertFalse((self.root / 'j/11-rolled-back-and-verified.json').exists())

    def test_plan_binds_other_runtime_settings_without_revealing_them(self):
        value = container()
        value['Config']['Env'] = ['EXAMPLE_SECRET=fixture-value']
        one = production_plan(value)
        value['Config']['Env'] = ['EXAMPLE_SECRET=other-value']
        two = production_plan(value)
        self.assertNotEqual(one.inspected_fingerprint, two.inspected_fingerprint)
        self.assertNotIn('fixture-value', repr(one))


if __name__ == '__main__':
    unittest.main()
