"""Exercise the actual Events caller and admission with every host effect closed."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import axxium_events_admission as admission
import axxium_events_activate as activate
import axxium_events_census_archive as census_archive
from axxium_production_qualification import Refusal

WORKFLOW = '.github/workflows/deploy-axxium-events.yml'
NOW = datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)
HEAD, CONTROLLER = 'a' * 40, 'b' * 40
RUN = 12345
MODULES = {
    'axxium_active_mailbox_release', 'axxium_events_admission', 'axxium_events_activate',
    'axxium_events_profile', 'axxium_events_release', 'axxium_events_census',
    'axxium_unified_mailbox_admission', 'axxium_unified_mailbox_release', 'axxium_minimal_release',
    'axxium_ingress_host', 'axxium_ingress_transaction', 'axxium_interrupted_handoff',
    'axxium_ingress_caddy', 'axxium_oidc_ingress', 'axxium_production_qualification',
    'environment_policy', 'github_environment_admission',
}
ARCHIVE_FILES = {admission.PLAN_PATH, admission.active.PLAN_PATH,
                 'digitalocean/services/axxium/unified-mailbox-production-plan.json',
                 'census/probe.js', 'census/probe.mjs',
                 *('scripts/' + name + '.py' for name in MODULES)}


def workflow():
    return yaml.safe_load((ROOT / WORKFLOW).read_text())


def secret_step():
    return next(step for step in workflow()['jobs']['deploy']['steps']
                if 'SSH_PRIVATE_KEY' in step.get('env', {}))


def unpack_controller(directory):
    """Execute only the workflow's real tar command over a temporary source copy."""
    command = next(line.strip() for line in secret_step()['run'].replace('\\\n', ' ').splitlines()
                   if line.strip().startswith('tar --mode='))
    args = shlex.split(command)
    names = [admission.PLAN_PATH if name == '$PLAN' else name
             for name in args[args.index('-cf') + 2:]]
    source = Path(directory) / 'source'
    source.mkdir()
    for name in names:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith('census/'):
            target.write_bytes(b'synthetic archive import placeholder; never executed\n')
        else:
            shutil.copyfile(ROOT / name, target)
    subprocess.run(['bash', '-euo', 'pipefail', '-c', command], cwd=source,
                   env={'PATH': os.environ['PATH'], 'PLAN': admission.PLAN_PATH},
                   check=True, capture_output=True, timeout=10)
    unpacked = Path(directory) / 'unpacked'
    unpacked.mkdir()
    with tarfile.open(source / 'controller.tar') as package:
        members = package.getmembers()
        if {member.name for member in members} != ARCHIVE_FILES:
            raise AssertionError('Events tar does not contain the exact reviewed import closure')
        if any(not member.isfile() or member.mode != 0o600 for member in members):
            raise AssertionError('Controller archive requires private regular inputs')
        package.extractall(unpacked, filter='data')
    return unpacked


class PlanContinuityTests(unittest.TestCase):
    def test_exact_plan_retains_the_current_image_and_staged_only_predecessor_chain(self):
        plan = admission.validate_plan_bytes(admission.PLAN_BYTES)
        self.assertEqual(plan['source_sha'], admission.active.SOURCE)
        self.assertEqual(plan['image_id'], plan['expected']['app_image_id'])
        self.assertEqual(plan['image_id'], admission.active.IMAGE)
        self.assertEqual(plan['compose_chain'][:-1], admission.active.PLAN['compose_chain'])
        self.assertTrue(plan['compose_chain'][-1]['path'].endswith(
            plan['source_sha'] + '-active-mailbox/candidate.compose.json'))
        self.assertEqual(plan['incumbent']['plan_sha256'], sha256(admission.active.PLAN_BYTES).hexdigest())
        for key in ('identity', 'retained_config', 'ingress'):
            self.assertEqual(plan[key], admission.active.PLAN[key])
        self.assertEqual(plan['recipient_did'], plan['identity']['work_atproto_did'])

    def test_a_reviewed_file_cannot_admit_changed_predecessor_or_census_custody(self):
        original = json.loads(admission.PLAN_BYTES)
        paths = [
            (('schema',), 'unreviewed'), (('image_id',), 'sha256:' + 'f' * 64),
            (('source_sha',), 'f' * 40), (('expected', 'app_image_id'), 'sha256:' + 'f' * 64),
            (('recipient_did',), 'did:plc:bbbbbbbbbbbbbbbbbbbbbbbb'),
            (('incumbent', 'source_sha'), 'f' * 40), (('incumbent', 'plan_sha256'), '0' * 64),
            (('compose_chain', 0, 'sha256'), '0' * 64), (('retained_config', 0, 'sha256'), '0' * 64),
            (('identity', 'work_actor_id'), 'changed'), (('ingress', 'live', 'root_sha256'), '0' * 64),
            (('census', 'artifact_id'), True), (('census', 'build_head'), 'not-a-sha'),
            (('census', 'module_sha256'), 'not-a-digest'),
        ]
        for path, value in paths:
            plan = deepcopy(original)
            parent = plan
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = value
            raw = json.dumps(plan).encode()
            with self.subTest(path=path), mock.patch.object(admission, 'PLAN_BYTES', raw):
                with self.assertRaises(Refusal):
                    admission.validate_plan_bytes(raw)
        with self.assertRaises(Refusal):
            admission.validate_plan_bytes(admission.PLAN_BYTES + b'\n')

    def test_invalid_plan_stops_before_shared_archive_or_native_evidence(self):
        with mock.patch.object(admission, 'admit') as shared, \
                mock.patch.object(admission, 'api') as api, \
                mock.patch.object(admission, 'native_file') as files:
            with self.assertRaises(Refusal):
                admission.admit_events(b'{"schema":"unreviewed"}', CONTROLLER, Path('/unused.zip'), now=NOW)
        shared.assert_not_called()
        api.assert_not_called()
        files.assert_not_called()


class NativeRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.e2e = {'rehearsal_head': HEAD, 'rehearsal_run_id': RUN}
        self.run = {'id': RUN, 'head_sha': HEAD, 'repository': {'full_name': admission.SERVICES},
                    'path': admission.WORKFLOW, 'event': 'pull_request', 'run_attempt': 1,
                    'status': 'completed', 'conclusion': 'success', 'updated_at': '2026-11-18T18:00:00Z'}
        self.jobs = [{'name': 'same-image (' + scenario + ')', 'status': 'completed',
                      'conclusion': 'success', 'head_sha': HEAD, 'run_id': RUN}
                     for scenario in sorted(admission.SCENARIOS)]

    def verify(self, *, jobs=None, different=None):
        def file_bytes(repository, path, revision):
            return (('changed:' if path == different and revision == CONTROLLER else '') + path).encode()
        with mock.patch.object(admission, 'main_ancestor'), \
                mock.patch.object(admission, 'api', side_effect=[self.run, {'jobs': self.jobs if jobs is None else jobs}]), \
                mock.patch.object(admission, 'native_file', side_effect=file_bytes) as files:
            admission.verify_native_rehearsal(self.e2e, CONTROLLER, NOW)
        return files

    def test_requires_all_three_successful_same_head_jobs_and_complete_source_continuity(self):
        files = self.verify()
        self.assertEqual(files.call_count, 2 * len(admission.REHEARSED_FILES))
        inspected = {call.args[1] for call in files.call_args_list}
        self.assertIn('scripts/axxium_events_release.py', inspected)
        self.assertIn('scripts/staging/axxium/active_upgrade_fixture.py', inspected)
        self.assertIn('scripts/staging/axxium/fixture_netns.py', inspected)

    def test_every_missing_failed_cancelled_or_wrong_head_case_refuses(self):
        for index in range(3):
            variants = [self.jobs[:index] + self.jobs[index + 1:]]
            for change in ({'conclusion': 'failure'}, {'conclusion': 'cancelled'},
                           {'status': 'in_progress'}, {'head_sha': 'f' * 40}, {'run_id': RUN + 1}):
                jobs = deepcopy(self.jobs)
                jobs[index].update(change)
                variants.append(jobs)
            for jobs in variants:
                with self.subTest(index=index, jobs=jobs), self.assertRaises(Refusal):
                    self.verify(jobs=jobs)

    def test_changed_release_or_isolation_source_is_not_covered_by_old_jobs(self):
        for path in ('scripts/axxium_events_release.py', 'scripts/axxium_events_census.py',
                     'scripts/staging/axxium/fixture_netns.py', admission.PLAN_PATH):
            with self.subTest(path=path), self.assertRaisesRegex(Refusal, 'differs from the hosted candidate'):
                self.verify(different=path)

    def test_failed_exact_rehearsal_prevents_census_build_credit(self):
        proof = json.dumps({'e2e': self.e2e}).encode()
        with mock.patch.object(admission, 'admit', return_value={'admitted': True}), \
                mock.patch.object(admission, 'native_file', return_value=proof), \
                mock.patch.object(admission, 'verify_native_rehearsal', side_effect=Refusal('not covered')), \
                mock.patch.object(admission, 'verify_native_census_build') as build:
            with self.assertRaises(Refusal):
                admission.admit_events(admission.PLAN_BYTES, CONTROLLER, Path('/unused.zip'), now=NOW)
        build.assert_not_called()


