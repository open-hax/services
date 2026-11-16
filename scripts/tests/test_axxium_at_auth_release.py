import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import axxium_at_auth_release as auth


class AtAuthReleaseTest(unittest.TestCase):
    def test_fixed_plan_and_separate_operation(self):
        plan = json.loads((ROOT / 'digitalocean/services/axxium/at-auth-production-plan.json').read_bytes())
        self.assertEqual(auth.validate_plan(plan), plan)
        host = auth.Host('a' * 64)
        self.assertEqual(host.operation_name(plan), auth.SOURCE + '-at-auth')
        self.assertEqual(host.signing_key.parent.name, host.operation_name(plan))
        plan['expected']['app_image_id'] = auth.IMAGE
        with self.assertRaisesRegex(auth.release.Refused, 'Incumbent'):
            auth.validate_plan(plan)

    def test_exact_app_only_delta(self):
        old = {'services': {'axxium': {'image': auth.release.IMAGE, 'environment': {'JWT_AUDIENCE': 'promethean',
                'AXXIUM_OIDC_CONFIG_FILE': '/run/axxium/oidc/authority.json'}, 'volumes': [], 'user': '1000:1000'},
                'pds': {'image': 'pds'}, 'axxium-db': {'image': 'postgres'}}}
        key = auth.Host('a' * 64).signing_key
        overlay = auth.compose_overlay(key)['services']['axxium']
        new = copy.deepcopy(old)
        profile = new['services']['axxium']
        profile['environment'].update(overlay['environment'])
        profile.update(image=overlay['image'], volumes=overlay['volumes'])
        auth.verify_projection(copy.deepcopy(old), copy.deepcopy(new), key)
        profile['environment']['AXXIUM_MAILBOX_LEDGER_FILE'] = '/fixture/ledger.edn'
        with self.assertRaisesRegex(auth.release.Refused, 'environment'):
            auth.verify_projection(old, new, key)

    def test_sign_in_uses_exact_canonical_authorizer(self):
        location = auth.ORIGIN + '/oauth/authorize?client_id=' + auth.ORIGIN + '/api/auth/atproto/client-metadata.json&request_uri=urn:fixture'
        auth.verify_at_redirect(302, location)
        for status, target in [(500, location), (302, location.replace('axxium.promethean.rest', 'outside.test')),
                               (302, location.replace('request_uri', 'token')), (200, location)]:
            with self.subTest(status=status, target=target), self.assertRaises(auth.release.KnownFailure):
                auth.verify_at_redirect(status, target)
