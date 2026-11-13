"""Private, unwired journal for the Axxium ingress adoption transaction.

The caller owns production admission, the host queue and independent readback.
Observations must bind the exact admitted topology, dependency identities and
active/startup route exposure. This module executes no runtime effects.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
import re
import stat
import tempfile

import axxium_minimal_release as production
from axxium_interrupted_handoff import _rename_noreplace


SCHEMA = "axxium-ingress-production-pending/v1"
MAX_EVENTS = 64
PHASES = {
    "fence_shared": ({"shared_live"}, "shared_fenced"),
    "select_dedicated": ({"shared_fenced"}, "dedicated_fenced"),
    "publish_dedicated": ({"dedicated_fenced"}, "dedicated_live"),
    "fence_dedicated": ({"dedicated_live"}, "dedicated_fenced"),
    "restore_shared": ({"shared_fenced", "dedicated_fenced"}, "restored_fenced"),
    "publish_restored": ({"restored_fenced"}, "restored_live"),
}
STATES = {target for _, target in PHASES.values()} | {"shared_live", "unknown"}
TERMINAL = {"dedicated_live", "restored_live"}


class Held(production.Refused):
    """Operation needs independent reconciliation before another effect."""


def sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class Observation:
    state: str
    state_sha256: str
    evidence_sha256: str

    def __post_init__(self):
        if self.state not in STATES or not sha256(self.state_sha256) or not sha256(self.evidence_sha256):
            raise Held("Invalid ingress observation")

    def agrees(self, other):
        return self.state == other.state and self.state_sha256 == other.state_sha256


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def publish_private(path, data, *, replace=False):
    """Publish one private file; phase records and the hold are create-only."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ingress-draft-", delete=False) as draft:
        try:
            draft.write(data)
            draft.flush()
            os.fsync(draft.fileno())
            if replace:
                os.replace(draft.name, path)
            else:
                _rename_noreplace(draft.name, path)
            production.NativeHost.sync_directory(path.parent)
        finally:
            if os.path.exists(draft.name):
                os.unlink(draft.name)


def private_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Held("Private ingress journal directory differs")


