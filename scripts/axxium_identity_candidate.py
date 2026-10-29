#!/usr/bin/env python3
"""Build a review-only production Caddy candidate from exact incumbent bytes.

This script never installs, replaces or reloads ingress. Configuration can
contain private operational values, so output is exclusive and mode 0600.
"""
import argparse
import hashlib
import ipaddress
import os
from pathlib import Path

OLD = """\t@axxium_api {
\t\thost axxium.promethean.rest
\t\tpath / /health /portal/* /api/auth/* /api/actors* /api/entities* /api/atproto/*
\t}
\thandle @axxium_api {
\t\treverse_proxy axxium:8787 {"""
NEW = """\t@axxium_api {
\t\thost axxium.promethean.rest
\t\tpath / /health /portal/* /api/auth/* /api/actors* /api/entities* /api/atproto/* /oidc /oidc/*
\t}
\thandle @axxium_api {
\t\treverse_proxy axxium-identity-ingress:8787 {"""
SUBNET = ipaddress.ip_network("172.30.115.0/29")


def candidate(source: bytes, expected_sha256: str) -> bytes:
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise ValueError("Incumbent Caddyfile does not match the selected baseline")
    text = source.decode("utf-8")
    if text.count(OLD) != 1 or text.count("axxium.promethean.rest, *.axxium.promethean.rest {") != 1:
        raise ValueError("Axxium host or incumbent route is missing or ambiguous")
    if "reverse_proxy axxium-identity-ingress:8787" in text:
        raise ValueError("Identity ingress appears already configured")
    return text.replace(OLD, NEW, 1).encode("utf-8")


def subnet_available(destinations: list[str]) -> bool:
    """Ignore only the default route; any other overlapping route refuses."""
    for dest in destinations:
        if dest == "default":
            continue
        route = ipaddress.ip_network(dest, strict=False)
        if route.version == 4 and route.overlaps(SUBNET):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = candidate(args.source.read_bytes(), args.expected_sha256)
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(result)
    print("Review candidate created; sha256=" + hashlib.sha256(result).hexdigest())


if __name__ == "__main__":
    main()
