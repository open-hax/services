#!/usr/bin/env python3
"""Prepare a private candidate root Caddyfile from a pinned incumbent copy."""

import argparse
import hashlib
import os
from pathlib import Path
import tempfile


SNIPPET = Path(__file__).with_name("axxium-production.caddy")
MAX_ROOT_BYTES = 2 * 1024 * 1024
SITE = b"axxium.promethean.rest, *.axxium.promethean.rest {\n"
OLD_UPSTREAM = b"reverse_proxy axxium:8787 {"
NEW_UPSTREAM = b"reverse_proxy axxium-ingress:8787 {"
INCUMBENT_SITE_SHA256 = "963bae7ca2ad3b8fcc6a3c591e33fe384b1689c819b1c14ea382876d293dbce5"
MANAGED_IMPORT = b"\nimport /etc/caddy/managed/*.caddy\n"
PDS_ASK = b"\t\task http://axxium-pds:3000/tls-check\n"


def render(root, expected_root_sha256, snippet):
    if len(root) > MAX_ROOT_BYTES or hashlib.sha256(root).hexdigest() != expected_root_sha256:
        raise ValueError("Incumbent root differs from the pinned input")
    incumbent_site = snippet.replace(NEW_UPSTREAM, OLD_UPSTREAM, 1)
    if (snippet.count(NEW_UPSTREAM) != 1
            or hashlib.sha256(incumbent_site).hexdigest() != INCUMBENT_SITE_SHA256):
        raise ValueError("Tracked snippet must preserve the incumbent route block with only the fixed upstream changed")
    if (root.count(SITE) != 1 or root.count(incumbent_site) != 1
            or root.count(MANAGED_IMPORT) != 1 or root.count(PDS_ASK) != 1):
        raise ValueError("Incumbent route block, managed import or PDS TLS permission check differs")
    return root.replace(incumbent_site, b"", 1)


def write_new_private(path, data):
    with tempfile.NamedTemporaryFile(prefix=".axxium-ingress-", dir=path.parent) as staged:
        staged.write(data)
        staged.flush()
        os.fsync(staged.fileno())
        os.link(staged.name, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incumbent", type=Path, required=True)
    parser.add_argument("--incumbent-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        with arguments.incumbent.open("rb") as source:
            root = source.read(MAX_ROOT_BYTES + 1)
        candidate = render(root, arguments.incumbent_sha256, SNIPPET.read_bytes())
        write_new_private(arguments.output, candidate)
    except (OSError, ValueError) as error:
        parser.exit(2, f"Axxium ingress candidate refused: {error}\n")
    print(f"Candidate root SHA256 {hashlib.sha256(candidate).hexdigest()}")


if __name__ == "__main__":
    main()
