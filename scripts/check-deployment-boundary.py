#!/usr/bin/env python3
"""Reject legacy VPS deployment contracts outside preserved history."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


RULES = (
    Rule("retired deployment workflow", re.compile(r"deploy-promethean\.yml")),
    Rule("legacy SSH identity", re.compile(r"\berror@proxx\.promethean\.rest\b")),
    Rule("legacy runtime root", re.compile(r"/home/error(?:/|\b)")),
    Rule(
        "trust-on-first-use SSH policy",
        re.compile(r"StrictHostKeyChecking\s*(?:=|\s)\s*accept-new", re.IGNORECASE),
    ),
    Rule("unverified SSH host-key discovery", re.compile(r"\bssh-keyscan\b")),
    Rule(
        "legacy SSH host default",
        re.compile(r"PROMETHEAN_SSH_HOST[^\n]{0,200}proxx\.promethean\.rest"),
    ),
    Rule(
        "legacy SSH user default",
        re.compile(r"PROMETHEAN_SSH_USER[^\n]{0,200}(?:\|\||:-)[^\n]{0,80}\berror\b"),
    ),
)

HISTORICAL_PREFIXES = (
    Path("docs/history"),
    Path("docs/reports"),
)
HISTORICAL_FILES = {Path("receipts.edn")}
SELF = Path("scripts/check-deployment-boundary.py")


def repository_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(root)
        if relative == SELF or relative in HISTORICAL_FILES:
            continue
        if any(relative == prefix or prefix in relative.parents for prefix in HISTORICAL_PREFIXES):
            continue
        yield path


def violations(path: Path, text: str) -> Iterable[tuple[Rule, int, str]]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line):
                yield rule, line_number, line.strip()


def scan(root: Path) -> int:
    found = 0
    for path in repository_files(root):
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for rule, line_number, line in violations(path, text):
            relative = path.relative_to(root)
            print(
                f"::error file={relative},line={line_number}::{rule.name}: {line}",
                file=sys.stderr,
            )
            found += 1
    if found:
        print(f"deployment boundary rejected {found} legacy reference(s)", file=sys.stderr)
        return 1
    print("deployment boundary contains no active legacy VPS references")
    return 0


def self_test() -> int:
    bad = (
        "uses: open-hax/services/.github/workflows/deploy-promethean.yml@main",
        "ssh: error@proxx.promethean.rest",
        "runtimeRoot: /home/error/devel/services/knoxx",
        "StrictHostKeyChecking=accept-new",
        "ssh-keyscan -H host.example",
        "PROMETHEAN_SSH_HOST: ${{ vars.HOST || 'proxx.promethean.rest' }}",
        "PROMETHEAN_SSH_USER: ${{ vars.USER || 'error' }}",
    )
    safe = (
        "sshUser: deploy",
        "runtimeRoot: /srv/open-hax",
        "ssh-keygen -F 157.245.125.134",
        "StrictHostKeyChecking yes",
        "PROXX_PUBLIC_HOST: proxx.promethean.rest",
    )
    failures = []
    for sample in bad:
        if not list(violations(Path("bad"), sample)):
            failures.append(f"missed forbidden sample: {sample}")
    for sample in safe:
        if list(violations(Path("safe"), sample)):
            failures.append(f"rejected safe sample: {sample}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("deployment boundary classifier self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    return self_test() if args.self_test else scan(args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
