#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Admit one new merged public image for the closed healthy19e Stage upgrade.

The previous Stage receipt is never reused as new-candidate execution evidence.
This is source/image custody before SSH, not normal production qualification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from axxium_production_qualification import main_ancestor, native_file, require, strict_json
from colab_stage_admission import SERVICES, verify_publisher
from colab_transport_stage_upgrade import PLAN_PATH, validate_plan
from environment_policy import timestamp


def admit(plan_bytes, controller_sha, artifact_bytes, *, now=None):
    plan = validate_plan(strict_json(plan_bytes))
    require(isinstance(controller_sha, str) and re.fullmatch(r'[0-9a-f]{40}', controller_sha),
            'immutable Services controller required')
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN_PATH, controller_sha) == plan_bytes, 'Stage upgrade plan bytes changed')
    artifact = verify_publisher(plan['candidate'], artifact_bytes, current_main=True)
    now = now or datetime.now(timezone.utc)
    require(timestamp(artifact['expires_at']) > now, 'native publisher artifact expired')
    return {'admitted': True, 'phase': 'staging', 'source_sha': plan['candidate']['source_sha'],
            'image_reference': plan['candidate']['image_reference'], 'image_id': plan['candidate']['image_id'],
            'runtime_image_id': plan['candidate']['runtime_image_id'],
            'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(), 'synthetic_upstreams': True,
            'production_qualified': False, 'google_execution_verified': False, 'human_login_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--controller-sha', required=True)
    parser.add_argument('--artifact', required=True)
    args = parser.parse_args()
    try:
        path, artifact = Path(args.plan), Path(args.artifact)
        require(path.stat().st_size <= 65536 and artifact.stat().st_size < 2 * 1024 * 1024,
                'Stage upgrade evidence exceeds bounded size')
        result = admit(path.read_bytes(), args.controller_sha, artifact.read_bytes())
    except Exception:
        print(json.dumps({'admitted': False, 'reason': 'fixed Stage upgrade source evidence unavailable or refused'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
