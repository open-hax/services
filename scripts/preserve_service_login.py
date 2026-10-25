#!/usr/bin/env python3
"""Retain optional login configuration through the legacy renderer.

Compose alone parses effective dotenv values. Explicit updates come only from a
protected JSON secret, never extra_env. No captured private values are logged.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from service_environment_config import KEYS, validate, private_file, private_authority

OPTIONAL={service:tuple(k for k in keys if k not in ('AXXIUM_STATE_PATH','PROXX_DATA_PATH','PROXX_DATABASE_URL','PROXX_EXISTING_BIND_ADDRESS','PROXX_OPERATOR_ENV_FILE')) for service,keys in KEYS.items()}


def projection(service, path):
    model={'services':{'optional':{'image':'scratch','environment':{k:'${'+k+'-}' for k in OPTIONAL[service]}}}}
    result=subprocess.run(['docker','compose','--project-name','optional-login-preflight',
        '--project-directory',str(path.parent),'--env-file',str(path),'-f','-',
        'config','--format','json'],input=json.dumps(model),text=True,capture_output=True,
        timeout=10,check=True,env={'PATH':os.environ.get('PATH',os.defpath)})
    return json.loads(result.stdout)['services']['optional']['environment']


def assignment_text(values):
    if any(not isinstance(v,str) or any(ord(c)<32 for c in v) or "'" in v for v in values.values()):
        raise ValueError('Optional values must be single-line shell-safe strings')
    return ''.join(f"{key}='{value}'\n" for key,value in values.items())


def append_values(path, values):
    text=assignment_text(values)
    with path.open('a') as target:
        target.write(text)


def render(service, path, raw):
    # The protected secret is inherited by the whole stack, including ingress.
    # Only its owning login services interpret it; unrelated renders stay intact.
    if service not in KEYS:return
    values=json.loads(raw) if raw else {}
    if not isinstance(values,dict) or set(values)-(set(KEYS[service])-{'AXXIUM_STATE_PATH','PROXX_DATA_PATH','PROXX_DATABASE_URL','PROXX_EXISTING_BIND_ADDRESS','PROXX_OPERATOR_ENV_FILE'}):
        raise ValueError('Unknown protected optional configuration key')
    if values:
        validate(service,values)
        replacement=assignment_text(values)
        # Candidate assignments are emitted by our fixed renderer as KEY=... .
        # Select names only; Compose remains the effective-value parser.
        retained=''.join(line for line in path.read_text().splitlines(keepends=True)
                         if line.partition('=')[0] not in values)
        path.write_text(retained+('\n' if retained and not retained.endswith('\n') else '')+replacement)


def preserve(service, existing, candidate, state):
    if service not in KEYS:return
    private_file(candidate,os.getuid())
    values={}
    if existing.exists() or existing.is_symlink():
        private_file(existing,os.getuid())
        values=projection(service,existing)
    # Presence controls replacement only. Compose parses every selected value.
    names=re.findall(r'^([A-Z][A-Z0-9_]*)=',candidate.read_text(),re.M)
    selected=set(names)&set(OPTIONAL[service])
    if len(names)!=len(set(names)):
        raise ValueError('Duplicate renderer assignments')
    new=projection(service,candidate)
    merged={**values,**{key:new[key] for key in selected}}
    mounted=validate(service,merged)
    if service in ('proxx','knoxx'):
        origin_key='AXXIUM_OIDC_PUBLIC_ORIGIN' if service=='proxx' else 'KNOXX_PUBLIC_BASE_URL'
        enabled=bool(merged.get('AXXIUM_OIDC_CLIENT_ID')) if service=='proxx' else merged.get('KNOXX_AXXIUM_OIDC_ENABLED')=='true'
        if enabled and merged.get(origin_key)!='https://'+service+'.promethean.rest':
            raise ValueError('Legacy production consumer origin must be canonical')
    if mounted:private_authority(state/'oidc',1000)
    elif service=='axxium' and ((state/'oidc').exists() or (state/'oidc').is_symlink()):
        raise ValueError('Authority directory requires explicit activation')
    append_values(candidate,{key:value for key,value in values.items() if key not in selected})
    # Compose resolves optional overlay relative to the installed fixed stack.
    # The remote command invokes preflight before any state-directory creation.
    if service=='axxium':
        append_values(candidate,{'COMPOSE_FILE':'compose.yaml:axxium-oidc.compose.yaml' if mounted else 'compose.yaml'})
    return mounted


if __name__=='__main__':
    try:
        operation,service,*args=sys.argv[1:]
        if operation=='render':render(service,Path(args[0]),os.environ.get('OPTIONAL_LOGIN_CONFIGURATION',''))
        elif operation=='preserve':preserve(service,*(Path(p) for p in args))
        else:raise ValueError('Unsupported operation')
    except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):
        raise SystemExit('Optional login configuration refused; inspect protected values locally') from None
