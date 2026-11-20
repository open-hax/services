#!/usr/bin/env python3
"""Activate the fixed Knoxx public policy bundle through the protected host controller."""

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HOST = "knoxx.promethean.rest"
SERVICES_ROOT = Path("/srv/open-hax/services")
STATE_ROOT = Path("/srv/open-hax/state")
FRONTEND = "knoxx-knoxx-frontend-1"
BACKEND = "knoxx-knoxx-backend-1"
CADDY = "caddy-caddy-1"
FRONTEND_IMAGE = "ghcr.io/open-hax/knoxx-frontend"
SNIPPET = "knoxx-public-policy.caddy"
POLICY_DIR = "knoxx-public-policy"
RECEIPT = ".knoxx-public-policy-release.json"
LOCK = ".knoxx-public-policy-release.lock"
MOUNT = "/etc/caddy/managed"
BASE_IMPORT = f"import {MOUNT}/*.caddy".encode()
FRONTEND_DIAL = "knoxx-frontend:80"
RETIREMENT_FRONTEND = "knoxx-policy-retirement-incumbent"
RETIREMENT_DIAL = RETIREMENT_FRONTEND + ":80"
LOGGER = "knoxx_policy_unassigned"
ACCESS_LOGGER = f"http.log.access.{LOGGER}"
HEX = re.compile(r"[0-9a-f]{64}")
SOURCE = re.compile(r"[0-9a-f]{40}")
FRAGMENT = re.compile(r"[A-Za-z0-9_.-]+\.caddy")
HTTP_PATHS = {"/": "homepage.html", "/privacy.html": "privacy.html"}
ASSET_FIELDS = {"link": "href", "script": "src"}
NEIGHBORS = ("/favicon.svg", "/api/auth/config", "/.well-known/oauth-protected-resource")
LIMIT = 8 * 1024 * 1024


class Refused(Exception):
    """A fixed safe message to return to the deployment workflow."""


