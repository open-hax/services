"""Verify the exact freshly compiled census and emit its native build receipt."""

from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from axxium_events_census import PinnedCensus, Refused
from axxium_events_profile import validate_census

directory = Path(sys.argv[1])
module = directory / "probe.js"
driver = directory / "probe.mjs"
pins = {
    "module_sha256": sha256(module.read_bytes()).hexdigest(),
    "driver_sha256": sha256(driver.read_bytes()).hexdigest(),
}
projector = PinnedCensus(module, driver, **pins)
recipient = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa"
validate_census(projector(b"", recipient))
try:
    projector(b"not a mailbox event\n", recipient)
except Refused:
    pass
else:
    raise AssertionError("Malformed ledger was accepted")
receipt = {
    "schema": "axxium-events-census-build/v1",
    "source_sha": "61b1cea021b13fac539232af23d6714f2287f8a4",
    "run_id": int(os.environ["GITHUB_RUN_ID"]),
    "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    **pins,
}
(directory / "build.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
