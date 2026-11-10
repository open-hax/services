#!/usr/bin/env python3
"""Activate or retire the one isolated Work pilot on the production host."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

import axxium_work_dark_host as dark

ROOT = dark.ROOT
SLOT = dark.SLOT
LIVE = SLOT / "live"
PACKAGE = ROOT / "digitalocean/services" / dark.PROJECT
PENDING = SLOT / ".axxium-work-production-pending"
MANAGED = Path("/srv/open-hax/state/caddy/managed")
ROUTE = MANAGED / "axxium-work-pilot.caddy"
PRE_CONSENT_ROUTE_SHA256 = "c5370b9431f840efb852391594f450e46a13afde08713581fdb6bd87bfcffc0f"
ORIGIN = "https://work.pilot.axxium.promethean.rest"
HOST = ORIGIN.removeprefix("https://")
ISSUER = ORIGIN + "/oidc"
MCP = "/api/mailbox/mcp"
RESOURCE = ORIGIN + MCP
METADATA = "/.well-known/oauth-protected-resource" + MCP
CLIENT = "axxium-mailbox-chatgpt-work-pilot"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
HANDLES = {
    "sender_did": "work-pilot-sender.axxium.promethean.rest",
    "recipient_did": "work-pilot-inbox.axxium.promethean.rest",
}
SOCKET = "promethean-axxium-work-pilot.socket"
TIMER = "promethean-axxium-work-pilot-stop.timer"
RELAY = "promethean-axxium-work-pilot.service"
STOP = "promethean-axxium-work-pilot-stop.service"
UNITS = Path("/etc/systemd/system")
require, command, docker = dark.require, dark.command, dark.docker


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_file(path, raw, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".work-pilot-", delete=False) as output:
        scratch = Path(output.name)
        try:
            os.fchmod(output.fileno(), mode)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            os.replace(scratch, path)
            sync_directory(path.parent)
        finally:
            scratch.unlink(missing_ok=True)


def private_path(path, directory=False):
    info = path.lstat()
    kind, mode = (stat.S_ISDIR, 0o700) if directory else (stat.S_ISREG, 0o600)
    require(path.resolve() == path and kind(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == mode and (directory or info.st_nlink == 1),
            "pilot authority path is not private")


def private_read(path, maximum=128 * 1024):
    private_path(path)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, "pilot authority input exceeds its bound")
    return raw


def absent(path):
    return not os.path.lexists(path)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True).encode() + b"\n"


def validate_config(binding, authority, seal):
    require(set(binding) == {"sender_did", "recipient_did", "stop_at"}
            and all(re.fullmatch("did:plc:[a-z2-7]{24}", binding[key])
                    for key in ("sender_did", "recipient_did"))
            and binding["sender_did"] != binding["recipient_did"], "two distinct pilot DIDs are required")
    stop = datetime.strptime(binding["stop_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    client, = authority["clients"]
    require(authority["issuer"] == ISSUER and authority["mailbox-resource"] == RESOURCE
            and authority["mailbox-client-id"] == CLIENT and not authority.get("allow-loopback?", False)
            and client["id"] == CLIENT and client["redirect-uris"] == [CALLBACK]
            and not client.get("device-enabled?", False)
            and re.fullmatch(r"[A-Za-z0-9_-]{64,128}", client["secret"]),
            "private Work authority does not match the exact client and redirect")
    require(len(base64.b64decode(seal.strip(), validate=True)) == 32, "webhook seal is invalid")
    return stop


def verify_provider(binding):
    for role, handle in HANDLES.items():
        did = binding[role]
        curl = ["curl", "--fail", "--silent", "--noproxy", "*", "--proto", "=https",
                "--max-time", "8", "--max-filesize", "1048576"]
        proof = command([*curl, f"https://{handle}/.well-known/atproto-did"], "pilot handle proof unavailable")
        document = json.loads(command([*curl, f"https://plc.directory/{did}"], "pilot DID document unavailable"))
        require(proof.decode().strip() == did and document["id"] == did
                and document["alsoKnownAs"] == ["at://" + handle]
                and any(service["id"] == "#atproto_pds" and service["type"] == "AtprotoPersonalDataServer"
                        and service["serviceEndpoint"] == dark.ORIGIN for service in document["service"]),
                "pilot public handle and DID do not reciprocally bind the incumbent provider")


def verify_relay(stop):
    remaining = (stop - datetime.now(timezone.utc)).total_seconds()
    require(300 <= remaining <= 14400, "the reviewed pilot deadline is outside the activation window")
    for unit in (SOCKET, RELAY, TIMER, STOP):
        installed = UNITS / unit
        template_name = unit + ".template" if unit == TIMER else unit
        template = (PACKAGE / "ingress/units" / template_name).read_bytes()
        if unit == TIMER:
            template = template.replace(b"__REVIEWED_UTC_STOP__", stop.strftime("%Y-%m-%d %H:%M:%S UTC").encode())
        require(not installed.is_symlink() and installed.stat().st_uid == 0
                and not installed.stat().st_mode & 0o022 and installed.read_bytes() == template,
                "installed relay or deadline unit differs from the reviewed file")
        fragment = systemd_property(unit, "FragmentPath")
        require(fragment == str(installed) and not systemd_property(unit, "DropInPaths")
                and systemd_property(unit, "NeedDaemonReload") == "no",
                "loaded relay unit differs from its reviewed file")
    for unit in (SOCKET, TIMER):
        command(["systemctl", "is-active", "--quiet", unit], "the pilot relay or stop timer is inactive")
    require(systemd_property(SOCKET, "Listen") == "172.31.255.1:19073 (Stream)", "pilot relay listener differs")
    next_time = systemd_property(TIMER, "NextElapseUSecRealtime")
    actual = command(["date", "--date", next_time, "+%s"], "pilot deadline unreadable").decode().strip()
    require(actual == str(int(stop.timestamp())), "armed stop timer does not match the private deadline")


def systemd_property(unit, prop):
    return command(["systemctl", "show", "--property=" + prop, "--value", unit],
                   "pilot relay configuration unavailable").decode().strip()


def verify_relay_boundary(caddy):
    docker("exec", caddy, "wget", "-qO", "/dev/null", "-T", "5", "http://172.31.255.1:19073/health",
           reason="Caddy cannot reach the pilot relay")
    peer = subprocess.run(["curl", "--silent", "--noproxy", "*", "--interface", "127.0.0.1",
                           "--max-time", "3", "--output", "/dev/null", "http://172.31.255.1:19073/health"],
                          env=dark.COMMAND_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=5, check=False)
    require(peer.returncode in (7, 28, 52, 56), "pilot relay accepted a source outside Caddy")


def selection():
    receipt = json.loads(private_read(SLOT / "release.json"))
    plan = json.loads((PACKAGE / "dark-release-plan.json").read_bytes())
    require(receipt["status"] == "healthy-dark"
            and receipt["source_sha"] == plan["source_sha"]
            and receipt["image_index_digest"] == plan["image_id"]
            and receipt["image_config_digest"] == plan["image_config_digest"]
            and receipt["image_manifest_digest"] == plan["image_manifest_digest"],
            "dark baseline does not match the reviewed slot")
    require(private_read(SLOT / "compose.yaml") == (PACKAGE / "compose.yaml").read_bytes()
            and absent(SLOT / "pending.json"), "dark Compose selection differs or is held")
    private_path(SLOT / "actor-signing.pem")
    environment = private_read(SLOT / "release.env")
    expected = (r"AXXIUM_PILOT_IMAGE=sha256:[a-f0-9]{64}\n"
                r"AXXIUM_PILOT_UID=1000\nAXXIUM_PILOT_GID=[0-9]+\n"
                r"AXXIUM_PILOT_DATABASE_PASSWORD=[a-f0-9]{64}\n")
    require(re.fullmatch(expected, environment.decode()) and environment.splitlines()[0].decode()
            == "AXXIUM_PILOT_IMAGE=" + receipt["pilot_runtime_images"]["axxium"],
            "dark environment does not match its recorded image")
    return receipt, environment


def live_compose(*args, reason, timeout=30):
    return docker("compose", "--project-name", dark.PROJECT, "--project-directory", str(SLOT),
                  "--env-file", str(LIVE / "candidate.env"), "-f", str(SLOT / "compose.yaml"),
                  "-f", str(LIVE / "compose.overlay.yaml"), *args, reason=reason, timeout=timeout)


def caddy_view():
    caddy, = json.loads(docker("inspect", "caddy-caddy-1", reason="Caddy is unavailable"))
    require(caddy["NetworkSettings"]["Networks"]["caddy-dev-ingress"]["IPAddress"] == "172.31.255.2"
            and MANAGED.resolve() == MANAGED and MANAGED.is_dir()
            and any(mount["Source"] == str(MANAGED) and mount["Destination"] == "/etc/caddy/managed"
                    and not mount["RW"] for mount in caddy["Mounts"]),
            "Caddy managed mount or relay source differs")
    root = docker("exec", caddy["Id"], "cat", "/etc/caddy/Caddyfile", reason="Caddyfile unavailable")
    names = sorted(p.name for p in MANAGED.glob("*.caddy") if p != ROUTE)
    require(all(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\.caddy", name) for name in names),
            "managed Caddy sibling names differ")
    siblings = {name: digest((MANAGED / name).read_bytes()) for name in names}
    return caddy["Id"], root, siblings


def render_validation(root, siblings, candidate):
    marker = b"import /etc/caddy/managed/*.caddy"
    lines = root.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip(b"\r\n") == marker]
    require(len(matches) == 1, "Caddy managed wildcard import differs")
    lines[matches[0]] = b"".join(candidate if name == ROUTE.name else
                                f"import /etc/caddy/managed/{name}\n".encode()
                                for name in sorted({*siblings, ROUTE.name}))
    return b"".join(lines)


def publish_route(candidate, expected, view):
    caddy, root, siblings = view
    require(caddy_view() == view and (absent(ROUTE) if expected is None
            else not ROUTE.is_symlink() and ROUTE.read_bytes() == expected),
            "pilot Caddy route or incumbent managed state changed")
    adapter = ("--adapter", "caddyfile", "--config")
    docker("exec", "-i", caddy, "caddy", "validate", *adapter, "-",
           reason="full Caddy candidate validation failed",
           data=render_validation(root, siblings, candidate or b""))
    if candidate is None:
        ROUTE.unlink(missing_ok=True)
        sync_directory(MANAGED)
    else:
        replace_file(ROUTE, candidate, 0o644)
        require(docker("exec", caddy, "cat", "/etc/caddy/managed/" + ROUTE.name,
                       reason="Caddy could not read the pilot snippet") == candidate,
                "container-visible Caddy snippet differs")
    docker("exec", caddy, "caddy", "validate", *adapter, "/etc/caddy/Caddyfile",
           reason="container-visible Caddy config rejected")
    docker("exec", caddy, "caddy", "reload", *adapter, "/etc/caddy/Caddyfile",
           reason="Caddy reload failed")


def public_probe(path, *, method="GET", body=False, retry=False, headers=False):
    args = ["curl", "--silent", "--show-error", "--noproxy", "*", "--proto", "=https",
            "--max-time", "6", "--max-filesize", "1048576", "--request", method]
    if not body:
        args += ["--output", "/dev/null", "--write-out", "%{http_code}"]
    if headers:
        args += ["--dump-header", "-"]
    # ACME may still be issuing the certificate. Retry failed invocations
    # without letting curl retry an intentional fenced 503 response.
    retries = 12 if retry else 0
    for attempt in range(retries + 1):
        try:
            raw = command([*args, ORIGIN + path], "pilot HTTPS probe failed", timeout=7)
        except dark.Refused:
            if attempt == retries:
                raise
            time.sleep(3)
        else:
            return json.loads(raw) if body else raw.decode().strip()


def probe_live():
    require(public_probe("/health", body=True)["status"] == "ok", "pilot health failed")
    for path in ("/.well-known/oauth-authorization-server/oidc", "/.well-known/openid-configuration/oidc"):
        metadata = public_probe(path, body=True)
        require(metadata["issuer"] == ISSUER and "S256" in metadata["code_challenge_methods_supported"],
                "pilot OIDC issuer or PKCE metadata differs")
    metadata = public_probe(METADATA, body=True)
    require(metadata["resource"] == RESOURCE and metadata["authorization_servers"] == [ISSUER]
            and metadata["scopes_supported"] == ["mailbox:events"], "pilot mailbox metadata differs")
    metadata = public_probe("/api/auth/atproto/client-metadata.json", body=True)
    require(metadata["client_id"] == ORIGIN + "/api/auth/atproto/client-metadata.json"
            and metadata["redirect_uris"] == [ORIGIN + "/api/auth/atproto/callback"],
            "pilot AT Protocol callback metadata differs")
    challenge = public_probe(MCP, method="POST", headers=True)
    expected = ('www-authenticate: Bearer resource_metadata="' + ORIGIN + METADATA + '"').lower()
    require(challenge.endswith("401") and expected in {line.lower() for line in challenge.splitlines()}
            and public_probe("/xrpc/com.atproto.server.describeServer") == "404",
            "pilot mailbox challenge or unrelated route differs")


def read_live_runtime(receipt, image, plan, binding):
    containers = json.loads(docker("inspect", *dark.project_containers(), reason="pilot runtime unavailable"))
    by_name = {c["Name"]: c for c in containers}
    app = by_name[f"/{dark.PROJECT}-axxium-1"]
    database = by_name[f"/{dark.PROJECT}-database-1"]
    require(len(containers) == 2 and database["Id"] == receipt["pilot_containers"]["database"]
            and set(database["NetworkSettings"]["Networks"]) == {dark.PROJECT + "_default"}
            and not database["HostConfig"]["PortBindings"], "pilot database or isolation changed")
    require(app["Image"] == image and app["State"]["Health"]["Status"] == "healthy"
            and app["HostConfig"]["PortBindings"] == {"8787/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18873"}]}
            and set(app["NetworkSettings"]["Networks"]) == {dark.PROJECT + "_default", dark.PROJECT + "_egress"},
            "pilot app image, health, port or networks differ")
    descriptor = app.get("ImageManifestDescriptor") or {}
    if descriptor or image != plan["image_config_digest"]:
        require(descriptor.get("digest") == plan["image_manifest_digest"], "pilot image manifest differs")
    egress, = json.loads(docker("network", "inspect", dark.PROJECT + "_egress",
                               reason="pilot egress network unavailable"))
    require(not egress["Internal"] and egress["Driver"] == "bridge"
            and egress["IPAM"]["Config"] == [{"Subnet": "172.31.254.0/29", "Gateway": "172.31.254.1"}]
            and set(egress["Containers"]) == {app["Id"]}, "pilot egress gateway or peer differs")
    require({(m["Source"], m["Destination"], m["RW"]) for m in app["Mounts"]} == {
        (str(SLOT / "actor-signing.pem"), "/run/axxium/actor-signing.pem", False),
        (str(LIVE / "oidc"), "/run/axxium/oidc", False),
        (str(LIVE / "mailbox/ledger.edn"), "/var/lib/axxium/mailbox/ledger.edn", True)},
        "pilot authority or ledger mount differs")
    env = dict(item.split("=", 1) for item in app["Config"]["Env"])
    require(env["AXXIUM_TRUST_PROXY"] == "172.31.254.1"
            and env["AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID"] == binding["recipient_did"]
            and env["AXXIUM_MAILBOX_PILOT_ALLOWED_DIDS"] == binding["sender_did"] + "," + binding["recipient_did"],
            "pilot runtime actor roles or proxy trust differ")
    return {"axxium": app["Id"], "database": database["Id"]}


def save_phase(result, phase):
    result["phase"] = phase
    replace_file(PENDING, json_bytes(result), 0o600)


def restore_dark(receipt):
    dark.compose("up", "-d", "--wait", "--wait-timeout", "180", "--no-deps", "--pull", "never", "axxium",
                 reason="dark app restoration failed", timeout=850)
    containers = dark.verify_dark(receipt["pilot_runtime_images"], {
        "axxium": (receipt["image_config_digest"], receipt["image_manifest_digest"]),
        "database": (dark.POSTGRES_CONFIG, dark.POSTGRES_MANIFEST)})
    require(containers["database"] == receipt["pilot_containers"]["database"], "dark database changed")
    return containers


def activate(controller, receipt, environment, baseline, view, fenced, active):
    require(absent(ROUTE) and all(absent(LIVE / name) for name in
            ("active.json", "candidate.env", "compose.overlay.yaml", "mailbox", "dark-postgres.dump")),
            "pilot activation files already exist")
    dark.verify_dark(receipt["pilot_runtime_images"], {
        "axxium": (receipt["image_config_digest"], receipt["image_manifest_digest"]),
        "database": (dark.POSTGRES_CONFIG, dark.POSTGRES_MANIFEST)})
    private_path(LIVE / "oidc", directory=True)
    binding = json.loads(private_read(LIVE / "activation.json"))
    stop = validate_config(binding, json.loads(private_read(LIVE / "oidc/authority.json")),
                           private_read(LIVE / "oidc/webhook-seal.key", 128))
    verify_provider(binding)
    verify_relay(stop)
    plan = json.loads((PACKAGE / "activation-release-plan.json").read_bytes())
    archive = ROOT / "stage-images.tar"
    with archive.open("rb") as stream:
        require(hashlib.file_digest(stream, "sha256").hexdigest() == plan["images_tar_sha256"],
                "transferred staged archive differs")
    result = {"schema": "axxium-work-activation-host/v1", "controller_sha": controller, "origin": ORIGIN,
              "source_sha": plan["source_sha"], "image_index_digest": plan["image_id"],
              "image_config_digest": plan["image_config_digest"], "image_manifest_digest": plan["image_manifest_digest"],
              "status": "held", "public_ingress": False, "mailbox_active": False, "stop_at": binding["stop_at"],
              "incumbent_before": baseline, "plan_sha256": digest((PACKAGE / "activation-release-plan.json").read_bytes())}
    write_json(PENDING, {**result, "phase": "before-effects"})
    changed_app = False
    try:
        db = receipt["pilot_containers"]["database"]
        query = "SELECT (SELECT count(*) FROM actors) + (SELECT count(*) FROM sessions);"
        require(docker("exec", db, "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", "axxium",
                       "-d", "axxium", "-c", query, reason="dark database inventory failed").strip() == b"0",
                "dark pilot already has actors or sessions")
        for table in ("axxium_oidc_records", "axxium_oidc_grant_sessions"):
            query = f"SELECT to_regclass('public.{table}');"
            if docker("exec", db, "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", "axxium",
                      "-d", "axxium", "-c", query, reason="dark grant inventory failed").strip():
                query = f"SELECT count(*) FROM {table};"
                require(docker("exec", db, "psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-U", "axxium",
                               "-d", "axxium", "-c", query, reason="dark grant inventory failed").strip() == b"0",
                        "dark pilot already has OIDC state")
        snapshot = docker("exec", db, "pg_dump", "-Fc", "-U", "axxium", "-d", "axxium",
                          reason="private dark database snapshot failed", timeout=60)
        require(snapshot.startswith(b"PGDMP") and len(snapshot) <= 16 * 1024 * 1024,
                "private dark database snapshot differs")
        dark.write_private(LIVE / "dark-postgres.dump", snapshot)
        docker("image", "load", "--input", str(archive), reason="staged image load failed", timeout=180)
        image = dark.verified_image("promethean-axxium:" + plan["source_sha"], plan["image_config_digest"],
                                    plan["image_id"], plan["image_manifest_digest"])
        next_env = re.sub(rb"\AAXXIUM_PILOT_IMAGE=[^\n]+", b"AXXIUM_PILOT_IMAGE=" + image.encode(), environment)
        next_env += (f'AXXIUM_PILOT_SENDER_DID={binding["sender_did"]}\n'
                     f'AXXIUM_PILOT_RECIPIENT_DID={binding["recipient_did"]}\n').encode()
        dark.write_private(LIVE / "candidate.env", next_env)
        dark.write_private(LIVE / "compose.overlay.yaml", (PACKAGE / "activation.compose.yaml").read_bytes())
        (LIVE / "mailbox").mkdir(mode=0o700)
        dark.write_private(LIVE / "mailbox/ledger.edn", b"")
        live_compose("config", "--quiet", reason="pilot Compose overlay rejected")
        save_phase(result, "publish-fence")
        publish_route(fenced, None, view)
        require(public_probe("/health", retry=True) == "503", "pilot TLS fence failed")
        save_phase(result, "start-live-app")
        changed_app = True
        live_compose("up", "-d", "--wait", "--wait-timeout", "180", "--no-deps", "--pull", "never", "axxium",
                     reason="candidate startup failed", timeout=850)
        result["pilot_containers"] = read_live_runtime(receipt, image, plan, binding)
        verify_relay_boundary(view[0])
        verify_relay(stop)
        save_phase(result, "publish-live-route")
        publish_route(active, fenced, view)
        probe_live()
        require((LIVE / "mailbox/ledger.edn").stat().st_size == 0, "preflight changed the fresh mailbox ledger")
        result["incumbent_after"] = dark.incumbent()
        require(result["incumbent_after"] == baseline and caddy_view() == view, "incumbent changed during activation")
        result.update(status="healthy-live", phase="complete", public_ingress=True, mailbox_active=True)
        write_json(LIVE / "active.json", result)
        clear_hold()
    except (dark.Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        result["reason"] = str(error) if isinstance(error, dark.Refused) else "pilot activation failed"
        rollback_activation(receipt, baseline, view, fenced, active, changed_app, error, result)
    return result


def write_json(path, result):
    dark.write_private(path, json_bytes(result))


def clear_hold():
    PENDING.unlink()
    sync_directory(SLOT)


def rollback_activation(receipt, baseline, view, fenced, active, changed_app, error, result):
    try:
        if not absent(ROUTE):
            require(not ROUTE.is_symlink(), "pilot route aliases another path")
            current = ROUTE.read_bytes()
            require(current in (fenced, active), "pilot route changed during rollback")
            if current == active:
                publish_route(fenced, active, view)
    except (dark.Refused, OSError, ValueError, KeyError, TypeError):
        result["route_rollback"] = "unverified"
    try:
        if changed_app:
            result["restored_dark_containers"] = restore_dark(receipt)
        if not absent(ROUTE):
            publish_route(None, fenced, view)
        require(dark.incumbent() == baseline and caddy_view() == view, "incumbent rollback readback differs")
        if not isinstance(error, dark.CommandTimeout):
            result.update(status="rolled-back-dark", phase="complete", public_ingress=False, mailbox_active=False)
            write_json(LIVE / "failed.json", result)
            clear_hold()
    except (dark.Refused, OSError, ValueError, KeyError, TypeError):
        result["rollback"] = "unverified"
    return result


def installed_active_route(active):
    require(not ROUTE.is_symlink() and ROUTE.is_file(), "pilot active route unavailable")
    installed = ROUTE.read_bytes()
    require(installed == active or digest(installed) == PRE_CONSENT_ROUTE_SHA256,
            "installed pilot route differs from both reviewed active routes")
    return installed


def repair_ingress(controller, baseline, view, active):
    original = json.loads(private_read(LIVE / "active.json"))
    plan_bytes = (PACKAGE / "activation-release-plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    require(original["status"] == "healthy-live" and original["origin"] == ORIGIN
            and original["source_sha"] == plan["source_sha"] and original["plan_sha256"] == digest(plan_bytes)
            and original["image_manifest_digest"] == plan["image_manifest_digest"]
            and all(absent(LIVE / name) for name in
                    ("teardown.json", "ingress-repair.json", "ingress-repair-failed.json")),
            "no exact active pilot is eligible for ingress repair")
    previous = installed_active_route(active)
    require(digest(previous) == PRE_CONSENT_ROUTE_SHA256, "the pilot consent route is already updated")
    binding = json.loads(private_read(LIVE / "activation.json"))
    stop = validate_config(binding, json.loads(private_read(LIVE / "oidc/authority.json")),
                           private_read(LIVE / "oidc/webhook-seal.key", 128))
    require(binding["stop_at"] == original["stop_at"], "live pilot deadline differs from activation")
    verify_relay(stop)
    image = dark.verified_image("promethean-axxium:" + plan["source_sha"], plan["image_config_digest"],
                                plan["image_id"], plan["image_manifest_digest"])
    require(read_live_runtime(original, image, plan, binding) == original["pilot_containers"],
            "pilot containers changed after activation")
    result = {"schema": "axxium-work-ingress-repair/v1", "controller_sha": controller,
              "source_sha": plan["source_sha"], "origin": ORIGIN, "status": "held",
              "phase": "before-reload", "stop_at": binding["stop_at"],
              "pilot_containers": original["pilot_containers"],
              "previous_route_sha256": digest(previous), "updated_route_sha256": digest(active)}
    write_json(PENDING, result)
    try:
        publish_route(active, previous, view)
        probe_live()
        require(dark.incumbent() == baseline and caddy_view() == view
                and read_live_runtime(original, image, plan, binding) == original["pilot_containers"],
                "pilot or incumbent changed during ingress repair")
    except (dark.Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        result["reason"] = str(error) if isinstance(error, dark.Refused) else "pilot ingress repair failed"
        try:
            installed = installed_active_route(active)
            if installed == active:
                publish_route(previous, active, view)
            require(dark.incumbent() == baseline and caddy_view() == view, "ingress rollback readback differs")
            if not isinstance(error, (dark.CommandTimeout, subprocess.TimeoutExpired)):
                result.update(status="ingress-rolled-back", phase="complete")
                write_json(LIVE / "ingress-repair-failed.json", result)
                clear_hold()
        except (dark.Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            result["rollback"] = "unverified"
    else:
        result.update(status="ingress-repaired", phase="complete")
        write_json(LIVE / "ingress-repair.json", result)
        clear_hold()
    return result


def verify_teardown_fence():
    require(all(systemd_property(unit, "ActiveState") == "inactive" for unit in (SOCKET, RELAY))
            and public_probe(MCP, method="POST") == "503",
            "operator must close the pilot relay before teardown")


def teardown(controller, receipt, baseline, view, fenced, active):
    original = json.loads(private_read(LIVE / "active.json"))
    plan = json.loads((PACKAGE / "activation-release-plan.json").read_bytes())
    require(original["status"] == "healthy-live" and original["origin"] == ORIGIN
            and original["source_sha"] == plan["source_sha"]
            and original["image_manifest_digest"] == plan["image_manifest_digest"]
            and absent(LIVE / "teardown.json"),
            "no exact active pilot is eligible for teardown")
    verify_teardown_fence()
    result = {"schema": "axxium-work-activation-host/v1", "controller_sha": controller, "status": "held",
              "phase": "before-teardown", "source_sha": original["source_sha"], "origin": ORIGIN,
              "incumbent_before": baseline}
    write_json(PENDING, result)
    try:
        publish_route(fenced, installed_active_route(active), view)
        require(public_probe(MCP, method="POST") == "503", "pilot route did not fence")
        save_phase(result, "restore-dark")
        result["pilot_containers"] = restore_dark(receipt)
        publish_route(None, fenced, view)
        result["incumbent_after"] = dark.incumbent()
        require(result["incumbent_after"] == baseline and caddy_view() == view, "incumbent changed during teardown")
        result.update(status="healthy-dark", phase="complete", public_ingress=False, mailbox_active=False)
        write_json(LIVE / "teardown.json", result)
        clear_hold()
    except (dark.Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        result["reason"] = str(error) if isinstance(error, dark.Refused) else "pilot teardown failed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", required=True, choices=("activate", "repair-ingress", "teardown"))
    parser.add_argument("--controller", required=True)
    args = parser.parse_args()
    try:
        os.umask(0o077)
        require(os.geteuid() == 1000 and re.fullmatch("[0-9a-f]{40}", args.controller),
                "pilot requires the production deploy user and controller identity")
        private_path(SLOT, directory=True)
        private_path(LIVE, directory=True)
        private_path(SLOT / ".lock")
        with (SLOT / ".lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            command(["sh", str(ROOT / "digitalocean/scripts/check-axxium-production-hold.sh")],
                    "a production transition is held")
            receipt, environment = selection()
            baseline, view = dark.incumbent(), caddy_view()
            fenced = (PACKAGE / "ingress/fenced.caddy").read_bytes()
            active = (PACKAGE / "ingress/active.caddy").read_bytes()
            if args.action == "activate":
                result = activate(args.controller, receipt, environment, baseline, view, fenced, active)
            elif args.action == "repair-ingress":
                result = repair_ingress(args.controller, baseline, view, active)
            else:
                result = teardown(args.controller, receipt, baseline, view, fenced, active)
    except (dark.Refused, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        result = {"status": "refused", "reason": str(error) if isinstance(error, dark.Refused)
                  else "pilot host preflight failed"}
    print(json.dumps(result, sort_keys=True))
    success = {"activate": "healthy-live", "repair-ingress": "ingress-repaired", "teardown": "healthy-dark"}
    return 0 if result["status"] == success[args.action] else 1


if __name__ == "__main__":
    sys.exit(main())
