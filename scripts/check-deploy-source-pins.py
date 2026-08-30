#!/usr/bin/env python3
"""Keep production source pins immutable and owned by one deploy entry point."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHA = re.compile(r"^[0-9a-f]{40}$")
PIN_FILES = (
    "deploy-stack.yml",
    "deploy-stack-chain.yml",
    "build-knoxx-devtools.yml",
)


@dataclass(frozen=True)
class SourcePin:
    input_name: str
    consumer_job: str
    consumer_workflow: str


PINS = (
    SourcePin("proxx_ref", "build-proxx", "deploy-stack-chain.yml"),
    SourcePin("openplanner_ref", "build-knoxx-devtools", "deploy-stack-chain.yml"),
)


def load(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def workflow_inputs(document: dict, trigger: str) -> dict:
    return document["on"][trigger]["inputs"]


def require_forwarded_input(
    errors: list[str],
    document: dict,
    trigger: str,
    path: str,
    input_name: str,
) -> None:
    spec = workflow_inputs(document, trigger)[input_name]
    if spec.get("required") != "true" or "default" in spec:
        errors.append(f"{path} {trigger}.{input_name} must be required with no default")


def production_fallback(expression: str, input_name: str) -> str | None:
    pattern = re.compile(
        rf"inputs\.{re.escape(input_name)}\s*\|\|\s*'([0-9a-f]{{40}})'"
    )
    match = pattern.search(expression)
    return match.group(1) if match else None


def validate() -> list[str]:
    errors: list[str] = []
    entry = load("deploy-stack.yml")
    chain = load("deploy-stack-chain.yml")
    resolved_pins: dict[str, str] = {}

    for pin in PINS:
        entry_input = workflow_inputs(entry, "workflow_dispatch")[pin.input_name]
        if "default" in entry_input:
            errors.append(
                f"deploy-stack.yml must not duplicate the production {pin.input_name} pin"
            )

        expression = entry["jobs"]["deploy"]["with"][pin.input_name]
        fallback = production_fallback(expression, pin.input_name)
        if fallback is None or not SHA.fullmatch(fallback):
            errors.append(
                f"deploy-stack.yml must provide one immutable {pin.input_name} SHA fallback"
            )
        else:
            resolved_pins[pin.input_name] = fallback

        require_forwarded_input(
            errors,
            chain,
            "workflow_call",
            "deploy-stack-chain.yml",
            pin.input_name,
        )
        forwarded = chain["jobs"][pin.consumer_job]["with"][pin.input_name]
        if forwarded != f"${{{{ inputs.{pin.input_name} }}}}":
            errors.append(
                f"{pin.consumer_workflow} must forward {pin.input_name} unchanged"
            )

    builder = load("build-knoxx-devtools.yml")
    for trigger in ("workflow_call", "workflow_dispatch"):
        require_forwarded_input(
            errors,
            builder,
            trigger,
            "build-knoxx-devtools.yml",
            "openplanner_ref",
        )

    pin_literals: list[str] = []
    for name in PIN_FILES:
        pin_literals.extend(
            re.findall(r"\b[0-9a-f]{40}\b", (WORKFLOWS / name).read_text())
        )

    for input_name, pin_sha in resolved_pins.items():
        if pin_literals.count(pin_sha) != 1:
            errors.append(
                f"the production {input_name} SHA must occur exactly once across the deploy chain"
            )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(
            "\n".join(f"deploy source pin error: {error}" for error in errors),
            file=sys.stderr,
        )
        return 1
    print("deploy source pin ownership is singular and immutable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
