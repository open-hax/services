#!/usr/bin/env bash
# Required read-only post-deploy gate. It consumes no private environment or
# authority files and runs inside the admitted transaction before its receipt.
set -euo pipefail
service_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 - "$service_dir/minimal-production-plan.json" <<'PY'
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode, urlsplit

SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
IMAGE = 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
ORIGIN = 'https://axxium.promethean.rest'
ISSUER = ORIGIN + '/oidc'
APP, DB, PDS, PROXY = 'axxium-axxium-1', 'axxium-axxium-db-1', 'axxium-pds-1', 'caddy-caddy-1'
FORMAT = ('{"id":{{json .Id}},"image":{{json .Image}},'
          '"running":{{json .State.Running}},"health":'
          '{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
          '"proxy_ip":{{with index .NetworkSettings.Networks "open-hax"}}'
          '{{json .IPAddress}}{{else}}null{{end}}}')

class Refused(Exception):
    pass

def require(condition, message):
    if not condition:
        raise Refused(message)

def unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'Duplicate JSON key')
        out[key] = value
    return out

def document(raw):
    require(len(raw) <= 1024 * 1024, 'Oversized verification response')
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise Refused('Invalid verification JSON') from None
    require(isinstance(value, dict), 'Verification response is not an object')
    return value

def command(args):
    # Command diagnostics can include response bodies. Never echo them.
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Verification command unavailable or timed out') from None
    require(p.returncode == 0, 'Verification command failed')
    require(len(p.stdout) <= 1024 * 1024, 'Oversized verification response')
    return p.stdout

def http(url, *, form=None, status=200):
    args = ['curl', '--silent', '--show-error', '--max-time', '10', '--max-filesize', '1048576',
            '--proto', '=http,https', '--write-out', '\n%{http_code}']
    if form is not None:
        args += ['--request', 'POST', '--header', 'Content-Type: application/x-www-form-urlencoded',
                 '--data', urlencode(form)]
    raw = command(args + [url])
    body, separator, code = raw.rpartition(b'\n')
    require(separator and code == str(status).encode(), 'Unexpected HTTP verification status')
    return body

def inspect(name):
    return document(command(['docker', 'inspect', '--format', FORMAT, name]))

def containers(plan):
    expected = plan['expected']
    selected = {name: inspect(name) for name in (APP, DB, PDS, PROXY)}
    app = selected[APP]
    require(app.get('image') == IMAGE and re.fullmatch('[0-9a-f]{64}', app.get('id', '')),
            'Application does not use the admitted image')
    for name, prefix in ((DB, 'db'), (PDS, 'pds'), (PROXY, 'caddy')):
        d = selected[name]
        require(d.get('id') == expected[prefix + '_container_id']
                and d.get('image') == expected[prefix + '_image_id'],
                'Preserved dependency identity differs')
    for name, d in selected.items():
        require(d.get('running') is True, 'Required container is not running')
        if name != PROXY:
            require(d.get('health') == 'healthy', 'Required container is not healthy')
    require(selected[PROXY].get('proxy_ip') == expected['caddy_proxy_ip'], 'Trusted proxy address differs')
    return selected

def discovery(raw):
    d = document(raw)
    require(d.get('issuer') == ISSUER, 'OIDC issuer differs')
    for key, required in (('code_challenge_methods_supported', 'S256'),
                          ('response_types_supported', 'code'),
                          ('grant_types_supported', 'authorization_code'),
                          ('token_endpoint_auth_methods_supported', 'client_secret_post')):
        require(isinstance(d.get(key), list) and required in d[key], 'Required OIDC capability missing')
    for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri', 'introspection_endpoint'):
        endpoint = d.get(key)
        require(isinstance(endpoint, str), 'Required OIDC endpoint missing')
        p = urlsplit(endpoint)
        require(p.scheme == 'https' and p.netloc == 'axxium.promethean.rest'
                and p.path.startswith('/oidc/') and not p.query and not p.fragment,
                'OIDC endpoint leaves the production authority')
    return d

def main():
    plan = document(Path(sys.argv[1]).read_bytes())
    require(plan.get('schema') == 'axxium-minimal-production/v1' and plan.get('source_sha') == SOURCE
            and plan.get('image_id') == IMAGE and plan.get('authority', {}).get('issuer') == ISSUER
            and plan.get('authority', {}).get('client_id') == 'knoxx-colab', 'Fixed verification plan differs')
    before = containers(plan)
    for origin in ('http://127.0.0.1:8787', ORIGIN):
        require(document(http(origin + '/health')).get('status') == 'ok', 'Identity health failed')
        metadata = document(http(origin + '/api/auth/atproto/client-metadata.json'))
        require(metadata.get('client_id') == ORIGIN + '/api/auth/atproto/client-metadata.json'
                and metadata.get('redirect_uris') == [ORIGIN + '/api/auth/atproto/callback'],
                'AT Protocol client metadata differs')
    for origin in ('http://127.0.0.1:3000', ORIGIN):
        health = document(http(origin + '/xrpc/_health'))
        require(isinstance(health.get('version'), str) and bool(health['version']), 'Preserved PDS health failed')
        require(document(http(origin + '/xrpc/com.atproto.server.describeServer')).get('did')
                == 'did:web:axxium.promethean.rest', 'Preserved PDS authority differs')
    # Axxium trusts the exact Caddy address. Loopback with a spoofed forwarding
    # header is intentionally rejected, so private discovery originates there.
    private = discovery(command(['docker', 'exec', PROXY, 'wget', '-q', '-O', '-', '-T', '10',
        '--header', 'X-Forwarded-Proto: https', '--header', 'Host: axxium.promethean.rest',
        'http://axxium:8787/oidc/.well-known/openid-configuration']))
    public = discovery(http(ISSUER + '/.well-known/openid-configuration'))
    require(private == public, 'Public and private OIDC metadata differ')
    jwks = document(http(public['jwks_uri']))
    keys = jwks.get('keys')
    require(isinstance(keys, list) and bool(keys), 'Public signing keys missing')
    for key in keys:
        require(isinstance(key, dict) and key.get('kty') == 'RSA' and key.get('alg') == 'RS256'
                and isinstance(key.get('kid'), str) and bool(key['kid'])
                and all(isinstance(key.get(k), str) and re.fullmatch('[A-Za-z0-9_-]+', key[k]) for k in ('n', 'e'))
                and not set(key).intersection({'d', 'p', 'q', 'dp', 'dq', 'qi', 'oth'}), 'Invalid public signing key')
    # Fixed invalid values prove the public endpoint refuses unauthenticated
    # introspection. This does not claim a real token exchange or human login.
    http(public['introspection_endpoint'], form={
        'token': 'axxium-deployment-verification-invalid-token',
        'client_id': 'knoxx-colab',
        'client_secret': 'axxium-deployment-verification-invalid-secret'}, status=401)
    require(containers(plan) == before, 'Runtime identity changed during verification')
    print('axxium: admitted image, preserved dependencies, identity/PDS health, OIDC metadata/JWKS and introspection denial verified')

try:
    main()
except (Refused, OSError, KeyError, TypeError, ValueError):
    # No raw command response, key material, filesystem exception or credential.
    print('axxium: required read-only deployment verification failed', file=sys.stderr)
    sys.exit(1)
PY
