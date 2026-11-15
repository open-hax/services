"""Observe and publish the admitted Axxium Caddy config under the native hold."""

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

import axxium_minimal_release as production


ROUTE_NAME = "axxium-production.caddy"
ROOT_PATH = "/etc/caddy/Caddyfile"
MANAGED_PATH = "/etc/caddy/managed"
PROSPECTIVE_PATH = "/dev/stdin"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\.caddy")
INSPECT = '{"id":{{json .Id}},"running":{{json .State.Running}},"mounts":{{json .Mounts}}}'
FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True)
class ConfigFiles:
    root: bytes
    routes: dict
    root_inode: tuple
    managed_inode: tuple

    def receipt(self, startup, active):
        return {"root_sha256": production.digest(self.root),
                "managed": {name: production.digest(value) for name, value in sorted(self.routes.items())},
                "startup_sha256": startup, "active_sha256": active}


def _inode(info):
    return info.st_dev, info.st_ino


def _read_file(path):
    with os.fdopen(os.open(path, os.O_RDONLY | FILE_FLAGS), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & stat.S_IWOTH:
            raise production.Refused("Caddy source file protection differs")
        data = stream.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES or _inode(path.lstat()) != _inode(info):
            raise production.Refused("Caddy source file changed or exceeds the limit")
        return data, _inode(info)


def _files(root, managed):
    if not root.is_absolute() or not managed.is_absolute() or root.resolve() != root or managed.resolve() != managed:
        raise production.Refused("Caddy bind sources must be canonical host paths")
    managed_info = managed.lstat()
    if not stat.S_ISDIR(managed_info.st_mode) or managed_info.st_mode & stat.S_IWOTH:
        raise production.Refused("Caddy managed directory protection differs")
    root_bytes, root_inode = _read_file(root)
    routes = {}
    for path in sorted(managed.glob("*.caddy")):
        if not NAME.fullmatch(path.name):
            raise production.Refused("Caddy managed route name differs")
        routes[path.name], _ = _read_file(path)
    if _inode(managed_info) != _inode(managed.lstat()):
        raise production.Refused("Caddy managed directory changed during readback")
    return ConfigFiles(root_bytes, routes, root_inode, _inode(managed_info))


def _exec(host, container, *args, data=None, effect=False):
    command = ["docker", "exec", *(["-i"] if data is not None else []), container, *args]
    return host.command(command, data=data, effect=effect)


def _visible(host, container, root, managed, container_root, container_managed, files):
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise production.Refused("Expected an exact Caddy container ID")
    expected = {PurePosixPath(container_root): str(root), PurePosixPath(container_managed): str(managed)}
    raw_paths = (container_root, container_managed)
    if len(expected) != 2 or any(not path.is_absolute() or ".." in path.parts or str(path) != raw
                                 for path, raw in zip(expected, raw_paths)):
        raise production.Refused("Caddy container paths differ")
    inspect = json.loads(host.command(["docker", "inspect", "--type=container", "--format", INSPECT, container]))
    if inspect["id"] != container or inspect["running"] is not True:
        raise production.Refused("Running Caddy container identity differs")
    sources = {}
    for mount in inspect["mounts"]:
        destination = PurePosixPath(mount["Destination"])
        if destination in expected:
            if destination in sources or mount["Type"] != "bind" or mount["RW"] is not False:
                raise production.Refused("Caddy config mount is not the exact read-only bind")
            sources[destination] = mount["Source"]
        elif any(path in destination.parents or destination in path.parents for path in expected):
            raise production.Refused("Unexpected mount overlaps the Caddy config binds")
    if sources != expected:
        raise production.Refused("Caddy config bind sources differ")
    if _exec(host, container, "cat", container_root) != files.root:
        raise production.Refused("Caddy root bind bytes differ")
    listing = _exec(host, container, "find", container_managed, "-mindepth", "1", "-maxdepth", "1",
                    "-name", "*.caddy", "-print0")
    paths = [(container_managed + "/" + name).encode() for name in sorted(files.routes)]
    if listing and not listing.endswith(b"\0"):
        raise production.Refused("Caddy managed bind inventory is incomplete")
    if sorted(listing.split(b"\0")[:-1]) != paths:
        raise production.Refused("Caddy managed bind inventory differs")
    for name, value in files.routes.items():
        if _exec(host, container, "cat", container_managed + "/" + name) != value:
            raise production.Refused("Caddy managed bind bytes differ")


def _config(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise production.Refused("Caddy configuration observation must be an object")
    return value


def _config_hash(value):
    return production.digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _adapted(host, container, path, *, data=None):
    return _config(_exec(host, container, "caddy", "adapt", "--adapter", "caddyfile", "--config", path, data=data))


def _active(host, container):
    raw = _exec(host, container, "wget", "-Y", "off", "-q", "-O", "-", "http://127.0.0.1:2019/config/")
    return _config_hash(_config(raw))


def _without_adapter_hide_paths(value, source_paths):
    if isinstance(value, list):
        return [_without_adapter_hide_paths(item, source_paths) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _without_adapter_hide_paths(item, source_paths) for key, item in value.items()}
    # Caddy hides the source files used to expand a file_server route.
    # Keep explicit hide entries and the actual paths of unchanged siblings.
    if value.get("handler") == "file_server" and isinstance(hidden := value.get("hide"), list):
        retained = [entry for entry in hidden if not isinstance(entry, str) or entry not in source_paths]
        if retained != hidden:
            if retained:
                result["hide"] = retained
            else:
                del result["hide"]
    return result


def readback(host, container, root, managed, *, container_root=ROOT_PATH, container_managed=MANAGED_PATH):
    """Hash the exact read-only binds, adapted disk config, and live admin config."""
    try:
        root, managed = Path(root), Path(managed)
        host.recheck_lock()
        files = _files(root, managed)
        _visible(host, container, root, managed, container_root, container_managed, files)
        startup = _config_hash(_adapted(host, container, container_root))
        active = _active(host, container)
        host.recheck_lock()
        if _files(root, managed) != files:
            raise production.Refused("Caddy source files changed during readback")
        return files.receipt(startup, active)
    except (KeyError, OSError, TypeError, ValueError):
        raise production.Refused("Caddy configuration readback unavailable") from None


def _render_validation(root_bytes, routes, container_managed, route_name):
    marker = f"import {container_managed}/*.caddy".encode()
    lines = root_bytes.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if line.rstrip(b"\r\n") == marker]
    if len(matches) != 1:
        raise production.Refused("Caddy managed wildcard import differs")
    # Existing siblings remain mounted during validation. Inline only the
    # proposed owned route, at the same position as the sorted wildcard.
    lines[matches[0]] = b"".join(
        routes[name] + b"\n" if name == route_name
        else f"import {container_managed}/{name}\n".encode()
        for name in sorted(routes)
    )
    return b"".join(lines)


def _write_root(path, before, data, host):
    with os.fdopen(os.open(path, os.O_RDWR | FILE_FLAGS), "r+b") as stream:
        fd = stream.fileno()
        host.recheck_lock()
        if (_inode(os.fstat(fd)) != before.root_inode or _inode(path.lstat()) != before.root_inode
                or stream.read(MAX_CONFIG_BYTES + 1) != before.root):
            raise production.Uncertain("Caddy root changed before in-place publication")
        stream.seek(0)
        stream.write(data)
        stream.truncate()
        stream.flush()
        os.fsync(fd)


def _write_route(path, data, *, exists):
    if data is None:
        path.unlink()
    else:
        mode = stat.S_IMODE(path.lstat().st_mode) if exists else 0o644
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".axxium-ingress-", delete=False) as staged:
            try:
                os.fchmod(staged.fileno(), mode)
                staged.write(data)
                staged.flush()
                os.fsync(staged.fileno())
                if exists:
                    os.replace(staged.name, path)
                else:
                    os.link(staged.name, path)
            finally:
                Path(staged.name).unlink(missing_ok=True)
    production.NativeHost.sync_directory(path.parent)


