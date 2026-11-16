#!/usr/bin/env python3
"""Admit the immutable app-only primary AT sign-in release."""
import sys

from axxium_at_auth_release import SCHEMA, validate_plan
from axxium_production_qualification import strict_json
from axxium_unified_mailbox_admission import admit, main


def admit_at_auth(plan_bytes, controller_sha, archive):
    validate_plan(strict_json(plan_bytes))
    return admit(plan_bytes, controller_sha, archive,
                 plan_path='digitalocean/services/axxium/at-auth-production-plan.json', plan_schema=SCHEMA,
                 qualification_scope='at-auth', qualification_suffix='-at-auth')


if __name__ == '__main__':
    sys.exit(main(admit_at_auth))
