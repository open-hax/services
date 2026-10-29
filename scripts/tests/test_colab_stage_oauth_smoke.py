# SPDX-License-Identifier: GPL-3.0-or-later
"""The deployed synthetic proof must refuse ambiguity and always remove its session."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest import TestCase, mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import colab_stage_oauth_smoke as proof


class StageProofTests(TestCase):
    def test_fixed_peers_refuse_redirect_escapes(self):
        for url in [proof.ISSUER + '/auth?state=x', proof.GATEWAY + '/mcp']:
            self.assertTrue(proof.stage_url(url))
        for url in ['https://axxium.promethean.rest/oidc/auth',
                    'https://staging.axxium.promethean.rest.evil/oidc/auth',
                    'https://user@staging.axxium.promethean.rest/oidc/auth',
                    proof.ISSUER + '/auth#secret', proof.ISSUER + '/../api/auth',
                    proof.ISSUER + '/%2e%2e/api/auth', proof.ORIGIN + '/api/auth',
                    'http://localhost:18771/mcp', proof.CALLBACK,
                    proof.GATEWAY + '/authorize/../mcp']:
            with self.subTest(url=url):
                self.assertFalse(proof.stage_url(url))

    def test_browser_refuses_escaped_url_before_io(self):
        browser = proof.Browser()
        with mock.patch.object(browser.opener, 'open') as opened:
            with self.assertRaisesRegex(proof.SmokeFailure, 'escaped'):
                browser.request('https://external.invalid/token')
            opened.assert_not_called()

    def test_browser_has_no_automatic_redirect_or_environment_proxy(self):
        browser = proof.Browser()
        redirects = [x for x in browser.opener.handlers if isinstance(x, proof.NoRedirect)]
        self.assertEqual(1, len(redirects))
        self.assertIsNone(redirects[0].redirect_request(None, None, 302, '', {}, 'https://external.invalid'))

    def test_root_cookie_confined_to_secure_stage_origin(self):
        browser = proof.Browser()
        browser.root_cookie('axxium_session', 'private-synthetic-token')
        cookie, = browser.cookies
        self.assertEqual('staging.axxium.promethean.rest', cookie.domain)
        self.assertTrue(cookie.secure)
        self.assertFalse(cookie.domain_specified)
        with self.assertRaises(proof.SmokeFailure):
            browser.root_cookie('bad;cookie', 'never-sent')

    def test_gateway_consent_uses_only_issued_hidden_fields(self):
        url = proof.GATEWAY + '/consent?txn_id=transaction'
        html = b'''<form method="POST" action=""><input type="hidden" name="txn_id" value="transaction">
          <input type="hidden" name="csrf_token" value="issued-csrf"><input type="hidden" name="submit" value="true">
          <button name="action" value="approve">Allow</button></form>'''
        target, fields, headers = proof.consent_request(url, html)
        self.assertEqual(url, target)
        self.assertEqual({'txn_id': 'transaction', 'csrf_token': 'issued-csrf', 'submit': 'true',
                          'action': 'approve'}, fields)
        self.assertEqual({'Origin': proof.GATEWAY}, headers)

    def test_axxium_consent_reuses_get_issued_csrf(self):
        url = proof.ISSUER + '/interaction/owned'
        html = ("<form method=post action='/oidc/interaction/owned'>"
                "<input type=hidden name=csrf value='" + 's' * 43 + "'></form>").encode()
        target, fields, headers = proof.consent_request(url, html)
        self.assertEqual(url, target)
        self.assertEqual({'csrf': 's' * 43, 'decision': 'approve'}, fields)
        self.assertEqual({'Origin': proof.ORIGIN}, headers)

    def test_consent_rejects_credential_forms_duplicates_and_cross_targets(self):
        url = proof.ISSUER + '/interaction/owned'
        cases = [b'<form method=post><input type=password name=password></form>',
                 b'<form method=post><input type=hidden name=csrf value=x><input type=hidden name=csrf value=y></form>',
                 b'<form method=post action="https://external.invalid/"><input type=hidden name=csrf value=x></form>',
                 b'<form method=get><input type=hidden name=csrf value=x></form>',
                 b'<form method=post></form><form method=post></form>',
                 b'<form method=post><form method=post></form></form>']
        for body in cases:
            with self.subTest(body=body), self.assertRaises(proof.SmokeFailure):
                proof.consent_request(url, body)

    def test_callback_state_and_duplicates_checked_without_contacting_callback(self):
        for query, expected in [({'code': 'private-code', 'state': 'expected'}, 'private-code'),
                                ({'code': 'private-code', 'state': 'wrong'}, None),
                                ({'code': 'private-code', 'state': 'expected', 'error': 'denied'}, None)]:
            browser = mock.Mock()
            browser.request.return_value = proof.Response(302, {'location': proof.CALLBACK + '?' + urlencode(query)}, b'')
            if expected:
                self.assertEqual(expected, proof.authorization_code(browser, proof.GATEWAY + '/authorize', 'expected'))
            else:
                with self.assertRaises(proof.SmokeFailure):
                    proof.authorization_code(browser, proof.GATEWAY + '/authorize', 'expected')
            browser.request.assert_called_once()
        browser = mock.Mock()
        browser.request.return_value = proof.Response(302, {'location': proof.CALLBACK + '?code=x&code=y&state=expected'}, b'')
        with self.assertRaises(proof.SmokeFailure):
            proof.authorization_code(browser, proof.GATEWAY + '/authorize', 'expected')

    def test_authorization_will_not_follow_foreign_location(self):
        browser = mock.Mock()
        browser.request.return_value = proof.Response(302, {'location': 'https://external.invalid/?code=private'}, b'')
        with self.assertRaisesRegex(proof.SmokeFailure, 'redirect refused'):
            proof.authorization_code(browser, proof.GATEWAY + '/authorize', 'expected')
        browser.request.assert_called_once()

    def test_native_session_uses_fixed_stage_container_and_stdin(self):
        identity = '7bd1ae66-92b6-448b-9e34-eedeb243660a'
        with mock.patch.object(proof.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
                json.dumps({'deleted': 1, 'remaining': 0}), '')) as run:
            self.assertEqual({'deleted': 1, 'remaining': 0}, proof.stage_session('delete', identity))
            args = run.call_args.args[0]
            self.assertIn(proof.CONTAINER, args)
            self.assertIn('COLAB_STAGE_SMOKE_SESSION=' + identity, args)
            self.assertEqual(proof.SESSION_SCRIPT, run.call_args.kwargs['input'])
            self.assertEqual(20, run.call_args.kwargs['timeout'])
            self.assertNotIn('JWT_SECRET=', ' '.join(args))
        self.assertIn(".setJti(id)", proof.SESSION_SCRIPT)
        self.assertIn("interval '5 minutes'", proof.SESSION_SCRIPT)
        self.assertIn('a.status=\'active\' AND e.kind=\'human\'', proof.SESSION_SCRIPT)

    def test_native_session_failure_does_not_disclose_subprocess_output(self):
        identity = '7bd1ae66-92b6-448b-9e34-eedeb243660a'
        with mock.patch.object(proof.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1,
                               'private-token-value', 'private-db-password')):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream), self.assertRaises(proof.SmokeFailure) as raised:
                proof.stage_session('create', identity)
            self.assertNotIn('private', str(raised.exception))
            self.assertEqual('', stream.getvalue())

    def test_cleanup_attempted_even_when_session_creation_output_uncertain(self):
        calls = []
        def session(operation, identity):
            calls.append((operation, identity))
            if operation == 'create':
                raise proof.SmokeFailure('Stage session operation failed')
            return {'deleted': 1, 'remaining': 0}
        with mock.patch.object(proof, 'stage_session', side_effect=session):
            with self.assertRaises(proof.SmokeFailure):
                proof.smoke()
        self.assertEqual(['create', 'delete'], [x[0] for x in calls])
        self.assertEqual(calls[0][1], calls[1][1])

    def test_cleanup_failure_overrides_positive_or_negative_claim(self):
        def session(operation, identity):
            return ({'token': 'synthetic', 'cookie': 'axxium_session'} if operation == 'create'
                    else {'deleted': True, 'remaining': 0})
        browser = mock.Mock()
        browser.request.return_value = proof.Response(503, {}, b'')
        with mock.patch.object(proof, 'stage_session', side_effect=session), mock.patch.object(proof, 'Browser', return_value=browser):
            with self.assertRaisesRegex(proof.SmokeFailure, 'cleanup'):
                proof.smoke()

    def test_partial_flow_failure_still_deletes_owned_session(self):
        browser = mock.Mock()
        browser.request.return_value = proof.Response(503, {}, b'')
        with (mock.patch.object(proof, 'stage_session', side_effect=[
                {'token': 'private', 'cookie': 'axxium_session'}, {'deleted': 1, 'remaining': 0}]) as session,
                mock.patch.object(proof, 'Browser', return_value=browser)):
            with self.assertRaisesRegex(proof.SmokeFailure, 'registration'):
                proof.smoke()
            self.assertEqual(['create', 'delete'], [x.args[0] for x in session.call_args_list])

    def _flow(self, denied=401, tool_override=None):
        browser = mock.Mock()
        browser.request.side_effect = [
            proof.Response(201, {}, b'{"client_id":"synthetic-client"}'),
            proof.Response(200, {}, b'{"access_token":"private-downstream"}'),
            proof.Response(400, {}, b'{}'), proof.Response(denied, {}, b'{}')]
        responses = {
            'colab_status': {'structuredContent': {'synthetic_upstreams': True,
                'bridge': {'browser_connected': False, 'execution_verified': False}}},
            'colab_tools': {'structuredContent': {'result': [{'name': 'fixture_echo'}]}},
            'colab_call': {'structuredContent': {'fixture_echo': 'services synthetic Stage assertion',
                'synthetic_upstreams': True, 'execution_verified': False}},
            'colab_compute_usage': {'structuredContent': {'synthetic_upstreams': True,
                'compute_units': None, 'execution_verified': False}},
            'colab_compute_sessions': {'structuredContent': {'synthetic_upstreams': True,
                'sessions': [], 'execution_verified': False}},
            'colab_compute_start': {'isError': True}}
        if tool_override:
            responses.update(tool_override)
        with (mock.patch.object(proof, 'Browser', return_value=browser),
              mock.patch.object(proof, 'stage_session', side_effect=[
                  {'token': 'private-root', 'cookie': 'axxium_session'}, {'deleted': 1, 'remaining': 0}]) as session,
              mock.patch.object(proof, 'authorization_code', return_value='private-code'),
              mock.patch.object(proof, 'initialized'),
              mock.patch.object(proof, 'rpc', return_value={'tools': [{'name': x} for x in responses]}),
              mock.patch.object(proof, 'tool', side_effect=lambda b, t, n, a=None: responses[n])):
            result = proof.smoke()
            self.assertEqual(['create', 'delete'], [x.args[0] for x in session.call_args_list])
            return result

    def test_complete_flow_reports_only_measured_safe_fields(self):
        result = self._flow()
        self.assertTrue(result['passed'])
        self.assertTrue(result['root_session_revocation_verified'])
        self.assertFalse(result['human_login_verified'])
        self.assertFalse(result['google_execution_verified'])
        self.assertNotIn('private', json.dumps(result))

    def test_positive_forwarding_without_revocation_is_not_a_pass(self):
        with self.assertRaisesRegex(proof.SmokeFailure, 'revocation'):
            self._flow(denied=200)

    def test_compute_execution_success_is_never_stage_success(self):
        with self.assertRaisesRegex(proof.SmokeFailure, 'execution was not refused'):
            self._flow(tool_override={'colab_compute_start': {'isError': False}})

    def test_google_or_unlabelled_compute_results_are_not_fixture_proof(self):
        with self.assertRaisesRegex(proof.SmokeFailure, 'provenance'):
            self._flow(tool_override={'colab_compute_usage': {'structuredContent': {
                'synthetic_upstreams': True, 'execution_verified': False, 'compute_units': 100}}})

