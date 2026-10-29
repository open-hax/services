#!/usr/bin/env python3
"""Admit one merged Cloud Cuckoo image before the locked staging host job.

The source-owned publisher's native artifact is data, never executable input.
This is staging admission, not production qualification or a Google grant.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from axxium_production_qualification import main_ancestor, native_file, require, strict_json
from environment_policy import staging_admission
from github_environment_admission import api

REPOSITORY = 'open-hax/cloud-cuckoo'
SERVICES = 'open-hax/services'
PLAN = 'digitalocean/services/colab-mcp/transport-stage-plan.json'
WORKFLOW = '.github/workflows/publish-image.yml'
SOURCE = '19e1adadb1b24eacca993de92f6370b400cf3d33'
LICENSE = '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986'
SHA = r'[0-9a-f]{40}'
DIGEST = r'sha256:[0-9a-f]{64}'


def validate_plan(plan):
    keys = {'schema', 'source_sha', 'source_pr', 'publisher_run_id', 'publisher_attempt',
            'publisher_controller_sha', 'artifact_id', 'artifact_digest', 'image_reference',
            'image_id', 'runtime_image_id', 'license_sha256', 'tests'}
    require(isinstance(plan, dict) and set(plan) == keys and plan['schema'] == 'colab-transport-stage/v1',
            'unreviewed transport-stage plan')
    require(plan['source_sha'] == SOURCE and plan['source_pr'] == 13 and plan['license_sha256'] == LICENSE,
            'unexpected transport-stage source or license')
    require(isinstance(plan['publisher_controller_sha'], str) and re.fullmatch(SHA, plan['publisher_controller_sha']),
            'immutable publisher required')
    for key in ('publisher_run_id', 'publisher_attempt', 'artifact_id', 'tests'):
        require(type(plan[key]) is int and plan[key] > 0, 'positive native plan identity required')
    require(isinstance(plan['image_reference'], str) and re.fullmatch(
        'ghcr.io/open-hax/cloud-cuckoo@' + DIGEST, plan['image_reference']), 'immutable Cloud Cuckoo image required')
    for key in ('image_id', 'artifact_digest'):
        require(isinstance(plan[key], str) and re.fullmatch(DIGEST, plan[key]), 'native digest required')
    require(plan['runtime_image_id'] == plan['image_reference'].split('@')[1],
            'fixed containerd runtime identity must match the immutable registry manifest')
    return plan


def artifact_receipt(raw, artifact, plan):
    require(isinstance(raw, bytes) and len(raw) < 2 * 1024 * 1024 and
            'sha256:' + hashlib.sha256(raw).hexdigest() == plan['artifact_digest'] == artifact.get('digest'),
            'publisher artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(len(members) == 1 and members[0].filename == 'release-receipt.json' and
                members[0].file_size < 65536 and not members[0].is_dir(), 'unexpected release artifact members')
        receipt = strict_json(archive.read(members[0]))
    require(isinstance(receipt, dict) and receipt.get('schema') == 'cloud-cuckoo-image/v1' and
            receipt.get('repository') == REPOSITORY and receipt.get('source_sha') == plan['source_sha'] and
            receipt.get('source_pr') == plan['source_pr'] and receipt.get('workflow_sha') == plan['publisher_controller_sha'] and
            receipt.get('run_id') == plan['publisher_run_id'] and receipt.get('run_attempt') == plan['publisher_attempt'] and
            receipt.get('image_reference') == plan['image_reference'] and receipt.get('image_id') == plan['image_id'] and
            receipt.get('image_digest') == plan['image_reference'].split('@')[1] and
            receipt.get('image_provenance_verified') is True and receipt.get('metadata_verified') is True and
            receipt.get('license_verified') is True and receipt.get('license_sha256') == LICENSE and
            receipt.get('license_path') == '/usr/share/licenses/cloud-cuckoo/LICENSE' and
            receipt.get('pending_smoke') == 'passed' and receipt.get('package_visibility') == 'public' and
            receipt.get('source_repository_visibility') == 'public' and receipt.get('publication_target') == 'public' and
            receipt.get('anonymous_digest_pull_verified') is True and receipt.get('public_release_complete') is True and
            receipt.get('tests') == {'tests': plan['tests'], 'failures': 0, 'errors': 0, 'skipped': 0},
            'published source/image/license evidence mismatch')
    return receipt


def admit(plan_bytes, controller_sha, artifact_bytes):
    plan = validate_plan(strict_json(plan_bytes))
    require(re.fullmatch(SHA, controller_sha or ''), 'immutable Services controller required')
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN, controller_sha) == plan_bytes, 'controller plan bytes changed')
    main_ancestor(REPOSITORY, plan['source_sha'])
    main_ancestor(REPOSITORY, plan['publisher_controller_sha'])
    source_main = api(f'repos/{REPOSITORY}/commits/main')
    require(source_main.get('sha') == plan['source_sha'], 'staging requires the current accepted source main')
    pr = api(f'repos/{REPOSITORY}/pulls/{plan["source_pr"]}')
    decision = staging_admission({'merged': pr.get('merged'), 'base': pr.get('base', {}).get('ref'),
                                  'merge_sha': pr.get('merge_commit_sha')})
    require(decision.get('admitted') is True and decision.get('source_sha') == plan['source_sha'] and
            pr.get('number') == plan['source_pr'] and pr.get('state') == 'closed' and
            pr.get('base', {}).get('repo', {}).get('full_name') == REPOSITORY,
            'native accepted source PR mismatch')
    run = api(f'repos/{REPOSITORY}/actions/runs/{plan["publisher_run_id"]}')
    require(run.get('id') == plan['publisher_run_id'] and run.get('run_attempt') == plan['publisher_attempt'] and
            run.get('event') == 'workflow_dispatch' and run.get('head_branch') == 'main' and
            run.get('head_sha') == plan['publisher_controller_sha'] and run.get('path') == WORKFLOW and
            run.get('status') == 'completed' and run.get('conclusion') == 'success' and
            run.get('repository', {}).get('full_name') == REPOSITORY, 'native publisher run mismatch')
    artifact = api(f'repos/{REPOSITORY}/actions/artifacts/{plan["artifact_id"]}')
    require(artifact.get('id') == plan['artifact_id'] and artifact.get('expired') is False and
            artifact.get('name') == f'cloud-cuckoo-release-{plan["publisher_run_id"]}-{plan["publisher_attempt"]}' and
            artifact.get('workflow_run', {}).get('id') == plan['publisher_run_id'] and
            artifact.get('workflow_run', {}).get('head_sha') == plan['publisher_controller_sha'] and
            artifact.get('digest') == plan['artifact_digest'] and
            type(artifact.get('size_in_bytes')) is int and artifact['size_in_bytes'] == len(artifact_bytes),
            'native publisher artifact identity mismatch')
    artifact_receipt(artifact_bytes, artifact, plan)
    return {'admitted': True, 'phase': 'staging', 'source_sha': plan['source_sha'],
            'image_reference': plan['image_reference'], 'image_id': plan['image_id'],
            'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(), 'synthetic_upstreams': True,
            'production_qualified': False, 'google_execution_verified': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True)
    parser.add_argument('--controller-sha', required=True)
    parser.add_argument('--artifact', required=True)
    args = parser.parse_args()
    try:
        result = admit(Path(args.plan).read_bytes(), args.controller_sha, Path(args.artifact).read_bytes())
    except Exception:
        print(json.dumps({'admitted': False, 'reason': 'native transport-stage evidence unavailable or mismatched'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
