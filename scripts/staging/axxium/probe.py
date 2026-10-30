#!/usr/bin/env python3
"""Exercise a disposable loopback Axxium authority using synthetic identities.

The caller owns and destroys the isolated database and application container.
Only stage names and HTTP status codes are printed; secrets and tokens stay in memory.
"""
import argparse
import base64
import hashlib
import http.client
import ipaddress
import http.cookiejar
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class ProbeFailure(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fail(message):
    raise ProbeFailure(message)


def check(condition, message):
    if not condition:
        fail(message)


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--client-id', required=True)
    parser.add_argument('--transport-ip', required=True, help='verified IPv4 address of the disposable app container')
    parser.add_argument('--client-secret-file', required=True)
    return parser.parse_args()



class ContainerHTTPHandler(urllib.request.HTTPHandler):
    """Connect to an explicit Docker address while retaining the logical host."""
    def __init__(self, transport_ip):
        super().__init__()
        self.transport_ip = transport_ip

    def http_open(self, request):
        check(request.host == '127.0.0.1:8787' and not request._tunnel_host,
              'transport: unexpected logical host')
        physical_ip = self.transport_ip

        def connection(host, **kwargs):
            check(host == '127.0.0.1:8787', 'transport: unexpected connection host')
            conn = http.client.HTTPConnection(host, **kwargs)
            create_connection = conn._create_connection

            def connect_to_container(address, timeout=None, source_address=None):
                check(address == ('127.0.0.1', 8787), 'transport: unexpected connection target')
                return create_connection((physical_ip, 8787), timeout, source_address)

            conn._create_connection = connect_to_container
            return conn

        return self.do_open(connection, request)


def checked_transport_ip(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        fail('transport IP must be a private IPv4 address')
    private_networks = (ipaddress.IPv4Network('10.0.0.0/8'),
                        ipaddress.IPv4Network('172.16.0.0/12'),
                        ipaddress.IPv4Network('192.168.0.0/16'))
    check(any(address in network for network in private_networks),
          'transport IP must be a private IPv4 address')
    check(address.packed[-1] not in (0, 255), 'transport IP cannot be a network or broadcast address')
    return str(address)

def main():
    args = parse_args()
    u = urllib.parse.urlsplit(args.base)
    check(u.scheme == 'http' and u.hostname == '127.0.0.1' and u.port is not None
          and u.username is None and u.password is None and u.path in ('', '/') and u.port == 8787
          and not u.query and not u.fragment, 'base must be the exact loopback origin http://127.0.0.1:8787')
    transport_ip = checked_transport_ip(args.transport_ip)
    origin = f'http://127.0.0.1:{u.port}'
    issuer = origin + '/oidc'
    redirect = 'http://127.0.0.1:8765/callback'
    secret = Path(args.client_secret_file).read_text(encoding='utf-8').rstrip('\r\n')
    check(bool(secret), 'empty client secret')
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ContainerHTTPHandler(transport_ip),
                                         urllib.request.HTTPCookieProcessor(jar), NoRedirect())

    def request(method, path, data=None, json_data=None, request_origin=None):
        url = urllib.parse.urljoin(origin + '/', path)
        parsed = urllib.parse.urlsplit(url)
        check(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
              and parsed.port == u.port and parsed.username is None and parsed.password is None
              and not parsed.fragment, 'request destination rejected')
        headers = {'Accept': 'application/json, text/html', 'Host': '127.0.0.1:8787'}
        if request_origin is not None:
            headers['Origin'] = request_origin
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if json_data is not None:
            body = json.dumps(json_data).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.code, response.headers, response.read()

    def expect(stage, status, expected):
        check(status in expected, f'{stage}: HTTP {status}, expected {"/".join(map(str, expected))}')
        print(f'{stage}: HTTP {status}', flush=True)

    def endpoint(doc, name):
        value = doc[name]
        parsed = urllib.parse.urlsplit(value)
        check(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
              and parsed.port == u.port and parsed.username is None and parsed.password is None
              and parsed.path.startswith('/oidc/') and not parsed.query and not parsed.fragment,
              f'{name}: invalid destination')
        return value

    email = f'isolated-probe-{secrets.token_hex(12)}@example.invalid'
    password = secrets.token_urlsafe(32)
    status, _, body = request('POST', '/api/auth/signup', json_data={
        'email': email, 'password': password, 'display_name': 'Disposable test'}, request_origin=origin)
    expect('signup', status, (200,))
    actor = json.loads(body)['actor']['id']
    check(isinstance(actor, str) and actor.startswith('actor_'), 'signup: invalid actor')
    status, _, _ = request('POST', '/api/auth/logout', json_data={}, request_origin=origin)
    expect('signup-session-logout', status, (200,))
    status, _, body = request('POST', '/api/auth/login', json_data={'email': email, 'password': password}, request_origin=origin)
    expect('password-login', status, (200,))
    check(json.loads(body)['actor']['id'] == actor, 'login actor mismatch')
    status, _, body = request('GET', '/api/auth/me')
    expect('session-readback', status, (200,))
    check(json.loads(body)['actor']['id'] == actor, 'session actor mismatch')
    status, _, body = request('GET', '/oidc/.well-known/openid-configuration')
    expect('discovery', status, (200,))
    discovery = json.loads(body)
    check(discovery['issuer'] == issuer and 'S256' in discovery['code_challenge_methods_supported'], 'discovery issuer or PKCE mismatch')
    auth = endpoint(discovery, 'authorization_endpoint')
    token_endpoint = endpoint(discovery, 'token_endpoint')
    jwks_endpoint = endpoint(discovery, 'jwks_uri')
    introspection = endpoint(discovery, 'introspection_endpoint')
    revocation = endpoint(discovery, 'revocation_endpoint')
    verifier = secrets.token_urlsafe(48)
    nonce, state = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    params = {'client_id': args.client_id, 'redirect_uri': redirect, 'response_type': 'code',
              'scope': 'openid profile', 'nonce': nonce, 'state': state,
              'code_challenge_method': 'S256', 'code_challenge': b64(hashlib.sha256(verifier.encode()).digest())}
    url = auth + '?' + urllib.parse.urlencode(params)
    code = None
    for _ in range(20):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) == ('http', '127.0.0.1', 8765, '/callback'):
            check(parsed.username is None and parsed.password is None and not parsed.fragment, 'invalid callback')
            query = urllib.parse.parse_qs(parsed.query)
            check(query.get('state') == [state] and len(query.get('code', [])) == 1, 'authorization callback mismatch')
            code = query['code'][0]
            break
        check(parsed.path.startswith('/oidc/'), 'unexpected authorization redirect')
        status, headers, body = request('GET', url)
        if status == 200 and parsed.path.startswith('/oidc/interaction/'):
            match = re.search(rb"name=csrf value='([^']+)'", body)
            check(match is not None, 'consent CSRF missing')
            csrf = match.group(1).decode()
            # Wrong Origin is a string sent only to loopback; no request is made to it.
            rejected, _, _ = request('POST', url, {'csrf': csrf, 'decision': 'approve'}, request_origin='http://invalid.example')
            expect('wrong-origin-rejected', rejected, (403,))
            status, headers, _ = request('POST', url, {'csrf': csrf, 'decision': 'approve'}, request_origin=origin)
            expect('consent', status, (302, 303))
        location = headers.get('Location')
        check(bool(location), f'authorization: HTTP {status} without redirect')
        url = urllib.parse.urljoin(origin + '/', location)
    check(code is not None, 'authorization code missing')
    print('authorization-code-pkce: pass', flush=True)
    status, _, body = request('POST', token_endpoint, {'client_id': args.client_id, 'client_secret': secret,
        'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier, 'redirect_uri': redirect})
    expect('token-exchange', status, (200,))
    tokens = json.loads(body)
    head, payload, signature = tokens['id_token'].split('.')
    claims, header = json.loads(decode(payload)), json.loads(decode(head))
    check(claims.get('sub') == actor and claims.get('iss') == issuer and claims.get('aud') == args.client_id
          and claims.get('nonce') == nonce and claims.get('exp', 0) > time.time(), 'ID token claims mismatch')
    check(header.get('alg') == 'RS256', 'ID token algorithm mismatch')
    status, _, body = request('GET', jwks_endpoint)
    expect('jwks', status, (200,))
    keys = [key for key in json.loads(body)['keys'] if key.get('kid') == header.get('kid') and key.get('kty') == 'RSA']
    check(len(keys) == 1, 'ID token signing key mismatch')
    integer = lambda s: int.from_bytes(decode(s), 'big')
    public_key = rsa.RSAPublicNumbers(integer(keys[0]['e']), integer(keys[0]['n'])).public_key()
    public_key.verify(decode(signature), (head + '.' + payload).encode(), padding.PKCS1v15(), hashes.SHA256())
    print('signed-id-token-identity: pass', flush=True)

    def inspect_access():
        status, _, body = request('POST', introspection, {'client_id': args.client_id,
            'client_secret': secret, 'token': tokens['access_token']})
        expect('introspection', status, (200,))
        return json.loads(body)

    check(inspect_access().get('active') is True, 'access token not active')
    status, _, _ = request('POST', revocation, {'client_id': args.client_id,
        'client_secret': secret, 'token': tokens['access_token']})
    expect('revocation', status, (200,))
    check(inspect_access().get('active') is False, 'revoked token still active')
    status, _, _ = request('POST', '/api/auth/logout', json_data={}, request_origin=origin)
    expect('logout', status, (200,))
    status, _, _ = request('GET', '/api/auth/me')
    expect('session-revoked', status, (401,))
    print('result: pass', flush=True)


if __name__ == '__main__':
    try:
        main()
    except ProbeFailure as exc:
        print(f'result: failed; {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Exception strings can contain URLs, tokens, or other sensitive values.
        print(f'result: failed; {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
