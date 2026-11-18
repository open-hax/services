#!/usr/bin/env python3
"""Check the same-image production mailbox Events profile before admission."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from axxium_minimal_release import Refused, private_read
from axxium_production_qualification import Refusal, require, strict_json

STREAM = "AXXIUM_MAILBOX_STREAM_ENABLED"
SUBSCRIPTIONS = "AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED"
COEXISTENCE = "AXXIUM_MAILBOX_TRANSPORT_COEXISTENCE"
RECIPIENT = "AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID"
SEALING_FILE = "AXXIUM_MAILBOX_WEBHOOK_KEY_FILE"

CALLBACK_COUNTS = frozenset({
    "retained-subscriptions",
    "active-subscriptions",
    "eligible-pending",
    "retryable-callbacks",
    "unsettled-attempts",
    "due-callbacks",
})


def validate_projection(incumbent, candidate, *, image_id, recipient_did):
    """Allow only the two transport settings to change in resolved Compose."""
    require(isinstance(incumbent, dict), "Incumbent projection must be an object")
    services = incumbent.get("services")
    require(isinstance(services, dict), "Incumbent services are missing")
    app = services.get("axxium")
    require(isinstance(app, dict), "Incumbent Axxium service is missing")
    require(isinstance(image_id, str) and image_id and app.get("image") == image_id,
            "Incumbent image differs from the reviewed pin")

    environment = app.get("environment")
    require(isinstance(environment, dict), "Incumbent environment is missing")
    require(environment.get(STREAM) == "true" and environment.get(SUBSCRIPTIONS) == "false"
            and COEXISTENCE not in environment, "Incumbent transport settings differ")
    require(isinstance(recipient_did, str) and recipient_did
            and environment.get(RECIPIENT) == recipient_did, "Selected recipient differs")
    sealing_file = environment.get(SEALING_FILE)
    require(isinstance(sealing_file, str) and sealing_file.strip(), "Webhook sealing path is missing")

    expected = deepcopy(incumbent)
    expected_environment = expected["services"]["axxium"]["environment"]
    expected_environment.update({SUBSCRIPTIONS: "true", COEXISTENCE: "true"})
    require(json.dumps(expected, sort_keys=True, allow_nan=False)
            == json.dumps(candidate, sort_keys=True, allow_nan=False),
            "Candidate changes more than the two Events settings")


def validate_census(counts):
    """Require zero subscription and delivery counts for a first enrollment."""
    require(isinstance(counts, dict) and set(counts) == CALLBACK_COUNTS,
            "Callback census is incomplete")
    require(all(type(count) is int and count == 0 for count in counts.values()),
            "Callback census is nonzero or malformed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incumbent", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--recipient-did", required=True)
    args = parser.parse_args(argv)

    try:
        incumbent = strict_json(private_read(args.incumbent))
        candidate = strict_json(private_read(args.candidate))
        validate_projection(incumbent, candidate, image_id=args.image_id, recipient_did=args.recipient_did)
        census = strict_json(private_read(args.census))
        require(isinstance(census, dict) and census.get("mailbox_did") == args.recipient_did
                and census.get("read_only") is True
                and census.get("private_bounded_stable_mount_verified") is True,
                "Callback census does not match the selected mailbox")
        validate_census(census.get("counts"))
    except Refusal as error:
        print(f"Events profile refused. {error}", file=sys.stderr)
        return 2
    except (OSError, Refused, ValueError):
        print("Events profile refused. A protected input could not be read.", file=sys.stderr)
        return 2

    print("Events profile projection and callback counts verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
