#!/usr/bin/env python3
"""Apply the exact Events plan supplied by the protected production caller."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from axxium_events_admission import validate_plan_bytes
from axxium_events_census import PinnedCensus
from axxium_events_release import EventsHost, execute_admitted
from axxium_minimal_release import private_read
from axxium_production_qualification import require


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--census-module", type=Path, required=True)
    parser.add_argument("--census-driver", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        raw = private_read(args.plan, maximum=65536)
        require(sha256(raw).hexdigest() == args.plan_sha256, "Reviewed Events plan digest differs")
        plan = validate_plan_bytes(raw)
        pins = plan["census"]
        census = PinnedCensus(
            args.census_module, args.census_driver,
            module_sha256=pins["module_sha256"], driver_sha256=pins["driver_sha256"],
        )
        host = EventsHost(plan, args.plan_sha256, census)
        result = execute_admitted(plan, host)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "healthy" else 3
    except Exception:
        print("Axxium Events activation refused. Preserve any native operation hold and journal.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
