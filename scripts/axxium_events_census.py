"""Run a pinned mailbox callback projection on a private host-local snapshot."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

from axxium_events_profile import CALLBACK_COUNTS
from axxium_minimal_release import Refused, private_read
from axxium_production_qualification import strict_json

MAX_LEDGER_BYTES = 2 * 1024 * 1024
MAX_PROJECTOR_BYTES = 4 * 1024 * 1024
MAX_DRIVER_BYTES = 4096
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 1024
PROJECTION_TIMEOUT = 15
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
MODULE_IMPORT = b"import { summarize } from './probe-dist/probe.js';"
MEMORY_IMPORT = (
    b"const { summarize } = await import('data:text/javascript;base64,' + "
    b"fs.readFileSync(Number(process.env.AXXIUM_PROBE_FD)).toString('base64'));"
)


def pinned_file(path, digest, maximum):
    """Read one owner-private artifact and verify its exact reviewed bytes."""
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refused("Callback projector digest is malformed")
    try:
        raw = private_read(path, maximum=maximum)
    except (OSError, Refused, TypeError, ValueError):
        raise Refused("Callback projector artifact custody differs") from None
    if not raw or len(raw) > maximum or hashlib.sha256(raw).hexdigest() != digest:
        raise Refused("Callback projector artifact digest differs")
    return raw


def callback_counts(raw):
    """Decode only the projector's bounded aggregate result."""
    if not raw or len(raw) > MAX_RESULT_BYTES:
        raise Refused("Callback projection result exceeds its bounds")
    try:
        counts = strict_json(raw)
    except ValueError:
        raise Refused("Callback projection produced invalid JSON") from None
    if (not isinstance(counts, dict)
            or set(counts) != CALLBACK_COUNTS
            or any(type(count) is not int or count < 0 for count in counts.values())
            or not 0 <= counts["active-subscriptions"] <= counts["retained-subscriptions"] <= 1):
        raise Refused("Callback projection produced invalid counts")
    return counts


class PinnedCensus:
    """Evaluate trusted projector bytes while keeping the ledger off argv and disk."""

    def __init__(self, module_file, driver_file, *, module_sha256, driver_sha256):
        self.module = pinned_file(module_file, module_sha256, MAX_PROJECTOR_BYTES)
        driver = pinned_file(driver_file, driver_sha256, MAX_DRIVER_BYTES)
        if driver.count(MODULE_IMPORT) != 1:
            raise Refused("Callback projector driver import differs")
        try:
            self.driver = driver.replace(MODULE_IMPORT, MEMORY_IMPORT).decode("ascii")
        except UnicodeError:
            raise Refused("Callback projector driver encoding differs") from None

    def __call__(self, ledger_bytes, recipient_did):
        if not isinstance(ledger_bytes, bytes) or len(ledger_bytes) > MAX_LEDGER_BYTES:
            raise Refused("Callback ledger input exceeds its bounds")
        if not isinstance(recipient_did, str) or not 0 < len(recipient_did) <= 2048:
            raise Refused("Callback recipient input exceeds its bounds")
        try:
            request = json.dumps(
                {"text": ledger_bytes.decode("utf-8"), "owner": recipient_did,
                 "now": time.time_ns() // 1_000_000},
                ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
        except UnicodeError:
            raise Refused("Callback projection input must be valid UTF-8") from None
        if len(request) > MAX_REQUEST_BYTES:
            raise Refused("Callback projection request exceeds its bounds")

        node = shutil.which("node", path=SYSTEM_PATH)
        if node is None:
            raise Refused("Callback projector Node runtime is unavailable")
        try:
            fd = os.memfd_create("axxium-callback-projection", os.MFD_CLOEXEC)
            try:
                with os.fdopen(os.dup(fd), "wb") as module:
                    module.write(self.module)
                os.lseek(fd, 0, os.SEEK_SET)
                result = subprocess.run(
                    [node, "--input-type=module", "--eval", self.driver],
                    input=request, pass_fds=(fd,), env={"AXXIUM_PROBE_FD": str(fd)},
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=PROJECTION_TIMEOUT, check=False,
                )
            finally:
                os.close(fd)
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("Callback projection could not complete") from None
        if result.returncode != 0:
            raise Refused("Callback projection refused the private ledger")
        return callback_counts(result.stdout)