class NativeCensusBuildTests(unittest.TestCase):
    def setUp(self):
        self.pins = json.loads(admission.PLAN_BYTES)['census']
        self.run = {'id': self.pins['build_run_id'], 'head_sha': self.pins['build_head'],
                    'repository': {'full_name': admission.SERVICES}, 'path': admission.CENSUS_WORKFLOW,
                    'run_attempt': self.pins['build_attempt'], 'status': 'completed', 'conclusion': 'success',
                    'updated_at': '2026-11-18T18:00:00Z'}
        self.artifact = {'id': self.pins['artifact_id'], 'name': 'axxium-events-census',
                         'digest': 'sha256:' + self.pins['artifact_sha256'], 'expired': False,
                         'expires_at': '2026-11-20T18:00:00Z',
                         'workflow_run': {'id': self.pins['build_run_id'], 'head_sha': self.pins['build_head']}}

    def test_exact_build_and_recipe_required_and_expired_or_changed_artifact_refuses(self):
        variants = [None, {'expired': True}, {'digest': 'sha256:' + '0' * 64},
                    {'workflow_run': {'id': 999, 'head_sha': self.pins['build_head']}}]
        for change in variants:
            artifact = {**self.artifact, **(change or {})}
            with self.subTest(change=change), mock.patch.object(admission, 'main_ancestor'), \
                    mock.patch.object(admission, 'api', side_effect=[self.run, artifact]), \
                    mock.patch.object(admission, 'native_file', return_value=b'exact source'):
                if change is None:
                    admission.verify_native_census_build(self.pins, CONTROLLER, NOW)
                else:
                    with self.assertRaises(Refusal):
                        admission.verify_native_census_build(self.pins, CONTROLLER, NOW)
        with mock.patch.object(admission, 'main_ancestor'), \
                mock.patch.object(admission, 'api', side_effect=[self.run, self.artifact]), \
                mock.patch.object(admission, 'native_file', side_effect=[b'built recipe', b'changed recipe']):
            with self.assertRaisesRegex(Refusal, 'recipe differs'):
                admission.verify_native_census_build(self.pins, CONTROLLER, NOW)


class ActivationTests(unittest.TestCase):
    def test_wrong_plan_digest_and_artifact_never_construct_a_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / 'plan.json'
            module, driver = root / 'probe.js', root / 'probe.mjs'
            for path, value in ((plan, admission.PLAN_BYTES), (module, b'wrong artifact'), (driver, b'wrong driver')):
                path.write_bytes(value)
                path.chmod(0o600)
            argv = ['--plan', str(plan), '--plan-sha256', sha256(admission.PLAN_BYTES).hexdigest(),
                    '--census-module', str(module), '--census-driver', str(driver), '--apply']
            for raw, expected_hash in ((admission.PLAN_BYTES + b'\n', sha256(admission.PLAN_BYTES).hexdigest()),
                                       (b'{"schema":"unreviewed"}', sha256(b'{"schema":"unreviewed"}').hexdigest()),
                                       (admission.PLAN_BYTES, sha256(admission.PLAN_BYTES).hexdigest())):
                plan.write_bytes(raw)
                flags = [*argv]
                flags[flags.index('--plan-sha256') + 1] = expected_hash
                with self.subTest(raw_size=len(raw)), mock.patch.object(activate, 'EventsHost') as host, \
                        mock.patch.object(activate, 'execute_admitted') as execute, \
                        redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(activate.main(flags), 2)
                host.assert_not_called()
                execute.assert_not_called()
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(err.getvalue(),
                                 'Axxium Events activation refused. Preserve any native operation hold and journal.\n')
            archive = root / 'census.zip'
            archive.write_bytes(b'wrong archive')
            archive.chmod(0o600)
            output = root / 'uncreated-census'
            with self.assertRaises(Refusal):
                census_archive.extract(archive, output, json.loads(admission.PLAN_BYTES))
            self.assertFalse(output.exists())

    def test_actual_tar_imports_and_activation_select_the_reviewed_module_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            unpacked = unpack_controller(directory)
            program = r'''
import contextlib, hashlib, io, json, pathlib, sys
from unittest import mock
root = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(root / 'scripts'))
import axxium_events_admission as admission
import axxium_events_activate as activate
for name in json.loads(sys.argv[2]):
    assert pathlib.Path(sys.modules[name].__file__).is_relative_to(root), name
raw = admission.PLAN_BYTES; plan = admission.validate_plan_bytes(raw); digest = hashlib.sha256(raw).hexdigest()
argv = ['--plan', str(root / admission.PLAN_PATH), '--plan-sha256', digest,
        '--census-module', str(root / 'census/probe.js'), '--census-driver', str(root / 'census/probe.mjs'), '--apply']
with mock.patch.object(activate, 'PinnedCensus') as census, mock.patch.object(activate, 'EventsHost') as host, \
        mock.patch.object(activate, 'execute_admitted', return_value={'status':'healthy'}) as execute, \
        contextlib.redirect_stdout(io.StringIO()) as output:
    assert activate.main(argv) == 0
census.assert_called_once_with(root / 'census/probe.js', root / 'census/probe.mjs',
    module_sha256=plan['census']['module_sha256'], driver_sha256=plan['census']['driver_sha256'])
host.assert_called_once_with(plan, digest, census.return_value)
execute.assert_called_once_with(plan, host.return_value)
assert json.loads(output.getvalue())['status'] == 'healthy'
print('isolated Events archive and activation selection passed')
'''
            result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(unpacked),
                                     json.dumps(sorted(MODULES))], cwd=directory,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'isolated Events archive and activation selection passed\n')


