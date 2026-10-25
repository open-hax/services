"""Protected receiver enrollment and native image authorization.

This deliberately supports updates of an already enrolled fixed Compose stack.
It never stops a foreign process or migrates a systemd application/database.
Root installs the reviewed controller, topology and enrollment after migration
qualification. No upload can provide mounts, scripts, enrollment or credentials.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess

from github_environment_admission import admit
from service_slots import proxx_slot


def topology_matches(slot, service, hashes):
    expected = {'compose.yaml'}
    if service == 'axxium':expected.add('axxium-oidc.compose.yaml')
    if service == 'proxx':
        expected.update({'proxx-relay-node.compose.yaml','proxx-relay-broker.compose.yaml'})
        if slot.parent.name=='production':expected.add('proxx-production-network.compose.yaml')
    if not isinstance(hashes,dict) or set(hashes)!=expected:return False
    for name,digest in hashes.items():
        path=slot/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:return False
    return True


def enrollment(slot, environment, service):
    path = slot / 'protected-receiver.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as source:
            meta = os.fstat(source.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0
                    or stat.S_IMODE(meta.st_mode) != 0o640
                    or meta.st_gid not in set(os.getgroups()) | {os.getegid()}
                    or meta.st_size > 65536):
                raise ValueError()
            data = json.load(source)
        expected = {'version','environment','service','host','native_hostname','operator_uid',
                    'controller_sha','pull_request','project','topology_sha256','migration'}
        host = environment if environment in ('stealth','yoga') else 'droplet'
        if (set(data) != expected or type(data['version']) is not int or data['version'] != 1 or data['environment'] != environment
                or data['service'] != service or data['host'] != host
                or data['native_hostname'] != socket.gethostname()
                or type(data['operator_uid']) is not int or data['operator_uid'] != os.getuid()
                or not re.fullmatch('[0-9a-f]{40}', data['controller_sha'])
                or type(data['pull_request']) is not int or data['pull_request'] <= 0
                or data['project'] != (proxx_slot(environment)['project'] if service=='proxx' else service if environment=='production' else service+'-'+environment)
                or not topology_matches(slot,service,data['topology_sha256'])):
            raise ValueError()
        migration = data['migration']
        if (set(migration) != {'runtime','database','data','effective_env_sha256','backup','rollback','client_access','review','bind_address','operator_env_file','operator_env_sha256'}
                or migration['runtime'] != 'existing-receiver-compose'
                or any(not isinstance(migration[key],str) or not migration[key].strip()
                       for key in ('database','data','backup','rollback','client_access','review'))
                or migration['effective_env_sha256'] != hashlib.sha256((slot/'.env').read_bytes()).hexdigest()):
            raise ValueError()
        # These are evidence references, not proof of an executed backup or a
        # migration. A protected owner must review/enroll them on the real host.
        return data
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError('Protected receiver requires reviewed host/state/backup/rollback/client-access enrollment; foreign runtime cutover is not implemented') from None


def authorize(slot, environment, service, source_sha, image_file):
    data = enrollment(slot, environment, service)
    with image_file.open('rb') as archive:
        digest = hashlib.file_digest(archive, 'sha256').hexdigest()
    result = admit('open-hax/'+service, data['pull_request'], environment,
                   controller_sha=data['controller_sha'], image_sha256=digest)
    if (not result.get('admitted') or result.get('source_sha') != source_sha
            or result.get('deployment_authorization',{}).get('host') != data['host']):
        raise ValueError('Fresh native source/image/host owner admission refused')
    return data['project']


def existing_state(slot, values):
    """Bind private effective values to the enrolled existing database/data/bind.

    The enrollment names exact pre-existing resources and evidence; this does
    not execute a backup, change firewall rules or convert a systemd service.
    """
    data = enrollment(slot, slot.parent.name, slot.name)
    migration = data['migration']
    expected = {
        'PROXX_DATABASE_URL': migration['database'],
        'PROXX_DATA_PATH': migration['data'],
        'PROXX_EXISTING_BIND_ADDRESS': migration['bind_address'],
        'PROXX_OPERATOR_ENV_FILE': migration['operator_env_file'],
    }
    from service_environment_config import private_file
    private_file(Path(migration['operator_env_file']),os.getuid())
    if (hashlib.sha256(Path(migration['operator_env_file']).read_bytes()).hexdigest()!=migration['operator_env_sha256']
            or any(values.get(key) != value for key,value in expected.items())
            or not Path(migration['data']).is_absolute()
            or not Path(migration['data']).is_dir()
            or migration['bind_address'] not in ('127.0.0.1', '0.0.0.0')):
        raise ValueError('Existing database/data/client bind differs from reviewed enrollment')

    # Preserve the current operator token, API-key pepper and session secret.
    # Compose parses the original env; no fallback or generated replacement.
    from service_environment_config import KEYS
    fields=('PROXY_AUTH_TOKEN','PROXY_TOKEN_PEPPER','SESSION_SECRET')
    identity=tuple(key for key in KEYS['proxx'] if key.startswith('AXXIUM_') or key in ('PROXX_SCOPED_RELAY_ROLE','FEDERATION_BRIDGE_RELAY_URL'))
    absent='__SERVICES_UNSET__'
    projection={key:'${'+key+'-}' for key in fields}
    projection.update({key:'${'+key+'-'+absent+'}' for key in identity})
    model={'services':{'preserve':{'image':'scratch','environment':projection}}}
    try:
        result=subprocess.run(['docker','compose','--project-name','existing-secret-preflight',
            '--project-directory',str(slot),'--env-file',migration['operator_env_file'],
            '-f','-','config','--format','json'],input=json.dumps(model),text=True,
            capture_output=True,timeout=10,check=True,env={'PATH':os.environ.get('PATH',os.defpath)})
        original=json.loads(result.stdout)['services']['preserve']['environment']
        for key in identity:
            value=original.get(key,absent)
            if value!=absent and ((key.startswith('AXXIUM_RELAY_') and not value)
                    or value and value!=values.get(key)):
                raise ValueError()
        selected=('PROXY_AUTH_TOKEN','PROXY_TOKEN_PEPPER','PROXX_SESSION_SECRET')
        if any(not original.get(old) or original[old]!=values.get(new) for old,new in zip(fields,selected)):
            raise ValueError()
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        raise ValueError('Existing operator token/pepper/session preservation refused') from None
