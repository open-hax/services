"""Stage upgrades retain the owning queue, admission and exact host scope."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.body = yaml.safe_load((ROOT / '.github/workflows/deploy-colab-transport-stage.yml').read_text())

    def test_upgrade_has_no_new_trigger_or_mutation_queue(self):
        self.assertEqual(set(self.body.get('on', self.body.get(True))), {'workflow_call'})
        chain = yaml.safe_load((ROOT / '.github/workflows/deploy-stack-chain.yml').read_text())
        selected = chain['jobs']['deploy-colab-stage']
        self.assertEqual(selected['if'], "inputs.scope == 'colab-stage'")
        self.assertEqual(selected['uses'], './.github/workflows/deploy-colab-transport-stage.yml')
        self.assertIs(selected['with']['caller_holds_production_host_lock'], True)
        self.assertTrue(all('concurrency' not in job for job in self.body['jobs'].values()))

    def test_unresolved_closed_plan_and_native_publisher_are_checked_without_host_secret(self):
        job = self.body['jobs']['admit']
        self.assertNotIn('secrets.', str(job))
        self.assertNotIn('ssh open-hax-target', str(job))
        text = '\n'.join(s.get('run', '') for s in job['steps'])
        self.assertLess(text.index('validate_plan'), text.index('gh api'))
        self.assertIn('.candidate.source_sha', text)
        self.assertIn('colab_stage_upgrade_admission.py', text)
        self.assertIn('deploy-stack.yml@refs/heads/main', text)

    def test_recheck_then_key_then_shared_hold_guard_then_controller_effects(self):
        job = self.body['jobs']['stage']
        self.assertEqual(job['needs'], 'admit')
        self.assertEqual(job['if'], "needs.admit.outputs.admitted == 'true'")
        steps = job['steps']
        evidence = next(i for i, s in enumerate(steps) if 'colab_stage_upgrade_admission.py' in s.get('run', ''))
        key = next(i for i, s in enumerate(steps) if 'SSH_PRIVATE_KEY' in s.get('env', {}))
        hold = next(i for i, s in enumerate(steps) if 'check-axxium-production-hold.sh' in s.get('run', ''))
        effect = next(i for i, s in enumerate(steps) if '--apply' in s.get('run', ''))
        self.assertLess(evidence, key)
        self.assertLess(key, hold)
        self.assertLess(hold, effect)

    def test_controller_only_delivery_never_enrolls_client_or_restarts_axxium(self):
        text = '\n'.join(s.get('run', '') for s in self.body['jobs']['stage']['steps'])
        self.assertIn('scripts/enroll_colab_transport_stage.py', text)
        self.assertIn('colab_transport_stage_upgrade.py', text)
        self.assertNotIn('--provision', text)
        self.assertNotIn('--enroll', text)
        self.assertNotIn('docker build', text)
        self.assertNotIn('docker load', text)
        self.assertNotIn('rsync', text)
        self.assertIn('.restore_copy_verified == true', text)
        self.assertIn('.google_execution_verified == false', text)
        self.assertIn('.production_qualified == false', text)

    def test_actual_checkout_and_native_api_share_exact_controller_and_token(self):
        for job in self.body['jobs'].values():
            checkout = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@'))
            self.assertEqual(checkout['with']['ref'], '${{ github.sha }}')
            self.assertIs(checkout['with']['persist-credentials'], False)
            evidence = next(s for s in job['steps'] if 'colab_stage_upgrade_admission.py' in s.get('run', ''))
            self.assertEqual(evidence['env']['GH_TOKEN'], '${{ github.token }}')
            self.assertEqual(evidence['env']['GITHUB_TOKEN'], evidence['env']['GH_TOKEN'])


if __name__ == '__main__': unittest.main()
