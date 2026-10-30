#!/usr/bin/env python3
"""Create, inspect and remove an isolated CI canary; never start application code."""
import json
import os
import re
from pathlib import Path
import secrets
import tempfile

from colab_stage_admission import validate_plan
from colab_transport_stage import require, run, validate_tmpfs

ROOT = Path(__file__).resolve().parents[1]


def check(command=run):
    plan = validate_plan(json.loads((ROOT / 'digitalocean/services/colab-mcp/transport-stage-plan.json').read_bytes()))
    project = 'colab-transport-create-' + secrets.token_hex(12)
    with tempfile.TemporaryDirectory(prefix=project + '-') as work:
        work = Path(work)
        auth = work / 'auth'
        auth.mkdir(mode=0o700)
        private = work / 'empty-private-fixture'
        private.mkdir(mode=0o700)
        (private / 'oauth').mkdir(mode=0o700)
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(auth), 'DOCKER_CONFIG': str(auth),
               'COLAB_MCP_IMAGE': plan['image_reference']}
        original = ['docker', 'compose', '--project-name', project, '--file',
                    str(ROOT / 'digitalocean/services/colab-mcp/compose.transport-stage.yaml')]
        model = json.loads(command(original + ['config', '--format', 'json'], env=env, timeout=30))
        require(set(model.get('services', {})) == {'bridge-staging', 'compute-staging', 'gateway-staging'})
        for service in model['services'].values():
            require(service.get('image') == plan['image_reference'] and service.get('tmpfs') == ['/tmp:size=32m,mode=1777'])
            for volume in service.get('volumes', []):
                require(volume.get('type') == 'bind' and volume.get('target') in
                        {'/run/colab-transport-stage', '/var/lib/colab-transport-stage'})
                volume['source'] = str(private / 'oauth' if volume['target'] == '/var/lib/colab-transport-stage' else private)
        rendered = work / 'compose.json'
        rendered.write_text(json.dumps(model))
        compose = ['docker', 'compose', '--project-name', project, '--file', str(rendered)]
        command(['docker', 'pull', plan['image_reference']], env=env, timeout=180)
        try:
            command(compose + ['create', '--pull', 'never', '--no-build'], env=env, timeout=60)
            ids = command(compose + ['ps', '-aq'], env=env, timeout=20).decode().split()
            require(len(ids) == 3)
            records = json.loads(command(['docker', 'inspect', *ids], env=env, timeout=20))
            full = command(['docker', 'ps', '-aq', '--no-trunc', '--filter',
                            'label=com.docker.compose.project=' + project], env=env, timeout=20).decode().split()
            require(len(records) == 3 and len(full) == 3 and all(re.fullmatch(r'[0-9a-f]{64}', value) for value in full)
                    and set(ids) == set(full) == {record.get('Id') for record in records},
                    'Native full Docker IDs differ from created and inspected containers')
            for record in records:
                require(record.get('State', {}).get('Running') is False and
                        record.get('State', {}).get('Status') == 'created' and
                        record.get('Config', {}).get('Image') == plan['image_reference'] and
                        record.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') == project)
                validate_tmpfs(record.get('HostConfig', {}))
        finally:
            command(compose + ['rm', '--force'], env=env, timeout=60)
            require(not command(compose + ['ps', '-aq'], env=env, timeout=20).strip(), 'Canary cleanup requires attention')
    return {'schema': 'colab-stage-create-canary/v1', 'passed': True, 'created_containers': 3,
            'full_container_ids_verified': True,
            'application_started': False, 'owned_containers_removed': True, 'google_credentials_used': False}


if __name__ == '__main__':
    try:
        print(json.dumps(check(), sort_keys=True))
    except Exception:
        print(json.dumps({'passed': False, 'reason': 'isolated Stage create contract or cleanup failed'}))
        raise SystemExit(2)
