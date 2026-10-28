"""Exercise production caller boundaries without touching any host or secrets."""
import copy
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github/workflows'

def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)

spec = importlib.util.spec_from_file_location('deploy_chain_guard', ROOT / 'scripts/check-deploy-chain.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
path_errors = guard.axxium_path_errors


class AxxiumProductionWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.entry = workflow('deploy-stack.yml')
        self.chain = workflow('deploy-stack-chain.yml')
        self.child = workflow('deploy-axxium-production.yml')

    def test_guarded_two_phase_caller_and_mutated_controls(self):
        self.assertEqual([], path_errors(self.entry, self.chain, self.child))
        for change in ('lock', 'scope', 'dispatch', 'build', 'host', 'recheck'):
            entry, chain, child = copy.deepcopy(self.entry), copy.deepcopy(self.chain), copy.deepcopy(self.child)
            if change == 'lock': entry['jobs']['deploy']['concurrency']['group'] = 'other'
            if change == 'scope': chain['jobs']['deploy-axxium']['if'] = 'true'
            if change == 'dispatch': child['on']['workflow_dispatch'] = {}
            if change == 'build': child['jobs']['build']['if'] = 'true'
            if change == 'host': child['jobs']['deploy']['environment'] = 'staging'
            if change == 'recheck':
                steps = child['jobs']['deploy']['steps']
                step = next(s for s in steps if s.get('name') == 'Recheck exact source, controller, archive and owner intent')
                step['run'] = step['run'].replace('ADMITTED_IMAGE_SHA256', 'IGNORED_IMAGE')
            self.assertTrue(path_errors(entry, chain, child), change)

    def test_entry_script_fails_closed_for_wrong_scope_or_unsafe_inputs(self):
        step = next(s for s in self.entry['jobs']['authorize']['steps'] if s.get('id') == 'decide')
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'github-output')
            base = {**os.environ, 'EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                    'DEPLOY_SCOPE': 'axxium', 'AXXIUM_PR': '34', 'AXXIUM_IMAGE_RUN_ID': '',
                    'GITHUB_OUTPUT': output, 'INCLUDE_WEBSITE': 'false', 'WEBSITE_IMAGE': '',
                    'KNOXX_SHA': '', 'PROXX_SHA': ''}
            def run(values):
                return subprocess.run(['bash', '-c', step['run']], env={**base, **values}, capture_output=True, text=True)
            self.assertEqual(run({}).returncode, 0)
            self.assertEqual(run({'AXXIUM_IMAGE_RUN_ID': '123'}).returncode, 0)
            for bad in ({'GITHUB_REF': 'refs/heads/feature'}, {'AXXIUM_PR': ''}, {'AXXIUM_PR': '0'},
                        {'AXXIUM_IMAGE_RUN_ID': '../123'}, {'INCLUDE_WEBSITE': 'true'},
                        {'DEPLOY_SCOPE': 'proxx'}, {'EVENT_NAME': 'push'}):
                self.assertNotEqual(run(bad).returncode, 0, bad)

    def test_producing_run_must_be_successful_current_main_stack_workflow(self):
        step = next(s for s in self.child['jobs']['deploy']['steps'] if s.get('name') == 'Validate the producing run before downloading its artifact')
        with tempfile.TemporaryDirectory() as directory:
            gh = Path(directory) / 'gh'
            gh.write_text('#!/bin/sh\ncat "$SYNTHETIC_RUN"\n')
            gh.chmod(0o755)
            source = 'a' * 40
            data = {'event': 'workflow_dispatch', 'path': '.github/workflows/deploy-stack.yml',
                    'head_sha': source, 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}
            fixture = Path(directory) / 'fixture.json'
            env = {**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH'],
                   'SYNTHETIC_RUN': str(fixture), 'IMAGE_RUN_ID': '123', 'CONTROLLER_SHA': source}
            def run(value):
                fixture.write_text(json.dumps(value))
                return subprocess.run(['bash', '-c', step['run']], cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(run(data).returncode, 0)
            for field, invalid in [('event', 'push'), ('path', '.github/workflows/other.yml'),
                                   ('head_sha', 'b'*40), ('head_branch', 'feature'), ('conclusion', 'failure')]:
                self.assertNotEqual(run({**data, field: invalid}).returncode, 0, field)

if __name__ == '__main__':
    unittest.main()
