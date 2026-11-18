"""Rehearse the same-image Events restart in the disposable active fixture."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import active_upgrade_fixture as base
from axxium_events_census import PinnedCensus
import axxium_events_profile as profile
import axxium_events_release as events
from axxium_production_qualification import strict_json

native = base.native
active = base.active
CENSUS_PLAN = Path('digitalocean/services/axxium/mailbox-events-production-plan.json')


class Fixture(base.Fixture):
    """Check the role selected by the transaction against real container state."""

    def __init__(self, image):
        super().__init__({'incumbent': image, 'candidate': image},
                         probe_operation=active.SOURCE + '-mailbox-events')
        self.roles = {native.APP: ('incumbent', None)}

    def runtime_role(self, container, record):
        expected = self.roles.get(container)
        base.require(expected is not None, 'Fixture runtime has no expected role')
        role, expected_id = expected
        base.require(record['Image'] == self.images['incumbent'] and record['State']['Running'] is True
                     and (expected_id is None or record['Id'] == expected_id),
                     'Same-image fixture runtime identity differs')
        environment = native.container_environment(record)
        live_events = container == native.APP and role == 'candidate'
        base.require(environment.get(profile.STREAM) == 'true'
                     and environment.get(profile.RECIPIENT) == base.DID
                     and environment.get(profile.SUBSCRIPTIONS) == ('true' if live_events else 'false')
                     and (environment.get(profile.COEXISTENCE) == 'true' if live_events
                          else profile.COEXISTENCE not in environment),
                     'Observed fixture runtime has the wrong Events profile')
        return role


def load_census(directory):
    """Pin the fixture projector to the exact digests in the retained production plan."""
    pins = strict_json((active.ROOT / CENSUS_PLAN).read_bytes())['census']
    module_sha256 = pins['module_sha256']
    census = PinnedCensus(directory / 'probe.js', directory / 'probe.mjs',
                          module_sha256=module_sha256, driver_sha256=pins['driver_sha256'])
    return census, module_sha256


class FixtureCensus:
    """Project the preserved fixture mailbox for each held candidate start."""

    def __init__(self, projector, baseline):
        self.projector = projector
        self.baseline = baseline
        self.reads = 0

    def __call__(self, ledger_bytes, recipient_did):
        base.require(recipient_did == base.DID and self.baseline
                     and isinstance(ledger_bytes, bytes) and ledger_bytes.startswith(self.baseline),
                     'Callback census requires the retained fixture mailbox ledger')
        counts = self.projector(ledger_bytes, recipient_did)
        self.reads += 1
        return counts


class Host(events.EventsHost):
    """Exercise the native host with isolated wire observations and named faults."""

    def __init__(self, plan, fixture, scenario, census):
        super().__init__(plan, native.digest(base.encode(plan)), census)
        self.fixture = fixture
        self.scenario = scenario
        self.injected = False
        self.started = []

    def http(self, path):
        return base.DIAG.call('ingress-http', base.wire.container_http, native.PROXY, path)

    def probe_profile(self, plan, image, name, values, *, candidate):
        result = super().probe_profile(plan, image, name, values, candidate=candidate)
        self.fixture.roles[name] = ('candidate' if candidate else 'incumbent', None)
        return result

    def command(self, args, **kwargs):
        if (len(args) == 5 and args[:4] == ['docker', 'stop', '--time', '610']
                and args[4].startswith('axxium-verify-') and args[4] in self.fixture.roles):
            self.fixture.verify(args[4], self)
        return super().command(args, **kwargs)

    def record(self, name, status):
        super().record(name, status)
        base.DIAG.native_phase = name if name in base.NATIVE_PHASES else 'none'

    def start_candidate(self, plan):
        super().start_candidate(plan)
        self.fixture.roles[native.APP] = ('candidate', self.candidate_id)
        selected = self.fixture.owned(native.APP)
        self.fixture.runtime_role(native.APP, selected)
        self.started.append(selected['Id'])
        if self.scenario == 'uncertain':
            self.injected = True
            raise base.ExpectedUncertainty('Synthetic uncertainty after the same-image Events start')

    def private_health(self, plan):
        super().private_health(plan)
        self.fixture.verify(native.APP, self)
        if self.scenario == 'known-failure' and not self.injected:
            self.fixture.tool(native.APP, 'acknowledge_message', {'message_id': self.fixture.b}, self)
            self.fixture.acked_b = True
            self.fixture.verify(native.APP, self)
            self.fixture.failure_ledgers = active.ledger_bytes(active.LEDGERS)
            self.injected = True
            raise base.ExpectedKnownFailure('Synthetic failure after the on-Events candidate ACK')

    def verify_rollback(self, plan, current):
        base.require(current['Id'] not in self.started, 'Rollback retained the Events container identity')
        self.fixture.roles[native.APP] = ('incumbent', current['Id'])
        self.fixture.runtime_role(native.APP, current)
        super().verify_rollback(plan, current)

    def public_health(self, plan):
        super().public_health(plan)
        self.fixture.verify(native.APP, self)


def project_plan(fixture):
    predecessor, differences = base.project_plan(fixture, bind_active_upgrade=False)
    base.require(predecessor['image_id'] == predecessor['expected']['app_image_id']
                 == predecessor['incumbent']['image_id'], 'Events fixture requires the same retained image')
    base.bind_plan(predecessor)
    plan = deepcopy(predecessor)
    plan.update(schema='axxium-mailbox-events-fixture/v1', recipient_did=base.DID)
    prior = plan['incumbent']
    prior.update(source_sha=plan['source_sha'], plan_sha256=native.digest(active.PLAN_BYTES))
    receipt = base.encode({'schema': 'axxium-active-mailbox-deployment/v1', 'status': 'healthy',
                           'source_sha': prior['source_sha'], 'image_id': prior['image_id'],
                           'app_container_id': prior['app_container_id'], 'plan_sha256': prior['plan_sha256'],
                           'webhook_enrollment': 'held'})
    path = native.STATE / 'operations' / (plan['source_sha'] + '-active-mailbox') / 'deployment.json'
    base.private(path, receipt)
    prior['receipt_sha256'] = native.digest(receipt)
    return plan, differences


def run_scenario(plan, fixture, scenario, census):
    host = Host(plan, fixture, scenario, census)
    if scenario == 'uncertain':
        try:
            base.DIAG.call('native-transaction', events.execute_admitted, plan, host)
        except native.Uncertain:
            base.require(host.injected and native.PENDING.is_file(), 'Events uncertainty did not retain the hold')
        else:
            raise RuntimeError('Events uncertainty was not observed')
        base.verify_held(plan, host, Host(plan, fixture, 'healthy', census), events.execute_admitted)
        final = fixture.owned(native.APP)
        base.require(fixture.runtime_role(native.APP, final) == 'candidate', 'Held Events runtime differs')
        return host, 'held'

    result = base.DIAG.call('native-transaction', events.execute_admitted, plan, host)
    status = result['status']
    healthy = scenario == 'healthy'
    base.require(status == ('healthy' if healthy else 'rolled_back') and not native.PENDING.exists(),
                 'Native Events terminal result differs')
    observed = fixture.owned(native.APP)
    role = fixture.runtime_role(native.APP, observed)
    base.require(role == ('candidate' if healthy else 'incumbent') and result['app_container_id'] == observed['Id'],
                 'Native Events receipt does not match the observed terminal application')
    fixture.verify(native.APP)
    if not healthy:
        base.require(host.injected and fixture.acked_b and host.rollback_id not in host.started,
                     'Events rollback did not preserve the known failure boundary')
        active.preserve_ledgers(fixture.failure_ledgers, active.ledger_bytes(active.LEDGERS))
    return host, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--census-dir', type=Path, required=True)
    parser.add_argument('--scenario', choices=('healthy', 'known-failure', 'uncertain'), required=True)
    with base.DIAG.phase('arguments'):
        args = parser.parse_args()
    base.require_hosted_fixture()
    with base.DIAG.phase('archive-custody'):
        binding = base.verify_role('candidate', args.archive)
        projector, census_module_sha256 = load_census(args.census_dir)
    with base.DIAG.phase('image-resolution'):
        image = base.runtime_id('candidate', base.wire.image_id('promethean-axxium:' + binding['source_sha']))
    fixture = Fixture(image)
    base.setup_fixture(fixture)
    plan, differences = base.DIAG.call('plan-projection', project_plan, fixture)
    fixture.roles[native.APP] = ('incumbent', plan['expected']['app_container_id'])
    base.private(base.ROOT / 'state/events-restart-fixture-projection.json', base.encode({
        'scope': 'synthetic fixture only', 'differences': differences,
    }))
    paths = [active.SIGNING, *(Path(row['path']) for row in plan['retained_config'])]
    material = {path: native.file_digest(path) for path in paths}
    census = FixtureCensus(projector, fixture.initial_ledgers['mailbox'])
    host, status = run_scenario(plan, fixture, args.scenario, census)

    with base.DIAG.phase('terminal-verification'):
        active.preserve_ledgers(fixture.initial_ledgers, active.ledger_bytes(active.LEDGERS))
        base.require(fixture.checks['clone_candidate'] > 0 and fixture.checks['clone_incumbent'] > 0,
                     'Both isolated parser roles must run')
        base.require(fixture.identity_hash() == fixture.database_hash
                     and material == {path: native.file_digest(path) for path in paths},
                     'Retained identity or configuration changed')
        expected_starts = 2 if status == 'healthy' else 1
        base.require(census.reads == len(host.started) == expected_starts
                     and plan['expected']['app_container_id'] not in host.started,
                     'Expected actual starts and held callback census checks differ')
    print(json.dumps({'status': status, 'scenario': args.scenario, 'source_sha': plan['source_sha'],
                      'image_id': image, 'archive_binding': binding, 'native_parser_checks': fixture.checks,
                      'held_callback_census_checks': census.reads, 'census_module_sha256': census_module_sha256,
                      'same_image_restart_verified': True, 'candidate_acknowledgement': fixture.acked_b,
                      'actual_candidate_starts': len(host.started), 'rollback_verified': status == 'rolled_back',
                      'provider_login_verified': False, 'webhook_enrollment': 'not-exercised',
                      'production_qualified': False}, sort_keys=True))


if __name__ == '__main__':
    raise SystemExit(base.run(main))
