#!/usr/bin/env python3
"""Read the fixed GPL image anonymously when public; retain private observations."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.request

IMAGE = 'ghcr.io/open-hax/cloud-cuckoo@sha256:f208ca09c52f33fbff03c8423d18c607db7069876c15597bed8d66b67f1cc26c'
SOURCE = '6e5b5ded1841364b97769e4093445b116873611f'
IMAGE_ID = 'sha256:942907824a8e1e5f4dcbccd2a68ba112764bafa86e4f2ab59adcfe4fdff9a95d'
LICENSE_PATH = '/usr/share/licenses/cloud-cuckoo/LICENSE'
LICENSE_SHA256 = '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986'


class Refusal(Exception):
    pass


def command(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=180, check=False, **kwargs)
    if result.returncode:
        raise Refusal()
    return result.stdout


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def metadata(token):
    request = urllib.request.Request('https://api.github.com/orgs/open-hax/packages/container/cloud-cuckoo', headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'services-colab-registry-read'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise Refusal()
            return response.status, json.loads(data)
    except urllib.error.HTTPError as error:
        return error.code, None


def observe(output, *, run=command, read=metadata):
    if (os.environ.get('GITHUB_REPOSITORY') != 'open-hax/services' or
            os.environ.get('GITHUB_REF') != 'refs/heads/main' or
            os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch' or
            not re.fullmatch(r'[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) or
            not os.environ.get('GITHUB_RUN_ID', '').isdecimal()):
        raise Refusal()
    result = {'schema': 'services-colab-registry-read/v1', 'repository': 'open-hax/services',
              'workflow_sha': os.environ['GITHUB_SHA'], 'run_id': int(os.environ['GITHUB_RUN_ID']),
              'image_reference': IMAGE, 'source_sha': SOURCE, 'expected_image_id': IMAGE_ID,
              'metadata_http_status': None, 'package_visibility': None,
              'publication_target': 'public', 'anonymous_digest_pull_verified': False,
              'license_verified': False, 'license_sha256': LICENSE_SHA256,
              'registry_pull_succeeded': False, 'image_contract_verified': False, 'stage': 'native_context'}
    stage = 'native_context'
    try:
        if run(['git', 'rev-parse', 'HEAD']).decode().strip() != result['workflow_sha']:
            raise Refusal()
        token, actor = os.environ.get('GH_TOKEN'), os.environ.get('GITHUB_ACTOR')
        if not token or not actor:
            raise Refusal()
        stage = 'package_metadata'
        status, package = read(token)
        result['metadata_http_status'] = status
        if isinstance(package, dict) and package.get('visibility') in ['private', 'public', 'internal']:
            result['package_visibility'] = package['visibility']
        if (status != 200 or not isinstance(package, dict) or package.get('visibility') not in ['private', 'public'] or
                package.get('name') != 'cloud-cuckoo' or package.get('package_type') != 'container' or
                not isinstance(package.get('owner'), dict) or package['owner'].get('login') != 'open-hax'):
            raise Refusal()
        with tempfile.TemporaryDirectory(prefix='services-colab-read-') as auth:
            anonymous = package['visibility'] == 'public'
            child_env = ({'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': auth, 'DOCKER_CONFIG': auth}
                         if anonymous else {**{key: value for key, value in os.environ.items()
                                               if key not in {'GH_TOKEN', 'GITHUB_TOKEN'}}, 'DOCKER_CONFIG': auth})
            try:
                if not anonymous:
                    stage = 'registry_login'
                    run(['docker', 'login', 'ghcr.io', '--username', actor, '--password-stdin'], input=token.encode(), env=child_env)
                stage = 'immutable_pull'
                run(['docker', 'pull', IMAGE], env=child_env)
                result['registry_pull_succeeded'] = True
                stage = 'image_contract'
                records = json.loads(run(['docker', 'image', 'inspect', IMAGE], env=child_env))
                if not isinstance(records, list) or len(records) != 1:
                    raise Refusal()
                image = records[0]
                config = image.get('Config', {})
                labels = config.get('Labels', {})
                if (image.get('Id') != IMAGE_ID or config.get('User') != '1000:1000' or
                        labels.get('org.opencontainers.image.source') != 'https://github.com/open-hax/cloud-cuckoo' or
                        labels.get('org.opencontainers.image.licenses') != 'GPL-3.0-or-later' or
                        labels.get('org.opencontainers.image.revision') != SOURCE):
                    raise Refusal()
                result['image_contract_verified'] = True
                stage = 'full_license'
                check = ("from pathlib import Path; import hashlib; "
                         f"assert hashlib.sha256(Path({LICENSE_PATH!r}).read_bytes()).hexdigest()=={LICENSE_SHA256!r}")
                run(['docker', 'run', '--rm', '-i', '--pull', 'never', '--network', 'none', '--read-only',
                     '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                     '--pids-limit', '64', '--memory', '128m', IMAGE_ID, 'python', '-'], input=check.encode(), env=child_env)
                result['license_verified'] = True
                result['anonymous_digest_pull_verified'] = anonymous
                stage = 'verified'
            finally:
                if not anonymous:
                    try:
                        run(['docker', 'logout', 'ghcr.io'], env=child_env)
                    except Refusal:
                        pass
    except (Refusal, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass  # The receipt carries only a fixed stage and projected observations.
    result['stage'] = stage
    Path(output).write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    Path(output).chmod(0o600)
    print(json.dumps(result, sort_keys=True))
    return 0 if stage == 'verified' else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        return observe(args.output)
    except (Refusal, OSError, ValueError, TypeError):
        print('Registry read probe refused before a native receipt could be recorded')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
