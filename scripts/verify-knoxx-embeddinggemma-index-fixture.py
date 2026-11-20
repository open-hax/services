#!/usr/bin/env python3
"""Rehearse the reviewed Knoxx staging embedding profile with an owned Atlas Local instance."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid


MONGO_IMAGE = "mongodb/mongodb-atlas-local@sha256:b3b11da285f28e6a37543d9b42b988c676c7c3b8515c1f7d93f1d0dba566dd66"
ATLAS_MEMORY_MIB = 2560
SERVICES = Path(__file__).resolve().parents[1]
PROBE = SERVICES / "digitalocean/services/knoxx/probe-embedding-migration.js"
PROFILE_FIXTURE = SERVICES / "scripts/tests/fixtures/knoxx-embeddinggemma-profiles.mjs"
PROFILE_CONTRACT = SERVICES / "scripts/staging/knoxx/embeddinggemma2-profile.json"
PROFILE_DATABASE = "knoxx_embeddinggemma_profile_fixture"


def command(*args, cwd=None, timeout=20, env=None, check=True, input=None):
    return subprocess.run(
        args, cwd=cwd, timeout=timeout, env=env, check=check, capture_output=True, text=True, input=input,
    )


def read_profile():
    profile = json.loads(PROFILE_CONTRACT.read_text())
    if (profile.get("version"), profile.get("service"), profile.get("environment")) != (1, "knoxx", "staging"):
        raise RuntimeError("This rehearsal only accepts the Knoxx staging profile")
    if not re.fullmatch(r"[0-9a-f]{40}", profile.get("openplannerSourceSha", "")):
        raise RuntimeError("The staging profile must pin a full OpenPlanner source SHA")
    if profile.get("eventsCollection") != "events":
        raise RuntimeError("Both embedding profiles must use the authoritative events collection")
    names = {profile["eventsCollection"]}
    for key, model, dimensions in (
        ("legacy", "gte-large-en-v1.5", 1024), ("candidate", "embeddinggemma-2", 768),
    ):
        entry = profile[key]
        if (entry["model"], entry["dimensions"]) != (model, dimensions):
            raise RuntimeError(f"Unexpected {key} embedding model or dimensions")
        nameset = entry["collections"]
        if set(nameset) != {"hot", "compact", "summaries", "partitions", "graph"}:
            raise RuntimeError(f"Incomplete {key} embedding profile collections")
        for name in nameset.values():
            if not re.fullmatch(r"[a-z][a-z_0-9]{0,62}", name) or name in names:
                raise RuntimeError("Profile collection names must be distinct from source and each other")
            names.add(name)
    return profile


def extract_package(workspace, checkout, revision, package):
    entries = command("git", "-C", str(checkout), "ls-tree", "-r", "--name-only", revision,
                      package).stdout.splitlines()
    for entry in entries:
        path = entry.removeprefix(package + "/")
        exact = command("git", "-C", str(checkout), "show", f"{revision}:{entry}")
        (workspace / path).parent.mkdir(parents=True, exist_ok=True)
        (workspace / path).write_text(exact.stdout)


def prepare_sdk(workspace, checkout, revision):
    node = Path(command("node", "-p", "process.execPath", cwd=checkout).stdout.strip()).resolve(strict=True)
    dependencies = (checkout / "packages/openplanner-sdk/node_modules").resolve(strict=True)
    driver = json.loads((dependencies / "mongodb/package.json").read_text())
    if driver["version"] != "7.2.0":
        raise RuntimeError("The pinned SDK fixture requires MongoDB Node driver 7.2.0")
    sdk_package = "packages/openplanner-sdk"
    hydration_package = "packages/stores/document-hydration"
    hydration = workspace / "stores/document-hydration"
    extract_package(workspace, checkout, revision, sdk_package)
    extract_package(hydration, checkout, revision, hydration_package)
    extract_package(workspace / "stores/cache", checkout, revision, "packages/stores/cache")

    # Keep installed third-party tools available while resolving both local
    # OpenPlanner dependencies from only the exact temporary source tree.
    modules = workspace / "node_modules"
    modules.mkdir()
    for dependency in dependencies.iterdir():
        if dependency.name != "@open-hax" and not dependency.name.startswith("."):
            (modules / dependency.name).symlink_to(dependency.resolve(strict=True), target_is_directory=True)
    (modules / "@open-hax").mkdir()
    (modules / "@open-hax/openplanner-document-hydration").symlink_to(hydration, target_is_directory=True)
    shadow = (checkout / hydration_package / "node_modules/shadow-cljs").resolve(strict=True)
    if json.loads((shadow / "package.json").read_text())["version"] != "3.4.10":
        raise RuntimeError("The pinned SDK fixture requires Shadow CLJS 3.4.10")
    (modules / "shadow-cljs").symlink_to(shadow, target_is_directory=True)

    compiler = modules / "typescript/bin/tsc"
    clean_env = {"PATH": os.environ["PATH"]}
    if command(str(node), str(compiler), "--version", env=clean_env).stdout.strip() != "Version 5.9.3":
        raise RuntimeError("The pinned SDK fixture requires TypeScript 5.9.3")
    command(str(node), str(shadow / "cli/runner.js"), "release", "lib",
            cwd=hydration, env=clean_env, timeout=90)
    if not (hydration / "dist/index.js").is_file():
        raise RuntimeError("The reviewed hydration build did not produce its runtime entry point")
    command(str(node), str(compiler), "--project", "tsconfig.json", cwd=workspace, env=clean_env, timeout=45)
    return str(node), clean_env


def local_proxx_binding(container):
    service = command("docker", "inspect", "--format",
                      '{{index .Config.Labels "com.docker.compose.service"}}', container).stdout.strip()
    if service != "proxx":
        raise RuntimeError("The authenticated proof requires an existing local Proxx service")
    listeners = command("docker", "port", container, "8789/tcp").stdout.splitlines()
    loopback = [line for line in listeners if re.fullmatch(r"127\.0\.0\.1:[0-9]{2,5}", line)]
    if len(loopback) != 1:
        raise RuntimeError("The existing Proxx service must expose one explicit loopback listener")
    template = '{{range .Config.Env}}{{if eq (index (split . "=") 0) "PROXY_AUTH_TOKEN"}}{{println .}}{{end}}{{end}}'
    credential = command("docker", "inspect", "--format", template, container).stdout.strip()
    key, _, token = credential.partition("=")
    if key != "PROXY_AUTH_TOKEN" or not token or "\n" in token:
        raise RuntimeError("The existing Proxx service has no usable local proxy authentication")
    return {"baseUrl": f"http://{loopback[0]}", "token": token}


def run_profile_fixture(node, sdk, uri, nonce, clean_env, provider, profile):
    try:
        result = command(node, str(PROFILE_FIXTURE), str(sdk), uri, nonce, env=clean_env, timeout=280,
                         input=json.dumps({"provider": provider, "profile": profile}))
    except subprocess.CalledProcessError as error:
        detail = error.stderr.replace(provider["token"], "[redacted]")
        raise RuntimeError(f"Native profile fixture failed ({error.returncode}): {detail.strip()}") from None
    return json.loads(result.stdout)


def require_memory_headroom():
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available_kib = int(fields["MemAvailable"].split()[0])
    if available_kib < 7 * 1024 * 1024:
        raise RuntimeError("Atlas fixture requires 7 GiB available RAM for its 2.5 GiB cap and host headroom")


def wait_for_mongo(container):
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        state = json.loads(command("docker", "inspect", "--format", "{{json .State}}", container).stdout)
        if not state["Running"]:
            raise RuntimeError("The owned Atlas fixture stopped before readiness")
        if state.get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(1)
    raise RuntimeError("Atlas Local did not pass its bundled healthcheck")


def verify_existing_gate(node, uri, clean_env, sdk):
    base = {
        **clean_env,
        "NODE_PATH": str(sdk / "node_modules"),
        "MONGODB_URI": uri,
        "MONGODB_DB": PROFILE_DATABASE,
        "EMBED_SOURCE_CONTRACT_PRESENT": "1",
        "EMBED_SOURCE_WRITER_ACTIVE": "1",
        "EMBED_SOURCE_MODEL": "gte-large-en-v1.5",
        "EMBED_SOURCE_DIMENSIONS": "1024",
        "EMBED_SOURCE_DATABASE_FINGERPRINT": "a" * 64,
        "EMBED_TARGET_DATABASE_FINGERPRINT": "a" * 64,
    }
    results = []
    for model, dimensions, expected in (
        ("qwen3-embedding:8b", "1024", "incompatible-writer-active"),
        ("embeddinggemma-2", "768", "unexpected-target"),
    ):
        env = {**base, "EMBED_PROVIDER_MODEL": model, "EMBED_PROVIDER_DIMENSIONS": dimensions}
        result = command(node, str(PROBE), env=env, check=False)
        response = json.loads(result.stdout)
        if result.returncode != 1 or response.get("ok") is not False or response.get("reason") != expected:
            raise RuntimeError(f"Existing Services guard did not refuse {model} as {expected}")
        results.append({"target": model, "guardRefusal": expected})
    return results


def run_fixture(sdk_checkout, proxx_container, profile):
    with tempfile.TemporaryDirectory(prefix="knoxx-embeddinggemma-index-") as temporary:
        sdk = Path(temporary)
        sdk_revision = profile["openplannerSourceSha"]
        actual = command("git", "-C", str(sdk_checkout), "rev-parse", "--verify", f"{sdk_revision}^{{commit}}")
        if actual.stdout.strip() != sdk_revision:
            raise RuntimeError("Could not identify the reviewed OpenPlanner source")
        node, clean_env = prepare_sdk(sdk, sdk_checkout, sdk_revision)
        provider = local_proxx_binding(proxx_container)
        require_memory_headroom()
        nonce = uuid.uuid4().hex
        name = f"knoxx-embeddinggemma-index-{nonce[:12]}"
        container = command(
            "docker", "create", "--pull=never", "--name", name, "--label", "open-hax.fixture=knoxx-embeddinggemma",
            "--publish", "127.0.0.1::27017", f"--memory={ATLAS_MEMORY_MIB}m",
            f"--memory-swap={ATLAS_MEMORY_MIB}m", "--cpus=1",
            MONGO_IMAGE,
        ).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise RuntimeError("Docker did not identify the owned fixture container")
        try:
            command("docker", "start", container)
            wait_for_mongo(container)
            listener = command("docker", "port", container, "27017/tcp").stdout.strip()
            if not re.fullmatch(r"127\.0\.0\.1:[0-9]{2,5}", listener):
                raise RuntimeError("The Atlas fixture requires an exclusive loopback listener")
            uri = f"mongodb://{listener}/?directConnection=true&serverSelectionTimeoutMS=4000"
            seed_owner = f'db.getSiblingDB("{PROFILE_DATABASE}").fixture_owner.insertOne({{_id:"{nonce}"}})'
            command("docker", "exec", container, "mongosh", "--norc", "--quiet", "--eval", seed_owner)
            guard = verify_existing_gate(node, uri, clean_env, sdk)
            evidence = run_profile_fixture(node, sdk, uri, nonce, clean_env, provider, profile)
        except (RuntimeError, subprocess.SubprocessError):
            state = command("docker", "inspect", "--format", "{{json .State}}", container, check=False)
            if state.returncode == 0:
                print(f"Owned Atlas fixture state at failure: {state.stdout.strip()}", file=sys.stderr)
            raise
        finally:
            command("docker", "rm", "--force", "--volumes", container)
        return {
            "service": profile["service"], "environment": profile["environment"],
            "sdkRevision": sdk_revision, "databaseImage": MONGO_IMAGE, "memoryLimitMiB": ATLAS_MEMORY_MIB,
            "hydrationSourceSha": sdk_revision,
            "guard": guard, **evidence,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("openplanner_checkout", type=Path, help="Checkout with the pinned SDK dependencies installed")
    parser.add_argument("--local-proxx-container", required=True,
                        help="Existing local Proxx service for authenticated inference")
    args = parser.parse_args()
    checkout = args.openplanner_checkout.resolve(strict=True)
    print(json.dumps(run_fixture(checkout, args.local_proxx_container, read_profile()), indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Fixture command failed ({error.returncode}): {error.stderr.strip()}") from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