class ReloadUncertain(Refused):
    """A reload request may have reached Caddy without acknowledgment."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalized_config(config):
    # Caddy numbers handle/rewrite groups by parse order. Compare group
    # membership, and disregard only the adapter's known config-source paths.
    groups = {}
    source_paths = {"/dev/stdin", "/etc/caddy/Caddyfile", f"{MOUNT}/{SNIPPET}"}

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {key: walk(value[key]) for key in sorted(value)}
        if isinstance(group := value.get("group"), str):
            normalized["group"] = groups.setdefault(group, len(groups))
        if value.get("handler") == "file_server" and "hide" in normalized:
            hidden = [item for item in normalized["hide"] if item not in source_paths]
            if hidden:
                normalized["hide"] = hidden
            else:
                del normalized["hide"]
        return normalized

    return walk(config)


def config_digest(config):
    return digest(json.dumps(normalized_config(config), sort_keys=True, separators=(",", ":")).encode())


def unique_keys(pairs):
    obj = dict(pairs)
    require(len(obj) == len(pairs), "duplicate JSON field")
    return obj


def directory(path):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_dir(),
            "noncanonical release directory")
    return path


@contextmanager
def host_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(fd)
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid()
                and not metadata.st_mode & 0o077, "unsafe policy lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("another policy controller is active on the host") from None
        yield
    finally:
        os.close(fd)


def read_file(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as src:
        require(stat.S_ISREG(os.fstat(src.fileno()).st_mode), "nonregular release file")
        data = src.read(LIMIT + 1)
    require(len(data) <= LIMIT, "release file exceeds size limit")
    return data


def read_optional(path):
    try:
        return read_file(path)
    except FileNotFoundError:
        return None


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path, data, mode, *, replace=True, staging=None):
    with tempfile.NamedTemporaryFile(dir=staging or path.parent, prefix=".policy-stage-", delete=False) as temp:
        tmp = Path(temp.name)
        try:
            os.fchmod(temp.fileno(), mode)
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
            if replace:
                os.replace(tmp, path)
            else:
                os.link(tmp, path)
            sync_dir(path.parent)
        finally:
            tmp.unlink(missing_ok=True)


class Homepage(HTMLParser):
    def __init__(self, data):
        super().__init__()
        self.mounts = 0
        self.assets = []
        self.privacy_links = 0
        self.feed(data.decode("utf-8"))
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "root":
            self.mounts += 1
        if tag in ASSET_FIELDS and (value := attrs.get(ASSET_FIELDS[tag])):
            self.assets.append((tag, value))
        if tag == "a" and attrs.get("href") == "/privacy.html":
            self.privacy_links += 1


def load_bundle(bundle, services_root):
    directory(bundle)
    require(bundle.is_relative_to(services_root) and bundle != services_root,
            "bundle must be staged under canonical services root")
    require(bundle.stat().st_uid == os.geteuid(), "staged bundle is not owned by the deployment identity")
    require({p.name for p in bundle.iterdir()} == {"plan.json", "homepage.html", "privacy.html", SNIPPET},
            "unexpected policy bundle contents")
    raw_plan = read_file(bundle / "plan.json")
    plan = json.loads(raw_plan, object_pairs_hook=unique_keys)
    file_hashes = {"homepage.html": "homepage_sha256", "privacy.html": "privacy_sha256", SNIPPET: "snippet_sha256"}
    require(isinstance(plan, dict) and set(plan) == {
        "host", "frontend_image_digest", "incumbent_index_sha256", "knoxx_source_sha", *file_hashes.values(),
    }, "invalid policy plan fields")
    require(plan["host"] == HOST and all(isinstance(value, str) for value in plan.values()),
            "invalid policy plan host or value")
    require(plan["frontend_image_digest"].startswith("sha256:")
            and HEX.fullmatch(plan["frontend_image_digest"][7:])
            and SOURCE.fullmatch(plan["knoxx_source_sha"])
            and all(HEX.fullmatch(plan[field]) for field in ("incumbent_index_sha256", *file_hashes.values())),
            "invalid policy plan pin")
    artifacts = {name: read_file(bundle / name) for name in file_hashes}
    require(all(artifacts[name] and digest(artifacts[name]) == plan[field]
                for name, field in file_hashes.items()), "policy bundle digest mismatch")
    return plan, digest(raw_plan), artifacts


def shell(args, label, data=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=45, check=False)
    require(result.returncode == 0, label)
    return result.stdout


def routes(config):
    return config["apps"]["http"]["servers"]


def child_routes(route):
    handler, = route["handle"]
    require(set(handler) == {"handler", "routes"} and handler["handler"] == "subroute",
            "unexpected site route handler")
    return handler["routes"]


def policy_paths(route):
    matchers = route.get("match", [])
    if not matchers or not all(set(m) == {"host", "path"} and m["host"] == [HOST]
                               and len(m["path"]) == 1 and m["path"][0] in HTTP_PATHS
                               for m in matchers):
        return set()
    return {m["path"][0] for m in matchers}


def without_group(route):
    return {key: value for key, value in route.items() if key != "group"}


def verify_policy_handlers(route, paths, incumbent):
    require(set(route) == {"match", "handle", "terminal"} and route["terminal"] is True,
            "policy route must be terminal")
    current = child_routes(incumbent)
    proposed = child_routes(route)
    fallback = current[-1]
    require(not fallback.get("match") and without_group(proposed[-1]) == without_group(fallback),
            "non-GET/HEAD fallback differs from incumbent")
    require(proposed[0] == current[0] and
            sorted(part["handler"] for part in proposed[0]["handle"]) == ["encode", "headers"],
            "policy security headers or encoding differ from incumbent")
    branches = proposed[1:-1]
    require(len(branches) == len(paths), "unexpected policy method handlers")
    found = set()
    for branch in branches:
        matcher, = branch["match"]
        require(set(matcher) == {"method", "path"} and matcher["method"] == ["GET", "HEAD"]
                and len(matcher["path"]) == 1 and matcher["path"][0] in paths,
                "unexpected policy path or method")
        path, = matcher["path"]
        found.add(path)
        require(branch.get("group") == proposed[-1].get("group") and branch.get("group"),
                "policy fallback is not mutually exclusive")
        actual = child_routes(branch)
        require(all("handle" in part and set(part) <= {"handle", "group"} for part in actual),
                "unexpected static handler match")
        handlers = [part for step in actual for part in step["handle"]]
        expected = [{"handler": "vars", "root": f"{MOUNT}/{POLICY_DIR}"}]
        if path != "/" + HTTP_PATHS[path]:
            expected.append({"handler": "rewrite", "uri": "/" + HTTP_PATHS[path]})
        require(handlers[:-1] == expected
                and handlers[-1].get("handler") == "file_server"
                and set(handlers[-1]) <= {"handler", "hide"},
                "policy route does not serve the fixed artifact")
        require(all(isinstance(value, str) and
                    (value in ("/dev/stdin", "/etc/caddy/Caddyfile") or value.startswith(f"{MOUNT}/"))
                    for value in handlers[-1].get("hide", [])), "unexpected static handler hide rules")
    require(found == paths, "policy paths differ from the plan")


def verify_route_delta(incumbent, proposed):
    stripped = deepcopy(proposed)
    added = []
    for name, server in routes(stripped).items():
        retained = []
        for route in server["routes"]:
            if paths := policy_paths(route):
                added.append((name, len(retained), route, paths))
            else:
                retained.append(route)
        server["routes"] = retained

    removed = [path for _, _, _, paths in added for path in paths]
    require(sorted(removed) == sorted(HTTP_PATHS), "policy route delta must contain exactly two paths")
    for name, position, route, paths in added:
        original = routes(incumbent)[name]["routes"]
        indices = [i for i, baseline in enumerate(original)
                   if baseline.get("match") == [{"host": [HOST]}]]
        require(len(indices) == 1 and position <= indices[0], "policy route does not precede Knoxx fallback")
        verify_policy_handlers(route, paths, original[indices[0]])

    logs = stripped.get("logging", {}).get("logs", {})
    policy_log = logs.pop(LOGGER, None)
    require(policy_log is None or policy_log.get("include") == [ACCESS_LOGGER],
            "policy logger is not isolated")
    exclusions = logs.get("default", {}).get("exclude", [])
    if ACCESS_LOGGER in exclusions:
        exclusions.remove(ACCESS_LOGGER)
    require(normalized_config(stripped) == normalized_config(incumbent),
            "unrelated Caddy configuration or logging changed")


def retirement_bridge(base, snippet):
    """Build the one-file restart-safe bridge from the exact mounted inputs."""
    host_block = b"{$CADDY_KNOXX_HOST} {"
    sites = re.findall(rb"^" + re.escape(host_block) + rb"\n.*?^}\n", base,
                       flags=re.MULTILINE | re.DOTALL)
    require(len(sites) == 1, "mounted Knoxx site is missing or ambiguous")
    site, = sites
    common = re.findall(rb"^\(common\) \{\n(.*?)^}\n", base, flags=re.MULTILINE | re.DOTALL)
    require(len(common) == 1 and site.count(b"\timport common\n") == 1,
            "mounted Knoxx common handling is missing or ambiguous")
    # The base host already supplies access logging for this hostname. Use
    # the overlay's no-host logger instead of adding or disabling a host logger.
    shared, count = re.subn(rb"^\tlog \{\n.*?^\t}\n", b"", common[0],
                            flags=re.MULTILINE | re.DOTALL)
    require(count == 1, "expected exactly one incumbent common logger")
    shared += f"\tlog {LOGGER} {{\n\t\tno_hostname\n\t}}\n".encode()
    upstream = b"reverse_proxy " + FRONTEND_DIAL.encode() + b" {"
    target = b"reverse_proxy " + RETIREMENT_DIAL.encode() + b" {"
    require(site.count(upstream) == snippet.count(upstream) == 1,
            "expected exactly one frontend proxy in each Knoxx route")
    wildcard = site.replace(host_block, b"{$CADDY_KNOXX_HOST}/* {", 1)
    wildcard = wildcard.replace(b"\timport common\n", shared, 1).replace(upstream, target, 1)
    return snippet.replace(upstream, target, 1) + b"\n" + wildcard


def replace_frontend_dial(route, old, new):
    """Retarget exactly one known proxy in a copied adapted site route."""
    pending = [route]
    matched = []
    while pending:
        part = pending.pop()
        if isinstance(part, list):
            pending.extend(part)
        elif isinstance(part, dict):
            if part.get("handler") == "reverse_proxy":
                matched.extend(upstream for upstream in part.get("upstreams", [])
                               if upstream.get("dial") == old)
            pending.extend(part.values())
    require(len(matched) == 1, "adapted Knoxx route does not contain its single frontend target")
    matched[0]["dial"] = new


def verify_retirement_bridge(native, active, bridge):
    """Prove only the fixed Knoxx ingress is redirected during replacement."""
    verify_route_delta(native, active)
    restored = deepcopy(bridge)
    temporary_match = [{"host": [HOST], "path": ["/*"]}]
    base_match = [{"host": [HOST]}]
    temporary_routes = [(server, index, route)
                        for server in routes(restored).values()
                        for index, route in enumerate(server["routes"])
                        if route.get("match") == temporary_match]
    require(len(temporary_routes) == 1, "retirement must add one temporary Knoxx wildcard route")
    server, index, temporary = temporary_routes[0]
    ordered = server["routes"]
    policies = [(position, route) for position, route in enumerate(ordered) if policy_paths(route)]
    bases = [(position, route) for position, route in enumerate(ordered) if route.get("match") == base_match]
    require(len(bases) == 1 and policies
            and all(position < index < bases[0][0] for position, _ in policies),
            "retirement bridge must follow policy routes and precede native routing")
    expected = deepcopy(bases[0][1])
    expected["match"] = temporary_match
    replace_frontend_dial(expected, FRONTEND_DIAL, RETIREMENT_DIAL)
    require(normalized_config(temporary) == normalized_config(expected),
            "retirement bridge changed the native Knoxx handlers")
    ordered.pop(index)
    for _, route in policies:
        replace_frontend_dial(route, RETIREMENT_DIAL, FRONTEND_DIAL)
    require(normalized_config(restored) == normalized_config(active),
            "retirement bridge changed unrelated ingress or policy handlers")


# On-disk Caddy stages precede their corresponding reload. During a reload
# the old config may still be live. A restart takes the disk branch instead.
RETIREMENT_READBACKS = {
    "prepared": (("active", "active"),),
    "install_bridge": (("active", "active"), ("bridge", "active"), ("bridge", "bridge")),
    "switch_frontend": (("bridge", "bridge"),),
    "install_native": (("bridge", "bridge"), ("native", "bridge"), ("native", "native")),
    "retired": (("native", "native"),),
    "restore_bridge": (("native", "native"), ("bridge", "native"), ("bridge", "bridge")),
    "restore_frontend": (("bridge", "bridge"),),
    "restore_active": (("bridge", "bridge"), ("active", "bridge"), ("active", "active")),
    "restored": (("active", "active"),),
}


def retirement_record(active_receipt, configs, controller_sha):
    """Describe a prospective transition while retaining the exact v1 receipt."""
    prior = json.loads(active_receipt, object_pairs_hook=unique_keys)
    hashes = {state: config_digest(config) for state, config in configs.items()}
    require(set(hashes) == {"native", "active", "bridge"} and len(set(hashes.values())) == 3,
            "ambiguous retirement configuration identities")
    require(isinstance(prior, dict) and prior.get("version", 1) == 1 and prior.get("phase") == "active"
            and prior.get("incumbent_config_sha256") == hashes["native"]
            and prior.get("candidate_config_sha256") == hashes["active"]
            and SOURCE.fullmatch(controller_sha), "retirement does not match an active receipt")
    require(all(isinstance(prior.get(key), str) and regex.fullmatch(prior[key])
                for key, regex in (("controller_sha", SOURCE), ("plan_sha256", HEX))),
            "invalid active policy receipt provenance")
    return {"version": 2, "phase": "prepared", "controller_sha": controller_sha,
            "active_receipt_sha256": digest(active_receipt), "configs": hashes}


def verify_retirement_checkpoint(receipt, disk, live, frontend, *, shadow,
                                 frontend_verified=False, archive_verified=False):
    """Require a known restart-safe observation for the journaled step."""
    phase = receipt.get("phase")
    require(receipt.get("version") == 2 and phase in RETIREMENT_READBACKS
            and all(isinstance(receipt.get(key), str) and pattern.fullmatch(receipt[key])
                    for key, pattern in (("active_receipt_sha256", HEX), ("controller_sha", SOURCE))),
            "invalid retirement journal phase")
    configs = receipt.get("configs", {})
    require(set(configs) == {"native", "active", "bridge"} and
            all(isinstance(value, str) and HEX.fullmatch(value) for value in configs.values()) and
            len(set(configs.values())) == 3, "invalid retirement configuration identity")
    require((disk, live) in {(configs[left], configs[right]) for left, right in RETIREMENT_READBACKS[phase]},
            "retirement interrupted in an unknown ingress state")
    if phase == "prepared":
        require(frontend == "incumbent" and frontend_verified and shadow == "absent",
                "active frontend must be verified before preparing a retirement")
    elif phase in {"retired", "restored"}:
        expected = "candidate" if phase == "retired" else "incumbent"
        require(frontend == expected and frontend_verified and archive_verified and shadow == "absent",
                "retirement terminal state is not fully recorded and drained")
    else:
        require(shadow == "ready", "retirement requires a verified incumbent recovery frontend")
        if phase == "install_bridge":
            require(frontend == "incumbent" and frontend_verified,
                    "the original frontend must remain live until the bridge is installed")
        elif phase in {"install_native", "restore_bridge", "restore_active"}:
            expected = "incumbent" if phase == "restore_active" else "candidate"
            require(frontend == expected and frontend_verified,
                    "frontend must be verified before changing the bridge route")
        else:
            require(frontend in {"incumbent", "candidate", "replacing"},
                    "unrecognized frontend during the bridged replacement")


def compose_candidate(base, managed):
    lines = base.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.rstrip(b"\r\n") == BASE_IMPORT]
    require(len(matches) == 1, "mounted Caddyfile managed import differs")
    names = sorted({p.name for p in managed.glob("*.caddy") if p.name != SNIPPET} | {SNIPPET})
    for name in names:
        require(FRAGMENT.fullmatch(name), "unexpected managed Caddy fragment name")
        if name != SNIPPET:
            read_file(managed / name)
    return lines, matches[0], names


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path, method="GET"):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    query = Request("https://" + HOST + path, method=method, headers={"Accept-Encoding": "identity"})
    try:
        response = opener.open(query, timeout=12)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(LIMIT + 1)
        require(len(body) <= LIMIT, "public verification response exceeded size limit")
        return {"status": response.code, "type": response.headers.get_content_type(), "sha256": digest(body)}


class Host:
    def __init__(self, services_root, state_root):
        self.base = directory(services_root / "caddy") / "Caddyfile"
        self.managed = directory(state_root / "caddy" / "managed")
        self.artifacts = self.managed / POLICY_DIR
        self.snippet = self.managed / SNIPPET
        self.receipt = self.managed.parent / RECEIPT
        self.lock = self.managed.parent / LOCK
        require(self.managed.stat().st_dev == self.receipt.parent.stat().st_dev,
                "policy staging and managed ingress must share a filesystem")

    def docker(self, args, label, data=None):
        return shell(["docker", *args], label, data)

    def exec(self, name, args, label, data=None):
        return self.docker(["exec", *(["-i"] if data is not None else []), name, *args], label, data)

    def inspect(self, name):
        fields = ("Id", "Image", "Config.Image", "State.Status", "State.StartedAt", "RestartCount", "Mounts")
        fmt = "\n".join("{{json ." + field + "}}" for field in fields)
        result = self.docker(["inspect", "--type", "container", "--format", fmt, name],
                             "container identity probe failed")
        return dict(zip(fields, map(json.loads, result.splitlines()), strict=True))

    def identities(self, plan):
        items = {name: self.inspect(name) for name in (FRONTEND, BACKEND, CADDY)}
        require(all(item["State.Status"] == "running" for item in items.values()),
                "incumbent services must be running")
        front = items[FRONTEND]
        require(re.fullmatch(re.escape(FRONTEND_IMAGE) + r"(?::[0-9a-f]{40}|@sha256:[0-9a-f]{64})",
                             front["Config.Image"]), "live frontend image repository differs")
        names = self.docker(["image", "inspect", "--format", "{{json .RepoDigests}}", front["Image"]],
                            "frontend image digest probe failed")
        require(FRONTEND_IMAGE + "@" + plan["frontend_image_digest"] in json.loads(names),
                "live frontend digest differs")
        mounts = {item["Destination"]: item for item in items[CADDY]["Mounts"]}
        for path, source in (("/etc/caddy/Caddyfile", self.base), (MOUNT, self.managed)):
            mount = mounts.get(path, {})
            require(mount.get("Source") == str(source) and mount.get("Type") == "bind"
                    and mount.get("RW") is False, "Caddy mount differs from the production contract")
        version = self.exec(CADDY, ["caddy", "version"], "Caddy version probe failed")
        require(version.split()[:1] == [b"v2.8.4"], "Caddy version requires requalification")
        return {name: {field: items[name][field] for field in ("Id", "Image", "State.StartedAt", "RestartCount")}
                for name in items}

    def index(self):
        return self.exec(FRONTEND, ["cat", "/usr/share/nginx/html/index.html"], "frontend index probe failed")

    def current(self):
        raw = self.exec(CADDY, ["wget", "-q", "-O", "-", "http://127.0.0.1:2019/config/"],
                        "active Caddy configuration probe failed")
        return json.loads(raw)

    def startup_config(self):
        raw = self.exec(CADDY, ["caddy", "adapt", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                        "mounted Caddyfile cannot be adapted")
        return json.loads(raw)

    def shadow_absent(self):
        found = self.docker(["ps", "--all", "--filter", f"name=^/{RETIREMENT_FRONTEND}$",
                             "--format", "{{.ID}}"], "retirement frontend inventory failed")
        return not found.strip()

    def adapt(self, data):
        raw = self.exec(CADDY, ["caddy", "adapt", "--config", "/dev/stdin", "--adapter", "caddyfile"],
                        "Caddy adaptation rejected the release configuration", data)
        return json.loads(raw)

    def configs(self, snippet):
        base = read_file(self.base)
        mounted = self.exec(CADDY, ["cat", "/etc/caddy/Caddyfile"], "mounted Caddyfile probe failed")
        require(base == mounted, "mounted Caddyfile bytes differ from the host file")
        lines, index, names = compose_candidate(base, self.managed)
        # Preserve incumbent glob ordering and exclude the fixed policy target during recovery.
        before = [f"import {MOUNT}/{name}\n".encode() for name in names if name != SNIPPET]
        after = [snippet + b"\n" if name == SNIPPET else f"import {MOUNT}/{name}\n".encode()
                 for name in names]
        self.incumbent_input = b"".join(lines[:index] + before + lines[index + 1:])
        proposed_input = b"".join(lines[:index] + after + lines[index + 1:])
        return self.adapt(self.incumbent_input), self.adapt(proposed_input)

    def reload(self, *, incumbent=False):
        # Keep the exact on-disk policy intact until a compensating reload is
        # acknowledged. A timed-out Docker client can outlive our observation.
        path = "/dev/stdin" if incumbent else "/etc/caddy/Caddyfile"
        self.exec(CADDY, ["caddy", "reload", "--config", path, "--adapter", "caddyfile"],
                  "Caddy rejected activation or rollback", self.incumbent_input if incumbent else None)

    def public(self):
        response = {path: request(path) for path in (*HTTP_PATHS, *NEIGHBORS)}
        response.update({f"HEAD {path}": request(path, "HEAD") for path in HTTP_PATHS})
        return response


def verify_public(before, after, artifacts):
    for path, name in HTTP_PATHS.items():
        require(after[path] == {"status": 200, "type": "text/html", "sha256": digest(artifacts[name])}
                and after[f"HEAD {path}"] == {"status": 200, "type": "text/html", "sha256": digest(b"")},
                "public policy or homepage response did not match the exact artifact")
    require(all(after[path] == before[path] for path in NEIGHBORS),
            "neighboring public route changed")


def exact_disk(host, artifacts):
    if host.artifacts.exists() or host.artifacts.is_symlink():
        directory(host.artifacts)
        require({p.name for p in host.artifacts.iterdir()} <= set(HTTP_PATHS.values()),
                "unknown files in policy artifact directory")
    paths = {name: host.artifacts / name for name in HTTP_PATHS.values()}
    paths[SNIPPET] = host.snippet
    disk = {name: read_optional(path) for name, path in paths.items()}
    require(all(value is None or value == artifacts[name] for name, value in disk.items()),
            "unknown or stale policy bytes on disk")
    return paths, disk


def prepare_retirement(host, plan, plan_hash, artifacts, controller_sha):
    """Read and bind the exact incumbent. No deployment action consumes this result yet."""
    stat_info = host.receipt.lstat()
    require(stat.S_ISREG(stat_info.st_mode) and stat_info.st_uid == os.geteuid()
            and not stat_info.st_mode & 0o077, "unsafe active policy receipt")
    active_receipt = read_file(host.receipt)
    prior = json.loads(active_receipt, object_pairs_hook=unique_keys)
    require(isinstance(prior, dict) and prior.get("phase") == "active"
            and prior.get("plan_sha256") == plan_hash, "policy bundle does not match the active receipt")
    initial = host.identities(plan)
    for name in (FRONTEND, BACKEND, CADDY):
        require(all(initial[name][field] == prior.get("containers", {}).get(name, {}).get(field)
                    for field in ("Id", "Image")), "active policy container or image identity has changed")
    require(digest(host.index()) == plan["incumbent_index_sha256"], "incumbent frontend index changed")
    _, disk = exact_disk(host, artifacts)
    require(all(value is not None for value in disk.values()), "active policy artifacts are incomplete")
    native, active = host.configs(artifacts[SNIPPET])
    same_native, bridge = host.configs(retirement_bridge(read_file(host.base), artifacts[SNIPPET]))
    require(config_digest(same_native) == config_digest(native), "managed ingress changed during preflight")
    verify_retirement_bridge(native, active, bridge)
    journal = retirement_record(active_receipt, {"native": native, "active": active, "bridge": bridge},
                                controller_sha)
    verify_public(prior["public_before"], host.public(), artifacts)
    require(host.identities(plan) == initial and read_file(host.receipt) == active_receipt
            and exact_disk(host, artifacts)[1] == disk and host.shadow_absent(),
            "active policy changed during preflight")
    verify_retirement_checkpoint(journal, config_digest(host.startup_config()), config_digest(host.current()),
                                 "incumbent", shadow="absent", frontend_verified=True)
    return {**journal, "result": "incumbent_verified", "plan_sha256": plan_hash, "containers": initial}


def record(path, value):
    write_atomic(path, (json.dumps(value, sort_keys=True) + "\n").encode(), 0o600)


def reload_checked(host, *, incumbent=False):
    try:
        host.reload(incumbent=incumbent)
    except Exception:
        raise ReloadUncertain("Caddy reload outcome is unknown; exact files and prepared receipt retained") from None


def restore(host, plan, artifacts, paths, key, before, *, retry):
    before_hash = key["incumbent_config_sha256"]
    after_hash = key["candidate_config_sha256"]
    current = config_digest(host.current())
    require(current in (before_hash, after_hash) and host.identities(plan) == key["containers"],
            "rollback would overwrite unknown runtime state")
    exact_disk(host, artifacts)
    record(host.receipt, {**key, "phase": "prepared", "public_before": before, "reload_intent": "rollback"})
    if retry or current != before_hash:
        reload_checked(host, incumbent=True)
    require(config_digest(host.current()) == before_hash and host.public() == before,
            "rollback did not restore incumbent Caddy and public responses")
    host.snippet.unlink(missing_ok=True)
    for name in HTTP_PATHS.values():
        paths[name].unlink(missing_ok=True)
    if host.artifacts.exists():
        sync_dir(host.artifacts)
        host.artifacts.rmdir()
    sync_dir(host.managed)
    record(host.receipt, {**key, "phase": "rolled_back", "public_before": before})


def release(host, plan, plan_hash, artifacts, controller_sha):
    initial = host.identities(plan)
    original_index = host.index()
    old_home, new_home = Homepage(original_index), Homepage(artifacts["homepage.html"])
    require(digest(original_index) == plan["incumbent_index_sha256"]
            and old_home.mounts == new_home.mounts == 1 and new_home.assets == old_home.assets
            and new_home.privacy_links == 1, "homepage would change the incumbent app shell")
    incumbent, proposed = host.configs(artifacts[SNIPPET])
    verify_route_delta(incumbent, proposed)
    before_hash, after_hash = config_digest(incumbent), config_digest(proposed)
    current = config_digest(host.current())
    require(current in (before_hash, after_hash), "active Caddy configuration has unknown drift")
    paths, disk = exact_disk(host, artifacts)
    key = {"controller_sha": controller_sha, "plan_sha256": plan_hash,
           "incumbent_config_sha256": before_hash, "candidate_config_sha256": after_hash,
           "containers": initial}
    saved = read_optional(host.receipt)
    intent = None
    if saved is None:
        require(current == before_hash and all(value is None for value in disk.values())
                and not host.artifacts.exists(), "unreceipted policy release state")
        before = host.public()
        require(before["/"] == {"status": 200, "type": "text/html", "sha256": digest(original_index)},
                "public homepage differs from incumbent frontend")
        record(host.receipt, {**key, "phase": "prepared", "public_before": before, "reload_intent": None})
    else:
        prior = json.loads(saved, object_pairs_hook=unique_keys)
        require(all(prior.get(field) == value for field, value in key.items()),
                "release receipt belongs to another controller, incumbent, or bundle")
        before = prior["public_before"]
        if prior.get("phase") == "active":
            require(current == after_hash and all(value is not None for value in disk.values()),
                    "recorded active release differs from disk or live Caddy")
            after = host.public()
            verify_public(before, after, artifacts)
            return {**key, "result": "already_active", "public_after": after}
        require(prior.get("phase") in ("prepared", "rolled_back"), "unknown release receipt phase")
        if prior["phase"] == "rolled_back":
            require(current == before_hash and all(value is None for value in disk.values())
                    and not host.artifacts.exists(), "recorded rollback differs from disk or live Caddy")
            record(host.receipt, {**key, "phase": "prepared", "public_before": before, "reload_intent": None})
        else:
            require("reload_intent" in prior and prior["reload_intent"] in (None, "activate", "rollback"),
                    "unknown prepared reload intent")
            intent = prior["reload_intent"]
            if intent is None:
                require(current == before_hash, "policy activated without a recorded reload intent")
            elif intent == "activate":
                require(all(value is not None for value in disk.values()), "prepared activation files are incomplete")

    if intent == "rollback":
        restore(host, plan, artifacts, paths, key, before, retry=True)
        raise Refused("interrupted policy rollback completed and the incumbent was restored")

    try:
        if not host.artifacts.exists():
            host.artifacts.mkdir(mode=0o755)
            sync_dir(host.managed)
        # The protected workflow uses umask 077. Public content is readable
        # through the read-only mount by an unprivileged ingress process.
        os.chmod(host.artifacts, 0o755, follow_symlinks=False)
        for name, path in paths.items():
            if disk[name] is None:
                write_atomic(path, artifacts[name], 0o644, replace=False, staging=host.receipt.parent)
        record(host.receipt, {**key, "phase": "prepared", "public_before": before, "reload_intent": "activate"})
        reload_checked(host)
        require(config_digest(host.current()) == after_hash, "activated Caddy configuration differs")
        require(host.identities(plan) == initial, "application container identities changed")
        after = host.public()
        verify_public(before, after, artifacts)
        record(host.receipt, {**key, "phase": "active", "public_before": before, "public_after": after})
        return {**key, "result": "active", "public_after": after}
    except ReloadUncertain:
        raise
    except Exception:
        if intent == "activate":
            # An earlier activation request may still arrive. A retry can
            # converge in the same direction but must not race it with rollback.
            raise Refused("retried activation did not verify; prepared state retained for reconciliation") from None
        try:
            restore(host, plan, artifacts, paths, key, before, retry=False)
        except ReloadUncertain:
            raise
        except Exception:
            raise Refused("policy release failed and rollback requires reconciliation") from None
        raise Refused("policy release failed and the incumbent was restored") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--services-root", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--controller-sha", required=True)
    parser.add_argument("--prepare-retirement", action="store_true", help="Inspect the incumbent without changing it")
    args = parser.parse_args()
    try:
        require(args.services_root == SERVICES_ROOT and args.state_root == STATE_ROOT
                and SOURCE.fullmatch(args.controller_sha), "invalid production roots or controller SHA")
        directory(args.services_root)
        directory(args.state_root)
        plan, plan_hash, artifacts = load_bundle(args.bundle, args.services_root)
        host = Host(args.services_root, args.state_root)
        with host_lock(host.lock):
            run = prepare_retirement if args.prepare_retirement else release
            result = run(host, plan, plan_hash, artifacts, args.controller_sha)
        print(json.dumps(result, sort_keys=True))
    except Refused as error:
        print(f"Knoxx policy release refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Knoxx policy release failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
