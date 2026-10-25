"""Run the shipped legacy entry guards before checkout or host effects."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load((ROOT/'.github/workflows'/name).read_text(), Loader=yaml.BaseLoader)


class LegacyLaneTest(unittest.TestCase):
    def run_guard(self, script, **values):
        with tempfile.TemporaryDirectory(prefix='legacy-lane-guard-') as directory:
            output = Path(directory)/'output'
            output.write_text('preserved=value\n')
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                env={'PATH': os.defpath, 'GITHUB_OUTPUT': str(output), **values},
                cwd=directory, text=True, capture_output=True, timeout=10)
            return result, output.read_text()

    def test_manual_stack_refuses_even_a_previously_valid_immutable_candidate(self):
        steps = workflow('deploy-stack.yml')['jobs']['authorize']['steps']
        script = next(step for step in steps if step.get('id') == 'decide')['run']
        for extra in ({}, {'INCLUDE_WEBSITE': 'true'}, {'WEBSITE_IMAGE': 'old/image@sha256:'+'a'*64}):
            with self.subTest(extra=extra):
                values = {'EVENT_NAME': 'workflow_dispatch', 'DEPLOY_SCOPE': 'stack',
                    'KNOXX_SHA': 'a'*40, 'INCLUDE_WEBSITE': 'false', 'WEBSITE_IMAGE': '', **extra}
                result, output = self.run_guard(script, **values)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('protected environment receiver', result.stderr)
                self.assertEqual('preserved=value\n', output)

    def service_guard(self):
        steps = workflow('deploy-digitalocean.yml')['jobs']['deploy']['steps']
        first = steps[0]
        self.assertEqual('Refuse retired application deployment', first.get('name'))
        self.assertEqual({'SERVICE': '${{ inputs.service }}'}, first.get('env'))
        self.assertNotIn('uses', first)
        return first['run']

    def test_direct_reusable_chain_refuses_before_source_builds_or_host_bootstrap(self):
        jobs = workflow('deploy-stack-chain.yml')['jobs']
        guard = jobs['admit-legacy-scope']
        self.assertNotIn('needs', guard)
        self.assertEqual(1, len(guard['steps']))
        self.assertNotIn('uses', guard['steps'][0])
        for scope in ('stack', 'proxx', 'unknown', ''):
            result, output = self.run_guard(guard['steps'][0]['run'], DEPLOY_SCOPE=scope)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual('preserved=value\n', output)
        result, _ = self.run_guard(guard['steps'][0]['run'], DEPLOY_SCOPE='website')
        self.assertEqual(0, result.returncode, result.stderr)
        for name in ('resolve-knoxx', 'build-proxx', 'verify-proxx-host'):
            self.assertEqual('admit-legacy-scope', jobs[name]['needs'])
        self.assertEqual('resolve-knoxx', jobs['knoxx-preflight']['needs'])
        self.assertEqual('knoxx-preflight', jobs['provision-host']['needs'])

    def test_reusable_application_entry_refuses_before_checkout_or_host_effects(self):
        script = self.service_guard()
        for service in ('proxx', 'knoxx', 'axxium'):
            with self.subTest(service=service):
                result, output = self.run_guard(script, SERVICE=service)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('protected environment receiver', result.stderr)
                self.assertEqual('preserved=value\n', output)

    def test_only_existing_isolated_website_and_ingress_helpers_remain_admitted(self):
        script = self.service_guard()
        for service in ('website', 'caddy'):
            with self.subTest(service=service):
                result, output = self.run_guard(script, SERVICE=service)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual('preserved=value\n', output)
        for service in ('', 'other', 'PROXX', 'proxx\n', '../proxx', 'knoxx;true'):
            with self.subTest(service=service):
                result, output = self.run_guard(script, SERVICE=service)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual('preserved=value\n', output)

    def test_isolated_website_dispatch_still_reaches_its_existing_qualification(self):
        steps = workflow('deploy-stack.yml')['jobs']['authorize']['steps']
        script = next(step for step in steps if step.get('id') == 'decide')['run']
        result, output = self.run_guard(script, EVENT_NAME='workflow_dispatch',
            DEPLOY_SCOPE='website', KNOXX_SHA='', INCLUDE_WEBSITE='false', WEBSITE_IMAGE='')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('preserved=value\nshould_deploy=true\n', output)


if __name__ == '__main__':
    unittest.main()
