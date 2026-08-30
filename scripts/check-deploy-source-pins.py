#!/usr/bin/env python3
"""Keep the production source pin owned by one deploy entry point."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHA = re.compile(r"^[0-9a-f]{40}$")
FALLBACK = re.compile(r"inputs\.openplanner_ref\s*\|\|\s*'([0-9a-f]{40})'")


def load(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def workflow_inputs(document: dict, trigger: str) -> dict:
    return document["on"][trigger]["inputs"]


def require_forwarded_input(
    errors: list[str], document: dict, trigger: str, path: str
) -> None:
    spec = workflow_inputs(document, trigger)["openplanner_ref"]
    if spec.get("required") != "true" or "default" in spec:
        errors.append(f"{path} {trigger}.openplanner_ref must be required with no default")


def validate() -> list[str]:
    errors: list[str] = []
    entry = load("deploy-stack.yml")
    entry_input = workflow_inputs(entry, "workflow_dispatch")["openplanner_ref"]
    if "default" in entry_input:
        errors.append("deploy-stack.yml must not duplicate the production OpenPlanner pin")

    expression = entry["jobs"]["deploy"]["with"]["openplanner_ref"]
    match = FALLBACK.search(expression)
    if not match or not SHA.fullmatch(match.group(1)):
        errors.append("deploy-stack.yml must provide one immutable OpenPlanner SHA fallback")

    chain = load("deploy-stack-chain.yml")
    require_forwarded_input(errors, chain, "workflow_call", "deploy-stack-chain.yml")
    if chain["jobs"]["build-knoxx-devtools"]["with"]["openplanner_ref"] != "${{ inputs.openplanner_ref }}":
        errors.append("deploy-stack-chain.yml must forward openplanner_ref unchanged")

    builder = load("build-knoxx-devtools.yml")
    require_forwarded_input(errors, builder, "workflow_call", "build-knoxx-devtools.yml")
    require_forwarded_input(errors, builder, "workflow_dispatch", "build-knoxx-devtools.yml")

    pin_literals = []
    for name in ("deploy-stack.yml", "deploy-stack-chain.yml", "build-knoxx-devtools.yml"):
        pin_literals.extend(re.findall(r"\b[0-9a-f]{40}\b", (WORKFLOWS / name).read_text()))
    openplanner_pins = [pin for pin in pin_literals if pin == (match.group(1) if match else None)]
    if len(openplanner_pins) != 1:
        errors.append("the production OpenPlanner SHA must occur exactly once across the deploy chain")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("\n".join(f"deploy source pin error: {error}" for error in errors), file=sys.stderr)
        return 1
    print("deploy source pin ownership is singular and immutable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
