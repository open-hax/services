"""Network-free integration tests for the disposable OIDC probe."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import runpy
import sys
import time
import unittest
import urllib.parse
from email.message import Message
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

PROBE = Path(__file__).with_name('probe.py')
spec = importlib.util.spec_from_file_location('staging_probe', PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
SECRET = 'synthetic-secret-do-not-print'
ORIGIN = 'http://127.0.0.1:8787'


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def b64int(value):
    return b64(value.to_bytes((value.bit_length() + 7) // 8, 'big'))


class Response(io.BytesIO):
    def __init__(self, code, data=None, location=None):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        super().__init__(data or b'')
        self.code = code
        self.headers = Message()
        if location:
            self.headers['Location'] = location


class FixtureOpener:
    """Implement only endpoints exercised by the probe; no sockets are created."""
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.active = True
        self.logged_in = False
        self.break_token = False
        self.break_signature = False
        self.redirect_external = False
        self.nonce = None
        self.challenge = None
        self.state = None
        self.urls = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        url = urllib.parse.urlsplit(request.full_url)
        if (url.scheme, url.hostname, url.port) != ('http', '127.0.0.1', 8787):
            raise AssertionError('Unexpected outbound request')
        path = url.path
        if path == '/api/auth/me':
            return Response(200, {'actor': {'id': 'actor_synthetic'}}) if self.logged_in else Response(401)
        if path == '/oidc/.well-known/openid-configuration':
            return Response(200, {'issuer': ORIGIN + '/oidc', 'code_challenge_methods_supported': ['S256'],
                'authorization_endpoint': ORIGIN + '/oidc/auth', 'token_endpoint': ORIGIN + '/oidc/token',
                'jwks_uri': ORIGIN + '/oidc/jwks', 'introspection_endpoint': ORIGIN + '/oidc/introspect',
                'revocation_endpoint': ORIGIN + '/oidc/revoke'})
        if path == '/oidc/auth':
            query = urllib.parse.parse_qs(url.query)
            self.nonce = query['nonce'][0]
            self.challenge = query['code_challenge'][0]
            self.state = query['state'][0]
            location = 'http://example.invalid/escape' if self.redirect_external else '/oidc/interaction/synthetic'
            return Response(302, location=location)
        if path == '/oidc/interaction/synthetic' and request.method == 'GET':
            return Response(200, b"<form><input name=csrf value='synthetic-csrf'></form>")
        if path == '/oidc/jwks':
            public = self.key.public_key().public_numbers()
            return Response(200, {'keys': [{'kty': 'RSA', 'kid': 'synthetic', 'n': b64int(public.n), 'e': b64int(public.e)}]})
        if path in ('/api/auth/signup', '/api/auth/login'):
            self.logged_in = True
            return Response(200, {'actor': {'id': 'actor_synthetic'}})
        if path == '/api/auth/logout':
            self.logged_in = False
            return Response(200, {})
        form = urllib.parse.parse_qs((request.data or b'').decode())
        if path == '/oidc/interaction/synthetic':
            if request.get_header('Origin') != ORIGIN:
                return Response(403)
            if form.get('csrf') != ['synthetic-csrf'] or form.get('decision') != ['approve']:
                return Response(400)
            return Response(303, location='http://127.0.0.1:8765/callback?' + urllib.parse.urlencode({'code': 'synthetic-code', 'state': self.state}))
        if path == '/oidc/token':
            challenge = b64(hashlib.sha256(form.get('code_verifier', [''])[0].encode()).digest())
            if challenge != self.challenge or form.get('client_secret') != [SECRET]:
                return Response(400)
            header = b64(json.dumps({'alg': 'RS256', 'kid': 'synthetic'}).encode())
            claims = b64(json.dumps({'sub': 'actor_wrong' if self.break_token else 'actor_synthetic',
                'iss': ORIGIN + '/oidc', 'aud': 'synthetic-staging-probe',
                'nonce': self.nonce, 'exp': int(time.time()) + 300}).encode())
            signed = (header + '.' + claims).encode()
            signature = self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
            if self.break_signature:
                signature = bytes([signature[0] ^ 1]) + signature[1:]
            return Response(200, {'id_token': signed.decode() + '.' + b64(signature), 'access_token': 'synthetic-access-token'})
        if path == '/oidc/introspect':
            return Response(200, {'active': self.active})
        if path == '/oidc/revoke':
            self.active = False
            return Response(200, {})
        return Response(404)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FixtureOpener()
        self.output = io.StringIO()

    def execute(self, base=ORIGIN):
        argv = ['probe.py', '--base', base, '--transport-ip', '172.20.0.7', '--client-id', 'synthetic-staging-probe', '--client-secret-file', '/private/synthetic']
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(Path, 'read_text', return_value=SECRET) as secret_read, \
                mock.patch.object(probe.urllib.request, 'build_opener', return_value=self.fixture), \
                contextlib.redirect_stdout(self.output):
            try:
                probe.main()
                error = None
            except Exception as exc:
                error = exc
            return error, secret_read

    def assert_private(self, error):
        output = self.output.getvalue() + (str(error) if error else '')
        for value in (SECRET, 'synthetic-access-token', 'synthetic-code', 'synthetic-csrf'):
            self.assertNotIn(value, output)

    def test_complete_flow_verifies_signature_pkce_revocation_and_logout(self):
        error, _ = self.execute()
        self.assertIsNone(error)
        for stage in ('signed-id-token-identity: pass', 'wrong-origin-rejected: HTTP 403',
                      'revocation: HTTP 200', 'session-revoked: HTTP 401', 'result: pass'):
            self.assertIn(stage, self.output.getvalue())
        self.assert_private(error)

    def test_bad_subject_fails_without_revealing_secrets(self):
        self.fixture.break_token = True
        error, _ = self.execute()
        self.assertIsInstance(error, probe.ProbeFailure)
        self.assertIn('ID token claims mismatch', str(error))
        self.assert_private(error)

    def test_bad_signature_fails_without_revealing_secrets(self):
        self.fixture.break_signature = True
        error, _ = self.execute()
        self.assertEqual(type(error).__name__, 'InvalidSignature')
        self.assertNotIn('signed-id-token-identity: pass', self.output.getvalue())
        self.assert_private(error)

    def test_unexpected_exception_details_are_not_printed(self):
        argv = ['probe.py', '--base', ORIGIN, '--transport-ip', '172.20.0.7', '--client-id', 'synthetic-staging-probe', '--client-secret-file', '/private/synthetic']
        opener = mock.Mock()
        opener.open.side_effect = RuntimeError(SECRET + ' synthetic-access-token')
        stderr = io.StringIO()
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(Path, 'read_text', return_value=SECRET), \
                mock.patch('urllib.request.build_opener', return_value=opener), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_result:
                runpy.run_path(str(PROBE), run_name='__main__')
        self.assertEqual(exit_result.exception.code, 1)
        self.assertIn('result: failed; RuntimeError', stderr.getvalue())
        self.assertNotIn(SECRET, stderr.getvalue() + self.output.getvalue())
        self.assertNotIn('synthetic-access-token', stderr.getvalue() + self.output.getvalue())

    def test_external_redirect_is_rejected_without_following(self):
        self.fixture.redirect_external = True
        error, _ = self.execute()
        self.assertIsInstance(error, probe.ProbeFailure)
        self.assertIn('unexpected authorization redirect', str(error))
        self.assertFalse(any('example.invalid' in url for url in self.fixture.urls))
        self.assert_private(error)

    def test_transport_routes_to_container_with_logical_host_header(self):
        sock = mock.Mock()
        sock.makefile.return_value = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok')
        with mock.patch('socket.create_connection', return_value=sock) as connect:
            opener = probe.urllib.request.build_opener(probe.urllib.request.ProxyHandler({}),
                                                       probe.ContainerHTTPHandler('172.20.0.7'), probe.NoRedirect())
            with opener.open(ORIGIN + '/oidc/test', timeout=15) as response:
                self.assertEqual(response.read(), b'ok')
        self.assertEqual(connect.call_args.args[0], ('172.20.0.7', 8787))
        wire = b''.join(call.args[0] for call in sock.sendall.call_args_list)
        self.assertIn(b'Host: 127.0.0.1:8787\r\n', wire)
        self.assertNotIn(b'Host: 172.20.0.7', wire)

    def test_unsafe_transport_ips_rejected_before_secret_read(self):
        for address in ('127.0.0.1', '0.0.0.0', '224.0.0.1', '169.254.1.1',
                        '8.8.8.8', '::1', '172.20.0.0', '172.20.0.255', 'localhost'):
            argv = ['probe.py', '--base', ORIGIN, '--transport-ip', address,
                    '--client-id', 'synthetic-staging-probe', '--client-secret-file', '/private/synthetic']
            with self.subTest(address=address), mock.patch.object(sys, 'argv', argv), \
                    mock.patch.object(Path, 'read_text') as secret_read:
                with self.assertRaises(probe.ProbeFailure):
                    probe.main()
                secret_read.assert_not_called()

    def test_handler_rejects_nonlogical_destination(self):
        handler = probe.ContainerHTTPHandler('172.20.0.7')
        for url in ('http://172.20.0.7:8787/oidc/test', 'http://127.0.0.1:8765/oidc/test'):
            with self.subTest(url=url), mock.patch.object(handler, 'do_open') as open_http:
                with self.assertRaises(probe.ProbeFailure):
                    handler.http_open(probe.urllib.request.Request(url))
                open_http.assert_not_called()

    def test_invalid_origins_rejected_before_secret_file_read(self):
        for base in ('https://example.invalid', 'http://127.0.0.1:8765@evil.invalid:1234',
                     'http://localhost:1234', 'http://127.0.0.1:1234/path'):
            with self.subTest(base=base):
                error, secret_read = self.execute(base)
                self.assertIsInstance(error, probe.ProbeFailure)
                self.assertIn('base must be the exact loopback origin', str(error))
                secret_read.assert_not_called()
                self.assert_private(error)


if __name__ == '__main__':
    unittest.main()
