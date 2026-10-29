#!/usr/bin/env python3
"""Fail-closed Caddy definition writer for a pre-adopted nonroot runtime.

This intentionally does not bootstrap the nonroot runtime, change mounts or
networks, pull an image, or install the shared host lease. All are prerequisites.
The host controls the paths; the caller supplies only a numeric run identifier.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from ingress_lock import ingress_lock
from identity_ingress_transaction import IngressTransactionError
from caddy_bundle_transaction import BundleItem, apply_bundle_under_lease
from website_ingress_writer import incumbent, selected_config
from axxium_identity_candidate import candidate as identity_candidate

RUNTIME = Path('/srv/open-hax/services/caddy')
MANAGED = Path('/srv/open-hax/state/caddy/managed')
JOURNALS = Path('/srv/open-hax/state/caddy/ingress-transactions')
CONTAINER = 'caddy-caddy-1'
FILES = ('Caddyfile', 'compose.yaml', 'verify.sh', 'env.template', '.env')
STAGE_FILES = ('Caddyfile', 'compose.yaml', 'verify.sh', 'env.template', '.env')


def sha(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def private_dir(path, owner):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
        raise IngressTransactionError('Directory must be private and operator-owned')


def read_file(path, owner):
    value = incumbent(path, owner)
    if value is None:
        raise IngressTransactionError('Required definition file is missing')
    return value


def managed_snapshot(managed, owner):
    info = managed.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise IngressTransactionError('Unsafe managed ingress directory')
    result = {}
    for path in sorted(managed.iterdir()):
        if not path.name.endswith('.caddy'):
            raise IngressTransactionError('Unexpected managed ingress entry')
        result[path.name] = sha(read_file(path, owner))
    return result


def refuse_pending(journals, owner):
    private_dir(journals, owner)
    for directory in journals.iterdir():
        private_dir(directory, owner)
        if (directory / 'pending').exists() or (directory / 'pending').is_symlink():
            raise IngressTransactionError('Prior ingress transaction requires recovery')


def capture(runtime, managed, owner, inspect):
    files = {name: sha(read_file(runtime / name, owner)) for name in FILES}
    container = inspect()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', container['Image']):
        raise IngressTransactionError('Actual running Caddy image is not pinned')
    if container['Config'].get('User') not in ('1000', '1000:1000'):
        raise IngressTransactionError('Caddy must already run as UID 1000 before this writer can recreate it')
    config = selected_config(container['Mounts'], container['Config']['Cmd'], managed)
    if config != '/etc/caddy/identity-ingress/Caddyfile':
        raise IngressTransactionError('Caddy must already use the approved directory-mounted configuration')
    mounts = [m for m in container['Mounts'] if m.get('Destination') == '/etc/caddy/identity-ingress']
    config_dir = runtime / 'identity-ingress'
    if len(mounts) != 1 or mounts[0].get('Source') != str(config_dir) or mounts[0].get('RW') is not False:
        raise IngressTransactionError('Caddy identity directory mount differs')
    files['identity-ingress/Caddyfile'] = sha(read_file(config_dir / 'Caddyfile', owner))
    return {'files': files, 'managed': managed_snapshot(managed, owner),
            'image': container['Image'], 'container': container['Id']}


def run_transaction(lease, *, runtime, managed, journals, stage, owner, inspect, prepare, recreate, verify):
    """Run fixed files and pinned-image recreation under the caller's lease."""
    lease.recheck()
    private_dir(stage, owner)
    refuse_pending(journals, owner)
    manifest = json.loads(read_file(stage / 'manifest.json', owner))
    if set(manifest) != {'baseline', 'candidate'} or set(manifest['candidate']) != set(STAGE_FILES):
        raise IngressTransactionError('Malformed staged manifest')
    baseline = capture(runtime, managed, owner, inspect)
    if baseline != manifest['baseline']:
        raise IngressTransactionError('Caddy, files, or managed routes drifted since capture')
    values = {name: read_file(stage / name, owner) for name in STAGE_FILES}
    if {name: sha(value) for name, value in values.items()} != manifest['candidate']:
        raise IngressTransactionError('Staged candidate digest mismatch')
    # Runtime adoption and environment changes require their own qualified
    # transaction. This writer can safely promote only a route/config change.
    for name in ('compose.yaml', '.env'):
        if sha(values[name]) != baseline['files'][name]:
            raise IngressTransactionError('Compose or environment adoption is not qualified')
    incumbent_config = read_file(runtime / 'Caddyfile', owner)
    if baseline['files']['identity-ingress/Caddyfile'] != sha(incumbent_config):
        raise IngressTransactionError('Live and managed Caddyfile baselines differ')
    if values['Caddyfile'] != incumbent_config:
        try:
            approved = identity_candidate(incumbent_config, baseline['files']['Caddyfile'])
        except ValueError as error:
            raise IngressTransactionError('Unrecognized Caddy route change') from error
        if values['Caddyfile'] != approved:
            raise IngressTransactionError('Caddy candidate changes unrelated routes')
    lease.recheck()
    prepare(baseline['image'])
    items = [BundleItem(runtime / name, values[name], baseline['files'][name]) for name in FILES]
    items.append(BundleItem(runtime / 'identity-ingress/Caddyfile', values['Caddyfile'],
                            baseline['files']['identity-ingress/Caddyfile']))
    journal = journals / stage.name
    journal.mkdir(mode=0o700)
    private_dir(journal, owner)

    def apply():
        recreate(baseline['image'])
        verify(baseline['image'], values['Caddyfile'] != incumbent_config)
        if managed_snapshot(managed, owner) != baseline['managed']:
            raise IngressTransactionError('Managed routes changed during Caddy deployment')

    def restore():
        recreate(baseline['image'])
        verify(baseline['image'], False)
        if managed_snapshot(managed, owner) != baseline['managed']:
            raise IngressTransactionError('Managed routes changed during Caddy rollback')

    return apply_bundle_under_lease(lease, items, journal, apply, restore, owner=owner)