class Journal:
    def __init__(self, host, directory):
        self.host = host
        self.directory = directory
        self.current = None
        self.pending = None
        self.terminal = False
        self.count = 0
        self.previous = None
        self.verified = set()

    @property
    def exposure(self):
        if self.current is None or self.pending is not None:
            return "unknown"
        return "fenced" if self.current.state.endswith("_fenced") else "live"

    def _anchor(self, *, replace):
        publish_private(self.directory / "head.json", json_bytes({"count": self.count, "sha256": self.previous}),
                        replace=replace)

    def _project(self, action, phase, observation):
        if self.terminal:
            raise Held("Terminal journal cannot accept more phases")
        if action == "opened" and self.current is None and phase is None and observation.state == "shared_live":
            self.current = observation
        elif action == "intent" and self.pending is None and phase in PHASES and phase not in self.verified:
            if self.current is None or not self.current.agrees(observation) or observation.state not in PHASES[phase][0]:
                raise Held("Phase source has not been independently verified")
            self.pending = phase
        elif action == "verified" and phase is not None and phase == self.pending and observation.state == PHASES[phase][1]:
            self.current = observation
            self.verified.add(phase)
            self.pending = None
        elif action == "not_applied" and phase is not None and phase == self.pending and self.current.agrees(observation):
            self.pending = None
        elif (action == "terminal" and phase is None and self.pending is None and self.current is not None
              and self.current.agrees(observation) and observation.state in TERMINAL):
            self.terminal = True
        else:
            raise Held("Ingress journal transition refused")

    def _append(self, action, phase, observation):
        self.host.recheck_lock()
        if self.count >= MAX_EVENTS:
            raise Held("Ingress journal limit reached")
        data = json_bytes({"action": action, "phase": phase, "observation": asdict(observation),
                           "sequence": self.count, "previous": self.previous})
        try:
            self._project(action, phase, observation)
            publish_private(self.directory / f"phase-{self.count:03d}.json", data)
            self.previous = production.digest(data)
            self.count += 1
            self._anchor(replace=self.count > 1)
        except BaseException:
            self.host.uncertain = True
            raise

    def load(self):
        names = sorted(path.name for path in self.directory.iterdir() if path.name.startswith("phase-"))
        if not names or len(names) > MAX_EVENTS:
            raise Held("Ingress journal record count differs")
        head = production.private_read(self.directory / "head.json", maximum=256)
        digests = []
        for sequence, name in enumerate(names):
            if name != f"phase-{sequence:03d}.json":
                raise Held("Ingress journal sequence differs")
            raw = production.private_read(self.directory / name, maximum=4096)
            try:
                event = json.loads(raw)
                if (set(event) != {"action", "phase", "observation", "sequence", "previous"}
                        or event["sequence"] != sequence or event["previous"] != self.previous
                        or json_bytes(event) != raw):
                    raise Held("Ingress journal chain differs")
                self._project(event["action"], event["phase"], Observation(**event["observation"]))
            except (TypeError, ValueError) as error:
                raise Held("Malformed ingress journal record") from error
            self.previous = production.digest(raw)
            digests.append(self.previous)
        self.count = len(names)
        if head == json_bytes({"count": self.count, "sha256": self.previous}):
            return
        if self.count > 1 and head == json_bytes({"count": self.count - 1, "sha256": digests[-2]}):
            # An append can finish before its anchor advances. A phase effect
            # cannot begin until begin() returns after both durable writes.
            self.host.recheck_lock()
            self._anchor(replace=True)
            return
        raise Held("Ingress journal head differs from its phase records")

    def begin(self, phase, source):
        """Durably record intent before the caller attempts the single effect."""
        if not isinstance(source, Observation) or not isinstance(phase, str):
            raise Held("Invalid ingress phase intent")
        self._append("intent", phase, source)

    def reconcile(self, observation):
        """Settle pending intent from fresh readback; unknown stays pending."""
        if not isinstance(observation, Observation):
            raise Held("Invalid ingress phase observation")
        self.host.recheck_lock()
        if self.pending is None:
            raise Held("Ingress journal has no phase to reconcile")
        if observation.state == PHASES[self.pending][1]:
            outcome = "verified"
        elif self.current.agrees(observation):
            outcome = "not_applied"
        else:
            raise Held("Phase exposure or effect remains uncertain")
        self._append(outcome, self.pending, observation)
        return outcome

    def finish(self, observation):
        """Release this hold only after terminal evidence and fresh readback."""
        if not isinstance(observation, Observation):
            raise Held("Invalid terminal ingress observation")
        self.host.recheck_lock()
        if not self.terminal:
            self._append("terminal", None, observation)
        if not self.current.agrees(observation):
            raise Held("Terminal ingress observation changed")
        marker = production.PENDING
        saved = self.host.pending_bytes
        self.host.recheck_lock()
        try:
            marker.unlink()
            production.NativeHost.sync_directory(marker.parent)
        except OSError as error:
            self.host.uncertain = True
            try:
                if not os.path.lexists(marker):
                    publish_private(marker, saved)
            except (OSError, RuntimeError):
                pass
            raise Held("Ingress hold release is uncertain") from error
        self.host.pending_bytes = None
        self.host.uncertain = True  # No further effects may use this journal instance.


@contextmanager
def open_journal(operation_id, plan_sha256, *, observe_initial=None):
    """Claim or reopen an exact operation under the existing native lock.

    The caller independently admits the plan and retains private rollback
    inputs. The optional read-only callback observes the incumbent while the
    native lock is held. Without it, this reopens an exact existing hold.
    """
    if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id) or not sha256(plan_sha256):
        raise Held("Invalid ingress operation identity")
    host = production.NativeHost()
    root = production.STATE / "ingress-operations"
    directory = root / operation_id
    marker = production.PENDING
    expected = json_bytes({"schema": SCHEMA, "operation": str(directory), "plan_sha256": plan_sha256})
    with host.lock():
        journal = Journal(host, directory)
        if observe_initial is not None:
            if os.path.lexists(marker):
                raise Held("A fresh ingress operation requires no prior hold")
            initial = observe_initial()
            if not isinstance(initial, Observation) or initial.state != "shared_live":
                raise Held("A fresh ingress operation requires a verified incumbent and no prior hold")
            root.mkdir(mode=0o700, exist_ok=True)
            private_directory(root)
            production.NativeHost.sync_directory(root.parent)
            directory.mkdir(mode=0o700)
            production.NativeHost.sync_directory(root)
            journal._append("opened", None, initial)
            publish_private(marker, expected)
            host.pending_bytes = expected
        else:
            if production.private_read(marker, maximum=4096) != expected:
                raise Held("Production hold is not this exact ingress operation")
            host.pending_bytes = expected
            private_directory(root)
            private_directory(directory)
            journal.load()
        host.recheck_lock()
        yield journal
