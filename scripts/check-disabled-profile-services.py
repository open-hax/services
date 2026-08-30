#!/usr/bin/env python3
"""Validate service-local retirement lists for disabled Compose profiles."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def dependency_required(dependency: Any) -> bool:
    """Return whether a Compose depends_on entry makes startup mandatory."""
    return not (isinstance(dependency, dict) and dependency.get("required") is False)


def validate_names(names: list[str], services: dict[str, Any]) -> list[str]:
    """Return contract violations without coupling validation to filesystem I/O."""
    failures: list[str] = []
    seen: set[str] = set()
    disabled = set(names)

    for name in names:
        if not SERVICE_NAME.fullmatch(name):
            failures.append(f"invalid Compose service name: {name!r}")
            continue
        if name in seen:
            failures.append(f"duplicate disabled profile service: {name}")
            continue
        seen.add(name)

        config = services.get(name)
        if not isinstance(config, dict):
            failures.append(f"disabled profile service is not declared: {name}")
            continue
        if not config.get("profiles"):
            failures.append(f"disabled service has no Compose profile: {name}")

        for consumer_name, consumer in services.items():
            if consumer_name in disabled or not isinstance(consumer, dict):
                continue
            if consumer.get("profiles"):
                continue
            dependencies = consumer.get("depends_on") or {}
            if isinstance(dependencies, list):
                dependency = name if name in dependencies else None
            elif isinstance(dependencies, dict):
                dependency = dependencies.get(name)
            else:
                dependency = None
            if dependency is not None and dependency_required(dependency):
                failures.append(
                    f"active service {consumer_name} requires disabled profile service {name}"
                )

    return failures


def marker_names(path: Path) -> list[str]:
    """Read one service name per line, allowing comments and blank lines."""
    names: list[str] = []
    for raw in path.read_text().splitlines():
        value = raw.split("#", 1)[0].strip()
        if value:
            names.append(value)
    return names


def scan(root: Path = ROOT) -> list[str]:
    """Validate every service directory that opts into profile retirement."""
    failures: list[str] = []
    markers = sorted(
        (root / "digitalocean" / "services").glob("*/disabled-profile-services")
    )
    for marker in markers:
        compose_path = marker.with_name("compose.yaml")
        try:
            document = yaml.safe_load(compose_path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            failures.append(f"{compose_path}: cannot load Compose model: {exc}")
            continue
        services = document.get("services")
        if not isinstance(services, dict):
            failures.append(f"{compose_path}: missing services map")
            continue
        names = marker_names(marker)
        if not names:
            failures.append(f"{marker}: marker must name at least one service")
            continue
        failures.extend(f"{marker}: {item}" for item in validate_names(names, services))
    return failures


def self_test() -> None:
    """Exercise the safe and rejected dependency/profile boundaries."""
    services = {
        "app": {
            "depends_on": {
                "sandbox": {"condition": "service_healthy", "required": False}
            }
        },
        "sandbox": {"profiles": ["sandbox"]},
    }
    assert validate_names(["sandbox"], services) == []
    assert validate_names(["missing"], services) == [
        "disabled profile service is not declared: missing"
    ]
    assert validate_names(["app"], services) == [
        "disabled service has no Compose profile: app"
    ]
    required = {
        **services,
        "app": {"depends_on": {"sandbox": {"condition": "service_healthy"}}},
    }
    assert validate_names(["sandbox"], required) == [
        "active service app requires disabled profile service sandbox"
    ]
    print("disabled profile service validator self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    failures = scan()
    if failures:
        for failure in failures:
            print(f"::error::{failure}")
        return 1
    print("disabled Compose profile retirement contracts valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
