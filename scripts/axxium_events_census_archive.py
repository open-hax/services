"""Extract the exact published callback projector from a reviewed plan."""

import argparse
from hashlib import sha256
import io
from pathlib import Path
import sys
import zipfile

from axxium_events_census import MAX_DRIVER_BYTES, MAX_PROJECTOR_BYTES
from axxium_minimal_release import private_read
from axxium_production_qualification import require, strict_json

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
OUTPUTS = {"probe.js": MAX_PROJECTOR_BYTES, "probe.mjs": MAX_DRIVER_BYTES, "build.json": 4096}


def extract(archive, output, plan):
    pins = plan["census"]
    raw = private_read(archive, maximum=MAX_ARCHIVE_BYTES)
    require(len(raw) <= MAX_ARCHIVE_BYTES and sha256(raw).hexdigest() == pins["artifact_sha256"],
            "Events census archive digest differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        members = package.infolist()
        require(len(members) == len(OUTPUTS) and {member.filename for member in members} == set(OUTPUTS)
                and all(0 < member.file_size <= OUTPUTS[member.filename] for member in members),
                "Events census archive member set or bounds differ")
        files = {member.filename: package.read(member) for member in members}

    expected_receipt = {
        "schema": "axxium-events-census-build/v1",
        "source_sha": plan["source_sha"],
        "run_id": pins["build_run_id"],
        "run_attempt": pins["build_attempt"],
        "module_sha256": pins["module_sha256"],
        "driver_sha256": pins["driver_sha256"],
    }
    require(strict_json(files["build.json"]) == expected_receipt
            and sha256(files["probe.js"]).hexdigest() == pins["module_sha256"]
            and sha256(files["probe.mjs"]).hexdigest() == pins["driver_sha256"],
            "Events census build receipt or executable bytes differ")
    output.mkdir(mode=0o700)
    for name in ("probe.js", "probe.mjs"):
        (output / name).write_bytes(files[name])
        (output / name).chmod(0o600)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        extract(args.archive, args.output, strict_json(args.plan.read_bytes()))
    except Exception:
        print("Reviewed Events census archive refused.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