class WorkflowTests(unittest.TestCase):
    def test_protected_caller_and_rechecks_precede_census_extraction_and_ssh_secret(self):
        body = workflow()
        self.assertEqual(set(body.get('on', body.get(True))), {'workflow_call'})
        self.assertEqual(body['env']['PLAN'], admission.PLAN_PATH)
        self.assertEqual(body['permissions'], {'contents': 'read', 'actions': 'read', 'pull-requests': 'read'})
        admit, deploy = body['jobs']['admit'], body['jobs']['deploy']
        self.assertNotIn('environment', admit)
        self.assertNotIn('secrets.', str(admit))
        self.assertEqual(deploy['environment'], 'production')
        self.assertEqual(deploy['needs'], 'admit')
        self.assertEqual(deploy['if'], "needs.admit.outputs.plan_sha256 != ''")
        qualification = next(step for step in admit['steps'] if step.get('id') == 'qualification')
        for guard in ('test "$CALLER_LOCKED" = true', 'test "$CALLER_REF_PROTECTED" = true',
                      'open-hax/services/.github/workflows/deploy-stack.yml@refs/heads/main',
                      'test "$SOURCE_SHA" = "$(jq -er .source_sha "$PLAN")"'):
            self.assertIn(guard, qualification['run'])
        steps = deploy['steps']
        recheck = next(i for i, step in enumerate(steps) if '--archive stage.zip' in step.get('run', ''))
        extraction = next(i for i, step in enumerate(steps) if 'axxium_events_census_archive.py' in step.get('run', ''))
        secret = next(i for i, step in enumerate(steps) if 'SSH_PRIVATE_KEY' in step.get('env', {}))
        self.assertLessEqual(recheck, extraction)
        self.assertLess(extraction, secret)
        run = steps[extraction]['run']
        self.assertLess(run.index('axxium_events_admission.py'), run.index('axxium_events_census_archive.py'))
        self.assertLess(run.index('.admitted == true and .plan_sha256 == $sha'),
                        run.index('axxium_events_census_archive.py'))
        for job in (admit, deploy):
            gate = next(step for step in job['steps'] if '--archive stage.zip' in step.get('run', ''))
            self.assertIn('python3 scripts/axxium_events_admission.py', gate['run'])
            self.assertEqual(gate['env']['GH_TOKEN'], gate['env']['GITHUB_TOKEN'])
        secret_body = steps[secret]['run']
        self.assertIn('assert_pinned_host_key', secret_body)
        self.assertIn('StrictHostKeyChecking yes', secret_body)
        self.assertIn('check-axxium-production-hold.sh', secret_body)
        self.assertIn("python3 -B '$controller/scripts/axxium_events_activate.py'", secret_body)
        self.assertIn("--census-module '$controller/census/probe.js'", secret_body)
        self.assertIn("--census-driver '$controller/census/probe.mjs'", secret_body)
        for forbidden in ('--recover-interrupted', 'production-binding.json', 'provision_axxium_', 'docker build', 'docker load'):
            self.assertNotIn(forbidden, secret_body)


if __name__ == '__main__':
    unittest.main()