def command(args, *, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                          env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                               'HOME': '/home/deploy'}).stdout


def inspect_container():
    return json.loads(command(['docker', 'inspect', CONTAINER]))[0]


def prepare(image):
    # Use the running image ID; a mutable tag must never alter rollback identity.
    override = RUNTIME / '.ingress-pinned-image.yaml'
    # A fixed pre-provisioned override avoids generating live unjournaled files.
    if read_file(override, os.getuid()) != ('services:\n  caddy:\n    image: ' + image + '\n').encode():
        raise IngressTransactionError('Pinned-image override is absent or does not match the incumbent image')
    inside = command(['docker', 'exec', '--user', '1000:1000', CONTAINER, 'cat', '/etc/caddy/identity-ingress/Caddyfile'])
    if inside != read_file(RUNTIME / 'identity-ingress/Caddyfile', os.getuid()):
        raise IngressTransactionError('Caddy configuration differs from its mounted host file')


def recreate(image):
    override = RUNTIME / '.ingress-pinned-image.yaml'
    if read_file(override, os.getuid()) != ('services:\n  caddy:\n    image: ' + image + '\n').encode():
        raise IngressTransactionError('Pinned-image override changed')
    command(['docker', 'compose', '--project-name', 'caddy', '--env-file', '.env',
             '-f', 'compose.yaml', '-f', override.name, 'up', '-d', '--no-deps',
             '--pull', 'never', '--force-recreate', '--wait', '--wait-timeout', '90', 'caddy'], cwd=RUNTIME)


def verify(image, require_oidc):
    container = inspect_container()
    if container['Image'] != image or container['Config'].get('User') not in ('1000', '1000:1000') or not container['State']['Running']:
        raise IngressTransactionError('Caddy recreation did not preserve image, identity, or running state')
    capture(RUNTIME, MANAGED, os.getuid(), lambda: container)
    inside = command(['docker', 'exec', '--user', '1000:1000', CONTAINER, 'cat', '/etc/caddy/identity-ingress/Caddyfile'])
    if inside != read_file(RUNTIME / 'identity-ingress/Caddyfile', os.getuid()):
        raise IngressTransactionError('Recreated Caddy mounted the wrong configuration')
    command(['docker', 'exec', '--user', '1000:1000', CONTAINER, 'caddy', 'validate', '--config',
             '/etc/caddy/identity-ingress/Caddyfile', '--adapter', 'caddyfile'])
    command(['bash', '-c', 'set -euo pipefail; set -a; . ./.env; set +a; ./verify.sh'], cwd=RUNTIME)
    if require_oidc:
        discovery = command(['curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                             '--resolve', 'axxium.promethean.rest:443:127.0.0.1',
                             'https://axxium.promethean.rest/oidc/.well-known/openid-configuration'])
        if json.loads(discovery).get('issuer') != 'https://axxium.promethean.rest/oidc':
            raise IngressTransactionError('Axxium OIDC issuer did not answer through production ingress')


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('capture', 'apply') or not re.fullmatch(r'[0-9]+-[0-9]+', sys.argv[2]):
        raise SystemExit('Expected capture|apply and numeric run-attempt ID')
    stage = RUNTIME / '.ingress-candidates' / sys.argv[2]
    owner = os.getuid()
    with ingress_lock() as lease:
        if sys.argv[1] == 'capture':
            private_dir(stage, owner)
            refuse_pending(JOURNALS, owner)
            baseline = capture(RUNTIME, MANAGED, owner, inspect_container)
            candidate = {name: sha(read_file(stage / name, owner)) for name in STAGE_FILES}
            fd = os.open(stage / 'manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump({'baseline': baseline, 'candidate': candidate}, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            print('Caddy baseline captured')
        else:
            run_transaction(lease, runtime=RUNTIME, managed=MANAGED, journals=JOURNALS,
                            stage=stage, owner=owner, inspect=inspect_container, prepare=prepare,
                            recreate=recreate, verify=verify)
            print('Caddy transaction applied and verified')


if __name__ == '__main__':
    main()
