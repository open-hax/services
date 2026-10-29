"""Prepare the minimal OIDC matcher change for an observed incumbent Caddyfile.

This is a pure byte transform, not a Caddy parser, host writer or authorization
surface. A source-owned deployment must bind the expected digest to its reviewed
plan, validate the complete candidate with the installed Caddy adapter, retain a
verified rollback copy and serialize any later publication/reload.
"""
import hashlib
import re


OLD = (b'\t@axxium_api {\n'
       b'\t\thost axxium.promethean.rest\n'
       b'\t\tpath / /health /portal/* /api/auth/* /api/actors* /api/entities* /api/atproto/*\n'
       b'\t}\n')
NEW = OLD.replace(b'/api/atproto/*\n', b'/api/atproto/* /oidc/*\n')
SITE = b'axxium.promethean.rest, *.axxium.promethean.rest {\n'
UPSTREAM = (b'\thandle @axxium_api {\n'
            b'\t\treverse_proxy axxium:8787 {\n'
            b'\t\t\theader_up -x-open-hax-bridge-auth\n'
            b'\t\t}\n\t}\n')
MAINTENANCE = (b'\thandle @axxium_api {\n'
               b'\t\trespond "Axxium sign-in is updating" 503\n\t}\n')


class IngressRefused(ValueError):
    pass


def prepare(config, expected_sha256):
    """Append only /oidc/* to the exact existing authority matcher.

    An expected hash supplied by arbitrary input is not production admission;
    the trusted caller must obtain it from the observed, reviewed host plan.
    Missing, duplicate, changed and already-updated matchers refuse.
    """
    if (not isinstance(config, bytes) or len(config) > 2 * 1024 * 1024
            or not isinstance(expected_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_sha256)
            or hashlib.sha256(config).hexdigest() != expected_sha256
            or config.count(SITE) != 1 or config.count(OLD) != 1
            or NEW in config):
        raise IngressRefused('Observed Axxium ingress does not match the reviewed plan')
    return config.replace(OLD, NEW, 1)


def maintenance(config, expected_sha256):
    """Fence only the existing Axxium API while preserving the PDS fallback."""
    prepare(config, expected_sha256)
    if config.count(UPSTREAM) != 1 or MAINTENANCE in config:
        raise IngressRefused('Observed authority upstream does not match the reviewed plan')
    return config.replace(UPSTREAM, MAINTENANCE, 1)