def publish(host, container, root, managed, *, root_bytes, route_name, route_bytes,
            container_root=ROOT_PATH, container_managed=MANAGED_PATH,
            expected_before=None, expected_adapted_sha256=None):
    """Validate the complete admitted config, update the two binds, and reload it."""
    if (route_name != ROUTE_NAME or not isinstance(root_bytes, bytes) or not root_bytes
            or len(root_bytes) > MAX_CONFIG_BYTES):
        raise production.Refused("Invalid Axxium Caddy publication input")
    if route_bytes is not None and (
            not isinstance(route_bytes, bytes) or not route_bytes or len(route_bytes) > MAX_CONFIG_BYTES):
        raise production.Refused("Invalid Axxium Caddy publication input")
    if expected_adapted_sha256 is not None and (
            not isinstance(expected_adapted_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_adapted_sha256)):
        raise production.Refused("Invalid admitted Caddy startup hash")
    started = False
    try:
        root, managed = Path(root), Path(managed)
        host.recheck_lock()
        before = _files(root, managed)
        _visible(host, container, root, managed, container_root, container_managed, before)
        routes = dict(before.routes)
        if route_bytes is None:
            routes.pop(route_name, None)
        else:
            routes[route_name] = route_bytes
        candidate = _render_validation(root_bytes, routes, container_managed, route_name)
        _exec(host, container, "caddy", "validate", "--adapter", "caddyfile", "--config", "-", data=candidate)
        adapted = _adapted(host, container, PROSPECTIVE_PATH, data=candidate)
        if expected_before is not None and readback(host, container, root, managed,
                                                   container_root=container_root,
                                                   container_managed=container_managed) != expected_before:
            raise production.Refused("Caddy incumbent differs from the admitted observation")
        host.recheck_lock()
        if _files(root, managed) != before:
            raise production.Refused("Caddy source files changed before publication")
        # Add the managed route before extracting the inline site. During a
        # rollback restore the inline site before removing the owned route.
        # A partial write is left for independent journal reconciliation.
        started = True
        route = managed / route_name
        if route_bytes is not None and route_bytes != before.routes.get(route_name):
            _write_route(route, route_bytes, exists=route_name in before.routes)
        if root_bytes != before.root:
            _write_root(root, before, root_bytes, host)
        if route_bytes is None and route_name in before.routes:
            host.recheck_lock()
            _write_route(route, None, exists=True)
        after = _files(root, managed)
        if after != ConfigFiles(root_bytes, routes, before.root_inode, before.managed_inode):
            raise production.Uncertain("Caddy source files differ after publication")
        _visible(host, container, root, managed, container_root, container_managed, after)
        startup = _adapted(host, container, container_root)
        source_paths = {container_root}
        if route_name in routes:
            source_paths.add(container_managed + "/" + route_name)
        if (_without_adapter_hide_paths(startup, source_paths)
                != _without_adapter_hide_paths(adapted, {PROSPECTIVE_PATH})):
            raise production.Uncertain("Caddy startup config differs from the validated candidate")
        startup_hash = _config_hash(startup)
        if expected_adapted_sha256 is not None and startup_hash != expected_adapted_sha256:
            raise production.Uncertain("Caddy startup config differs from the admitted hash")
        if expected_before is not None and _active(host, container) != expected_before["active_sha256"]:
            raise production.Uncertain("Caddy active config changed before reload")
        host.recheck_lock()
        _exec(host, container, "caddy", "reload", "--adapter", "caddyfile", "--config", container_root, effect=True)
        active = _active(host, container)
        if active != startup_hash:
            raise production.Uncertain("Caddy active config differs from the verified startup config")
        return after.receipt(startup_hash, active)
    except BaseException as error:
        if started:
            host.uncertain = True
        if isinstance(error, (KeyError, OSError, TypeError, ValueError)):
            failure = production.Uncertain if started else production.Refused
            raise failure("Caddy configuration publication unavailable") from None
        raise
