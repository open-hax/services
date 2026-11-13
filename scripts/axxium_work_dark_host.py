#!/usr/bin/env python3
"""Start a single fixed, ingress-dark Work preflight on the production host."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "axxium-work-pilot"
SLOT = Path("/srv/open-hax/services") / PROJECT
PROJECT_LABEL = f"com.docker.compose.project={PROJECT}"
POSTGRES_INDEX = "sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
POSTGRES = "docker.io/library/postgres@" + POSTGRES_INDEX
POSTGRES_CONFIG = "sha256:75f5a96988cdf694a215073c3e9c001b706b371e2f94df3967f2efdec2787f6b"
POSTGRES_MANIFEST = "sha256:075f7ba66bc9b3ce7d6b8b635208ff61cd7cf1a67d71ec530eec5d7ae0cbe571"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
COMMAND_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "HOME": str(Path.home())}
INCUMBENT = ("axxium-axxium-1", "axxium-axxium-db-1", "axxium-pds-1", "caddy-caddy-1")
ORIGIN = "https://axxium.promethean.rest"
RUNTIME_PROBE = """
const base = 'http://127.0.0.1:8787';
(async () => {
  const health = await fetch(base + '/health');
  if (health.status !== 200 || (await health.json()).status !== 'ok') process.exit(1);
  for (const [method, path] of [
    ['POST', '/api/mailbox/mcp'],
    ['GET', '/.well-known/oauth-protected-resource/api/mailbox/mcp'],
    ['GET', '/oidc/.well-known/openid-configuration'],
  ]) {
    const response = await fetch(base + path, {method, redirect: 'manual'});
    if (response.status !== 404) process.exit(1);
  }
})().catch(() => process.exit(1));
"""


class Refused(RuntimeError):
    """A static diagnostic safe for the production release receipt."""


class CommandTimeout(Refused):
    """The daemon may still complete work after its client was killed."""


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def command(args, reason, timeout=30, *, data=None):
    try:
        process = subprocess.Popen(args, env=COMMAND_ENV,
                                   stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError:
        raise Refused(reason) from None
    with process:
        try:
            stdout, _ = process.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise CommandTimeout(reason) from None
        require(process.returncode == 0, reason)
        return stdout


def docker(*args, reason, timeout=30, data=None):
    return command([*DOCKER, *args], reason, timeout, data=data)


def incumbent():
    containers = json.loads(docker("inspect", *INCUMBENT, reason="incumbent runtime unavailable"))
    require(tuple(c["Name"] for c in containers) == tuple("/" + n for n in INCUMBENT),
            "incumbent container names differ")
    identities = {}
    for name, container in zip(INCUMBENT, containers):
        state = container["State"]
        require(state["Running"] and state.get("Health", {}).get("Status") == "healthy",
                "incumbent service is unhealthy")
        identities[name] = [container["Id"], container["Image"], state["StartedAt"]]
    sentinels = {}
    for path, field, expected in (
        ("/health", "status", "ok"),
        ("/oidc/.well-known/openid-configuration", "issuer", ORIGIN + "/oidc"),
        ("/xrpc/com.atproto.server.describeServer", "did", "did:web:axxium.promethean.rest"),
    ):
        response = command(["curl", "--fail", "--silent", "--noproxy", "*", "--proto", "=https",
                            "--max-time", "10", "--max-filesize", "1048576", ORIGIN + path],
                           "incumbent public sentinel unavailable")
        value = json.loads(response).get(field)
        require(value == expected, "incumbent public sentinel changed")
        sentinels[path] = value
    return {"containers": identities, "public_sentinels": sentinels}


def sync_slot():
    fd = os.open(SLOT, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private(path, content):
    with path.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    sync_slot()


def compose(*args, reason, timeout=30):
    return docker("compose", "--project-name", PROJECT, "--project-directory", str(SLOT),
                  "--env-file", str(SLOT / "release.env"), "-f", str(SLOT / "compose.yaml"), *args,
                  reason=reason, timeout=timeout)


def project_containers(*options):
    return docker("ps", *options, "--quiet", "--filter", f"label={PROJECT_LABEL}",
                  reason="pilot container inventory unavailable").decode().splitlines()


def ensure_new_project():
    require(not any(path.name != ".lock" for path in SLOT.iterdir()), "pilot slot already initialized")
    require(not project_containers("--all"), "pilot container already exists")
    names = docker("ps", "--all", "--format", "{{.Names}}", reason="container names unavailable")
    reserved = {f"{PROJECT}-{service}-1" for service in ("axxium", "database")}
    require(reserved.isdisjoint(names.decode().splitlines()), "pilot container name already exists")
    for kind, name in (("network", PROJECT + "_default"), ("volume", PROJECT + "_database")):
        require(not docker(kind, "ls", "--quiet", "--filter", f"label={PROJECT_LABEL}",
                           reason="pilot resource inventory unavailable").strip(),
                "pilot network or volume already exists")
        names = docker(kind, "ls", "--format", "{{.Name}}", reason="pilot resource names unavailable")
        require(name not in names.decode().splitlines(), "pilot network or volume name already exists")


def verified_image(reference, config, index, manifest):
    raw = docker("image", "inspect", reference, reason="pinned image unavailable")
    selected, = json.loads(raw)
    raw = docker("image", "inspect", "--platform", "linux/amd64", reference,
                 reason="pinned image platform unavailable")
    platform, = json.loads(raw)
    require((platform["Os"], platform["Architecture"]) == ("linux", "amd64"),
            "pinned image platform differs")
    # Classic Docker exposes the config ID. Containerd exposes the tagged
    # index as the image ID and the concrete manifest on the platform view.
    if selected["Id"] == config:
        require(platform["Id"] == config, "pinned image config differs")
    else:
        require(selected["Id"] == index and platform["Id"] == manifest
                and (platform.get("Descriptor") or {}).get("digest") == manifest,
                "pinned image index or manifest differs")
    return selected["Id"]


def verify_dark(images, manifests):
    ids = project_containers()
    require(len(ids) == 2, "expected one running pilot app and database")
    containers = json.loads(docker("inspect", *ids, reason="pilot runtime unavailable"))
    apps = {}
    network_name = PROJECT + "_default"
    for container in containers:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        state, config, network = container["State"], container["HostConfig"], container["NetworkSettings"]
        require(service not in apps and container["Image"] == images.get(service),
                "unexpected pilot service image")
        descriptor = container.get("ImageManifestDescriptor") or {}
        if descriptor or images[service] != manifests[service][0]:
            require(descriptor.get("digest") == manifests[service][1],
                    "pilot container manifest differs")
        require(state["Running"] and state["Health"]["Status"] == "healthy", "pilot service is unhealthy")
        require(not config["PortBindings"] and config["PublishAllPorts"] is False
                and set(network["Networks"]) == {network_name},
                "pilot service is exposed or attached outside its private network")
        mounts = container["Mounts"]
        require(len(mounts) == 1, "unexpected pilot mounts")
        mount = mounts[0]
        if service == "axxium":
            require(mount["Type"] == "bind" and mount["Source"] == str(SLOT / "actor-signing.pem")
                    and mount["Destination"] == "/run/axxium/actor-signing.pem" and not mount["RW"],
                    "unexpected pilot signing key mount")
            require(not any(item.startswith(("AXXIUM_MAILBOX_", "AXXIUM_OIDC_CONFIG_FILE="))
                            for item in container["Config"]["Env"]), "pilot intake is configured")
        else:
            require(mount["Type"] == "volume" and mount["Name"] == PROJECT + "_database"
                    and mount["Destination"] == "/var/lib/postgresql/data" and mount["RW"],
                    "unexpected pilot database mount")
        apps[service] = container["Id"]
    network, = json.loads(docker("network", "inspect", network_name, reason="pilot network unavailable"))
    require(network["Internal"] and network["Driver"] == "bridge"
            and network["Labels"]["com.docker.compose.project"] == PROJECT
            and network["Labels"]["com.docker.compose.network"] == "default"
            and set(network["Containers"]) == set(apps.values()), "pilot network is not isolated")
    volume, = json.loads(docker("volume", "inspect", PROJECT + "_database", reason="pilot volume unavailable"))
    require(volume["Driver"] == "local" and not volume["Options"]
            and volume["Labels"]["com.docker.compose.project"] == PROJECT
            and volume["Labels"]["com.docker.compose.volume"] == "database", "pilot database volume is not isolated")
    docker("exec", apps["axxium"], "node", "-e", RUNTIME_PROBE, reason="pilot HTTP boundary failed")
    return apps


def prepare_database_image():
    """Pull the pinned database digest before inspection. The app archive does not contain it."""
    docker("pull", POSTGRES, reason="pinned database image unavailable", timeout=180)
    return verified_image(POSTGRES, POSTGRES_CONFIG, POSTGRES_INDEX, POSTGRES_MANIFEST)


def deploy(args):
    require(os.geteuid() == 1000, "pilot must run as the production deploy user")
    require(SLOT.resolve() == SLOT and SLOT.parent.is_dir(), "pilot path aliases the host runtime")
    SLOT.mkdir(mode=0o700, exist_ok=True)
    require(SLOT.is_dir() and SLOT.stat().st_uid == os.geteuid()
            and stat.S_IMODE(SLOT.stat().st_mode) == 0o700, "pilot slot is not private")
    with os.fdopen(os.open(SLOT / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command(["sh", str(ROOT / "digitalocean/scripts/check-axxium-production-hold.sh")],
                "an incumbent production transition is held")
        ensure_new_project()
        baseline = incumbent()
        archive = ROOT / "stage-images.tar"
        with archive.open("rb") as image_stream:
            require(hashlib.file_digest(image_stream, "sha256").hexdigest() == args.archive_sha256,
                    "transferred staged image archive differs")
        receipt = {"schema": "axxium-work-dark-host/v1", "source_sha": args.source,
                   "controller_sha": args.controller, "image_config_digest": args.image,
                   "image_index_digest": args.index, "image_manifest_digest": args.manifest,
                   "images_tar_sha256": args.archive_sha256, "public_ingress": False,
                   "mailbox_active": False, "incumbent_before": baseline}
        write_private(SLOT / "pending.json", json.dumps(receipt).encode() + b"\n")
        attempted_start = False
        try:
            database = prepare_database_image()
            docker("image", "load", "--input", str(archive), reason="staged image load failed", timeout=180)
            selected = verified_image(f"promethean-axxium:{args.source}", args.image, args.index, args.manifest)
            images = {"axxium": selected, "database": database}
            template = ROOT / "digitalocean/services" / PROJECT / "compose.yaml"
            write_private(SLOT / "compose.yaml", template.read_bytes())
            write_private(SLOT / "release.env", (
                f"AXXIUM_PILOT_IMAGE={selected}\nAXXIUM_PILOT_UID={os.geteuid()}\n"
                f"AXXIUM_PILOT_GID={os.getegid()}\nAXXIUM_PILOT_DATABASE_PASSWORD={secrets.token_hex(32)}\n"
            ).encode())
            key = command(["openssl", "genpkey", "-algorithm", "ED25519"], "pilot key generation failed")
            write_private(SLOT / "actor-signing.pem", key)
            compose("config", "--quiet", reason="pilot Compose configuration rejected")
            attempted_start = True
            compose("up", "-d", "--wait", "--wait-timeout", "180", "--pull", "never",
                    reason="pilot Compose startup failed", timeout=210)
            receipt["pilot_runtime_images"] = images
            receipt["pilot_containers"] = verify_dark(
                images, {"axxium": (args.image, args.manifest), "database": (POSTGRES_CONFIG, POSTGRES_MANIFEST)},
            )
            receipt["incumbent_after"] = incumbent()
            require(receipt["incumbent_after"] == baseline, "incumbent runtime changed")
            receipt["status"] = "healthy-dark"
        except (Refused, OSError, ValueError, KeyError) as error:
            receipt.update(status="held", reason=str(error) if isinstance(error, Refused) else "pilot verification failed")
            if attempted_start:
                try:
                    compose("stop", "--timeout", "610", reason="pilot stop could not be verified", timeout=670)
                    require(not project_containers(), "pilot still running after rollback")
                    receipt["incumbent_after"] = incumbent()
                    require(receipt["incumbent_after"] == baseline, "incumbent changed during rollback")
                    if isinstance(error, CommandTimeout):
                        receipt["rollback"] = "stop-observed-daemon-state-uncertain"
                    else:
                        receipt["status"] = "rolled-back-dark"
                except (Refused, OSError, ValueError, KeyError):
                    receipt["rollback"] = "unverified"
        write_private(SLOT / "release.json", json.dumps(receipt).encode() + b"\n")
        if receipt["status"] in {"healthy-dark", "rolled-back-dark"}:
            (SLOT / "pending.json").unlink()
            sync_slot()
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("source", "controller", "image", "index", "manifest", "archive-sha256"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[0-9a-f]{40}", args.source) and re.fullmatch(r"[0-9a-f]{40}", args.controller)
                and all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in (args.image, args.index, args.manifest))
                and re.fullmatch(r"[0-9a-f]{64}", args.archive_sha256), "invalid admitted candidate identity")
        os.umask(0o077)
        receipt = deploy(args)
    except (Refused, OSError, ValueError, KeyError) as error:
        receipt = {"status": "refused", "reason": str(error) if isinstance(error, Refused) else "host preflight failed"}
    json.dump(receipt, sys.stdout)
    print()
    return 0 if receipt["status"] == "healthy-dark" else 1


if __name__ == "__main__":
    sys.exit(main())
