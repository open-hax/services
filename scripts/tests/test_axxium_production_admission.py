"""Exercise the native qualification and owner gates copied from PR #105."""
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import github_environment_admission as admission

SHA = 'a' * 40
CONTROLLER = 'b' * 40
IMAGE = 'c' * 64
NOW = datetime.now(timezone.utc)

def batches():
    return [{'source_sha': SHA, 'status': 'passed', 'baseline': 'passed', 'batch_index': i,
             'mutants': [{'fingerprint': f'{i}-{j}', 'status': 'killed', 'phase': 'test'} for j in range(250)]}
            for i in range(4)]

def qualification():
    receipt = {'source_sha': SHA, 'status': 'passed', 'assertions': 1}
    return {'repository': 'open-hax/axxium', 'source_sha': SHA, 'integration': receipt,
            'e2e': receipt, 'mutation_batches': batches()}

def api_fixture(path, *, accept=None):
    if path.endswith('/branches/main'):
        return {'commit': {'sha': SHA}}
    if '/pulls/' in path:
        return {'merged': True, 'base': {'ref': 'main'}, 'merge_commit_sha': SHA}
    if '/git/trees/' in path:
        return {'truncated': False, 'tree': [{'type': 'blob', 'path': 'src/main.cljs'}]}
    if '/contents/digitalocean/qualifications/' in path:
        return {'type': 'file', 'encoding': 'base64',
                'content': base64.encodebytes(json.dumps(qualification()).encode()).decode()}
    raise AssertionError(path)

def owner_review():
    return {'id': 1, 'state': 'COMMENTED', 'commit_id': SHA, 'submitted_at': NOW.isoformat(),
            'user': {'id': 1, 'type': 'User', 'login': 'owner'},
            'body': f'Authorize production deployment of axxium {SHA} with Services {CONTROLLER} image sha256:{IMAGE} on droplet.'}

class AxxiumProductionAdmissionTest(unittest.TestCase):
    def test_missing_qualification_never_admits_or_reads_review(self):
        def api(path, *, accept=None):
            if '/contents/digitalocean/qualifications/' in path:
                raise HTTPError(path, 404, 'missing', {}, None)
            return api_fixture(path, accept=accept)
        with patch.object(admission, 'api', side_effect=api), patch.object(admission, 'pages') as pages:
            result = admission.admit('open-hax/axxium', 34, 'production', controller_sha=CONTROLLER)
        self.assertFalse(result['admitted'])
        self.assertIn('qualification', result['reason'])
        pages.assert_not_called()

    def test_existing_policy_accepts_complete_pinned_evidence_but_requires_native_owner_for_image(self):
        with patch.object(admission, 'api', side_effect=api_fixture), patch.object(admission, 'pages', return_value=[owner_review()]), patch.object(admission, 'owns_files', return_value=True):
            result = admission.admit('open-hax/axxium', 34, 'production', controller_sha=CONTROLLER, image_sha256=IMAGE)
            self.assertTrue(result['admitted'])
            self.assertEqual(result['source_sha'], SHA)
            denied = admission.admit('open-hax/axxium', 34, 'production', controller_sha=CONTROLLER, image_sha256='d'*64)
            self.assertFalse(denied['admitted'])
            denied = admission.admit('open-hax/knoxx', 34, 'production', controller_sha=CONTROLLER)
            self.assertFalse(denied['admitted'])

    def test_invalid_mutants_or_changed_main_refuse(self):
        data = qualification()
        data['mutation_batches'][0]['mutants'][0]['status'] = 'survived'
        fake = {'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(json.dumps(data).encode()).decode()}
        def invalid(path, *, accept=None):
            return fake if '/contents/digitalocean/qualifications/' in path else api_fixture(path, accept=accept)
        with patch.object(admission, 'api', side_effect=invalid):
            self.assertFalse(admission.admit('open-hax/axxium', 34, 'production', controller_sha=CONTROLLER)['admitted'])
        pr = {'merged': True, 'base': {'ref': 'main'}, 'merge_commit_sha': 'f'*40}
        with patch.object(admission, 'api', return_value={'commit': {'sha': SHA}}):
            self.assertFalse(admission.protected_admission('open-hax/axxium', 34, 'production', pr, CONTROLLER, None)['admitted'])

if __name__ == '__main__':
    unittest.main()
