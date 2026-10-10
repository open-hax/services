#!/usr/bin/env python3
"""Run the shipped alias and workspace preparation against disposable Docker state.

Uses the existing nginx-unprivileged CI image as a shell host. All state is in
container tmpfs; this test never reads or changes a deployed workspace.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import textwrap
import uuid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = "nginxinc/nginx-unprivileged:1.27"


def docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments], check=check, text=True, capture_output=True,
        timeout=45,
    )


def shipped_commands() -> tuple[str, str]:
    dockerfile = (ROOT / "digitalocean/services/knoxx/Dockerfile.backend-sdk").read_text()
    lines = dockerfile.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("RUN test ! -e /srv/website-content")]
    if len(starts) != 1:
        raise RuntimeError("the guarded image alias RUN is missing")
    index = starts[0]
    instruction = [lines[index][4:]]
    while instruction[-1].endswith("\\"):
        index += 1
        instruction.append(lines[index])
    alias_command = "\n".join(instruction).replace("\\\n", " ")
    workflow = (ROOT / ".github/workflows/deploy-digitalocean.yml").read_text()
    begin = "          # BEGIN KNOXX PUBLICATION WORKSPACE\n"
    end = "          # END KNOXX PUBLICATION WORKSPACE"
    if workflow.count(begin) != 1 or workflow.count(end) != 1:
        raise RuntimeError("the exact deployment preparation block is missing or ambiguous")
    preparation = textwrap.dedent(workflow.split(begin, 1)[1].split(end, 1)[0])
    return alias_command, preparation


def run(image: str) -> None:
    alias, preparation = shipped_commands()
    metadata = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
    print(f"IMAGE {image} id={metadata}")
    container = f"knoxx-publication-mount-proof-{uuid.uuid4().hex[:12]}"
    assertions = 0
    try:
        docker(
            "run", "--detach", "--name", container, "--network", "none", "--read-only",
            "--tmpfs", "/srv:rw", "--tmpfs", "/app/workspace:rw",
            "--tmpfs", "/fixture:rw", "--entrypoint", "/bin/sh", image,
            "-c", "sleep 300",
        )
        def shell(code: str, uid: int = 0, check: bool = True):
            return docker("exec", "--user", str(uid), container, "/bin/sh", "-ec", code, check=check)

        def expect(label: str, code: str, outcome: bool, uid: int = 0):
            nonlocal assertions
            result = shell(code, uid=uid, check=False)
            if (result.returncode == 0) != outcome:
                raise AssertionError(f"{label}: status {result.returncode}; {result.stderr.strip()}")
            assertions += 1
            print(f"PASS {label}")

        # Reset only the synthetic tmpfs paths owned by this fixture.
        reset = "rm -rf /srv/website-content /app/workspace/.knoxx /fixture/*; chmod 777 /app/workspace /fixture"
        shell(reset)
        expect("absent image path installs alias", alias, True)
        expect("alias targets contained writer directory", 'test "$(readlink /srv/website-content)" = /app/workspace/.knoxx/website-content', True)
        for label, setup, preserved in (
            ("file", "printf incumbent > /srv/website-content", 'test "$(cat /srv/website-content)" = incumbent'),
            ("directory", "mkdir /srv/website-content; printf incumbent > /srv/website-content/keep", 'test "$(cat /srv/website-content/keep)" = incumbent'),
            ("dangling symlink", "ln -s /fixture/absent /srv/website-content", 'test "$(readlink /srv/website-content)" = /fixture/absent'),
        ):
            shell(reset + "; " + setup)
            expect(f"alias refuses incumbent {label}", alias, False)
            expect(f"alias preserves incumbent {label}", preserved, True)

        command = "service=knoxx; KNOXX_WORKSPACE_PATH=/app/workspace;\n" + preparation
        shell(reset)
        expect("fresh generated contracts preparation as UID 1000", command, True, uid=1000)
        expect("fresh generated contracts owner and access", 'test "$(stat -c %u /app/workspace/.knoxx/contracts)" = 1000; test -w /app/workspace/.knoxx/contracts; test -x /app/workspace/.knoxx/contracts', True, uid=1000)
        shell("printf preserved > /app/workspace/.knoxx/contracts/keep", uid=1000)
        expect("compatible incumbent preparation remains idempotent", command, True, uid=1000)
        expect("compatible incumbent bytes are preserved", 'test "$(cat /app/workspace/.knoxx/contracts/keep)" = preserved', True, uid=1000)
        expect("root deployment identity is refused", command, False)

        cases = (
            ("root-owned parent", "mkdir /app/workspace/.knoxx; chmod 777 /app/workspace/.knoxx; printf preserved > /app/workspace/.knoxx/keep", '/app/workspace/.knoxx/keep', "test ! -e /app/workspace/.knoxx/contracts"),
            ("root-owned contracts", "mkdir -p /app/workspace/.knoxx/contracts; chown 1000 /app/workspace/.knoxx; chmod 777 /app/workspace/.knoxx/contracts; printf preserved > /app/workspace/.knoxx/contracts/keep", '/app/workspace/.knoxx/contracts/keep', "test \"$(stat -c %u /app/workspace/.knoxx/contracts)\" = 0"),
            ("unwritable contracts", "mkdir -p /app/workspace/.knoxx/contracts; printf preserved > /app/workspace/.knoxx/contracts/keep; chown -R 1000 /app/workspace/.knoxx; chmod 555 /app/workspace/.knoxx/contracts", '/app/workspace/.knoxx/contracts/keep', "test \"$(stat -c %a /app/workspace/.knoxx/contracts)\" = 555"),
            ("untraversable parent", "mkdir /app/workspace/.knoxx; printf preserved > /app/workspace/.knoxx/keep; chown -R 1000 /app/workspace/.knoxx; chmod 600 /app/workspace/.knoxx", '/app/workspace/.knoxx/keep', "test ! -e /app/workspace/.knoxx/contracts"),
            ("outside parent symlink", "mkdir /fixture/outside; chown 1000 /fixture/outside; printf preserved > /fixture/outside/keep; ln -s /fixture/outside /app/workspace/.knoxx", '/fixture/outside/keep', "test ! -e /fixture/outside/contracts"),
        )
        for label, setup, preserved, unchanged in cases:
            shell(reset + "; " + setup)
            expect(f"preparation refuses {label}", command, False, uid=1000)
            expect(f"preparation preserves {label} bytes and state", f'test "$(cat {preserved})" = preserved; {unchanged}', True)
        shell(reset)
        expect("other services do not create Knoxx state", "service=website; KNOXX_WORKSPACE_PATH=/app/workspace;\n" + preparation + "\ntest ! -e /app/workspace/.knoxx", True, uid=1000)
        print(f"{assertions} mount/ownership assertions; all passed")
    finally:
        cleanup = docker("rm", "--force", container, check=False)
        if cleanup.returncode and "No such container" not in cleanup.stderr:
            raise RuntimeError(f"cannot remove owned fixture container {container}: {cleanup.stderr}")


if __name__ == "__main__":
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("isolated Docker fixture interrupted")

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.environ.get("KNOXX_MOUNT_TEST_IMAGE", DEFAULT_IMAGE))
    args = parser.parse_args()
    run(args.image)
