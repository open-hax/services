#!/usr/bin/env python3
"""Reuse protected native custody and exact-source qualification for the upgrade."""
from axxium_active_mailbox_release import PLAN_PATH, SCHEMA, validate_plan
from axxium_production_qualification import strict_json
from axxium_unified_mailbox_admission import admit, main


def admit_active_mailbox(plan_bytes, controller_sha, archive, *, now=None):
    validate_plan(strict_json(plan_bytes))
    return admit(plan_bytes, controller_sha, archive, now=now, plan_path=PLAN_PATH,
                 plan_schema=SCHEMA, qualification_scope="active-mailbox-upgrade",
                 qualification_suffix="-active-upgrade")


if __name__ == "__main__":
    raise SystemExit(main(admit_active_mailbox))
