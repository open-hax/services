#!/usr/bin/env python3
"""Admit one fixed Cloud Cuckoo release before host credentials or SSH.

The source/image/stage tuple remains deliberately unresolved until an actual
warning-free accepted candidate has completed the ordinary qualification path.
No command-line source override or staging-only receipt can unlock production.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from axxium_production_qualification import main_ancestor, native_file, require, strict_json
from colab_stage_admission import LICENSE, REPOSITORY, SERVICES, verify_publisher
from environment_policy import production_admission, timestamp
from github_environment_admission import api

PLAN = 'digitalocean/services/colab-mcp/production-plan.json'
WORKFLOW = '.github/workflows/deploy-stack.yml'
SHA = r'[0-9a-f]{40}'
DIGEST = r'sha256:[0-9a-f]{64}'
# These are changed only by a reviewed source pin after real publication/stage.
CANDIDATE = None
STAGE = None
PREDECESSOR = {
    'container_id': '2d9bb92a96525b746ef21c914b8165e2e76b8fe1ee39a95c3362d2e6643ac687',
    'image_id': 'sha256:54afa62b1a2ec5fc10b299ded1ba830dc70f774a8bffe36f1c70af9c50104e72',
    'image_reference': 'knoxx-colab-mcp:candidate',
    'environment_sha256': '3643163da21c683cb9a05264c722020a688921870d1a20b5fd9699d1b25e5d47',
    'compose_sha256': 'f721c5735b35d1b70cf8499d733f947e797fd5303099aa03390e2fa10ae24d20',
    'environment_file_exists': False,
}


def validate_plan(plan):
    require(isinstance(plan, dict) and set(plan) == {'schema', 'host', 'candidate', 'stage', 'predecessor'}
            and plan['schema'] == 'colab-qualified-production/v1'
            and plan['host'] == 'knoxx.promethean.rest' and plan['predecessor'] == PREDECESSOR,
            'unreviewed fixed Colab production plan')
    require(CANDIDATE is not None and STAGE is not None, 'qualified Cloud Cuckoo candidate is not pinned')
    require(plan['candidate'] == CANDIDATE and plan['stage'] == STAGE,
            'production candidate differs from the reviewed fixed tuple')
    candidate, stage = plan['candidate'], plan['stage']
    require(isinstance(candidate, dict) and set(candidate) == {
        'source_sha', 'source_pr', 'publisher_run_id', 'publisher_attempt', 'publisher_controller_sha',
        'artifact_id', 'artifact_digest', 'image_reference', 'image_id', 'runtime_image_id', 'license_sha256', 'tests'},
        'malformed fixed publication tuple')
    for key in ('source_sha', 'publisher_controller_sha'):
        require(isinstance(candidate[key], str) and re.fullmatch(SHA, candidate[key]), 'immutable source required')
    for key in ('source_pr', 'publisher_run_id', 'publisher_attempt', 'artifact_id', 'tests'):
        require(type(candidate[key]) is int and candidate[key] > 0, 'native publication identity required')
    require(isinstance(candidate['image_reference'], str) and re.fullmatch(
        'ghcr.io/open-hax/cloud-cuckoo@' + DIGEST, candidate['image_reference']), 'immutable public image required')
    require(candidate['runtime_image_id'] == candidate['image_reference'].split('@')[1]
            and candidate['license_sha256'] == LICENSE, 'runtime manifest or full GPL license differs')
    for key in ('image_id', 'artifact_digest'):
        require(isinstance(candidate[key], str) and re.fullmatch(DIGEST, candidate[key]), 'native digest required')
    require(isinstance(stage, dict) and set(stage) == {
        'controller_sha', 'run_id', 'attempt', 'artifact_id', 'artifact_digest', 'compose_sha256', 'plan_sha256'},
        'malformed fixed Stage tuple')
    require(isinstance(stage['controller_sha'], str) and re.fullmatch(SHA, stage['controller_sha']),
            'immutable Stage controller required')
    for key in ('run_id', 'attempt', 'artifact_id'):
        require(type(stage[key]) is int and stage[key] > 0, 'native Stage identity required')
    for key in ('artifact_digest', 'compose_sha256', 'plan_sha256'):
        require(isinstance(stage[key], str) and re.fullmatch(DIGEST if key == 'artifact_digest' else r'[0-9a-f]{64}', stage[key]),
                'native Stage digest required')
    return plan


def verify_stage(plan, raw, now):
    candidate, selected = plan['candidate'], plan['stage']
    main_ancestor(SERVICES, selected['controller_sha'])
    run = api(f'repos/{SERVICES}/actions/runs/{selected["run_id"]}')
    require(run.get('id') == selected['run_id'] and run.get('run_attempt') == selected['attempt']
            and run.get('path') == WORKFLOW and run.get('head_sha') == selected['controller_sha']
            and run.get('head_branch') == 'main' and run.get('event') == 'workflow_dispatch'
            and run.get('repository', {}).get('full_name') == SERVICES
            and run.get('status') == 'completed' and run.get('conclusion') == 'success',
            'native Cloud Cuckoo Stage run mismatch')
    artifact = api(f'repos/{SERVICES}/actions/artifacts/{selected["artifact_id"]}')
    require(artifact.get('id') == selected['artifact_id'] and artifact.get('expired') is False
            and artifact.get('name') == f'colab-transport-stage-{selected["run_id"]}-{selected["attempt"]}'
            and artifact.get('workflow_run', {}).get('id') == run['id']
            and artifact.get('workflow_run', {}).get('head_sha') == selected['controller_sha']
            and artifact.get('digest') == selected['artifact_digest']
            and type(artifact.get('size_in_bytes')) is int and artifact['size_in_bytes'] == len(raw)
            and 0 < len(raw) < 2 * 1024 * 1024
            and 'sha256:' + hashlib.sha256(raw).hexdigest() == selected['artifact_digest']
            and timestamp(artifact['expires_at']) > now, 'native Stage artifact custody unavailable')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1 and entries[0].filename == 'stage-receipt.json'
                and not entries[0].is_dir() and entries[0].file_size < 65536, 'unexpected Stage artifact members')
        proof = strict_json(archive.read(entries[0]))
    require(isinstance(proof, dict) and proof.get('schema') == 'colab-transport-stage-receipt/v1'
            and proof.get('health') == 'success' and proof.get('phase') == 'staging'
            and proof.get('source_repository') == REPOSITORY and proof.get('source_sha') == candidate['source_sha']
            and proof.get('image_reference') == candidate['image_reference'] and proof.get('image_id') == candidate['image_id']
            and proof.get('runtime_image_id') == candidate['runtime_image_id']
            and proof.get('manifest_config_binding_verified') is True
            and proof.get('services_sha') == selected['controller_sha']
            and proof.get('compose_sha256') == selected['compose_sha256']
            and proof.get('plan_sha256') == selected['plan_sha256']
            and proof.get('compose_project') == 'colab-mcp-transport-staging'
            and all(proof.get(key) is True for key in ('synthetic_actor', 'synthetic_upstreams',
                'oauth_flow_verified', 'gateway_forwarding_verified', 'session_revoked', 'unrelated_runtime_preserved'))
            and all(proof.get(key) is False for key in ('human_login_verified', 'google_execution_verified', 'production_qualified'))
            and type(proof.get('assertions')) is int and proof['assertions'] > 0
            and timestamp(proof['completed_at']) <= now, 'Stage does not prove this exact candidate and image')
    return proof


def admit(plan_bytes, controller_sha, publisher_bytes, stage_bytes, *, now=None):
    plan = validate_plan(strict_json(plan_bytes))
    require(isinstance(controller_sha, str) and re.fullmatch(SHA, controller_sha), 'immutable Services controller required')
    main_ancestor(SERVICES, controller_sha)
    require(native_file(SERVICES, PLAN, controller_sha) == plan_bytes, 'fixed production plan bytes changed')
    candidate = plan['candidate']
    fixed_time = now
    now = now or datetime.now(timezone.utc)
    published = verify_publisher(candidate, publisher_bytes)
    require(timestamp(published['expires_at']) > now, 'publisher artifact expired')
    verify_stage(plan, stage_bytes, now)
    path = f'digitalocean/qualifications/colab-mcp/{candidate["source_sha"]}.json'
    proof_raw = native_file(SERVICES, path, controller_sha)
    proof = strict_json(proof_raw)
    require(isinstance(proof, dict) and proof.get('repository') == REPOSITORY
            and proof.get('source_sha') == candidate['source_sha'], 'immutable qualification source differs')
    decision = production_admission(candidate['source_sha'], proof.get('integration'), proof.get('e2e'), proof.get('mutation_batches'))
    require(decision.get('admitted') is True, decision.get('reason', 'ordinary production qualification refused'))
    # Recheck native custody after canonical policy admission and before SSH.
    main_ancestor(SERVICES, controller_sha)
    now = fixed_time or datetime.now(timezone.utc)
    published = verify_publisher(candidate, publisher_bytes)
    require(timestamp(published['expires_at']) > now, 'publisher artifact expired')
    verify_stage(plan, stage_bytes, now)
    return {'admitted': True, 'phase': 'production', 'source_sha': candidate['source_sha'],
            'image_reference': candidate['image_reference'], 'image_id': candidate['image_id'],
            'runtime_image_id': candidate['runtime_image_id'], 'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
            'qualification_sha256': hashlib.sha256(proof_raw).hexdigest(),
            'evaluated_mutations': decision['evaluated_mutations'], 'human_login_verified': False,
            'google_execution_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--controller-sha', required=True)
    parser.add_argument('--publisher-artifact', required=True)
    parser.add_argument('--stage-artifact', required=True)
    args = parser.parse_args()
    try:
        plan = Path(args.plan).read_bytes()
        require(len(plan) <= 65536, 'production plan exceeds bound')
        publisher, stage = Path(args.publisher_artifact), Path(args.stage_artifact)
        require(publisher.stat().st_size < 2 * 1024 * 1024 and stage.stat().st_size < 2 * 1024 * 1024,
                'native artifact exceeds bound')
        result = admit(plan, args.controller_sha, publisher.read_bytes(), stage.read_bytes())
    except Exception:
        print(json.dumps({'admitted': False, 'reason': 'fixed Colab production evidence unavailable or refused'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
