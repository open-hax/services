"""Operator configuration preflight; no candidate code, shell, or secret diagnostics.

Compose remains the authority for dotenv interpolation. Read a fixed projection
through stdin and keep captured private values inside this process.
"""
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess

SERVICES = ('knoxx', 'axxium', 'proxx')
from service_slots import load_slots, proxx_slot, hostname
PROXX_SLOTS = load_slots()
ISSUER = 'https://axxium.promethean.rest/oidc'
AUTHORITY_FILE = '/run/axxium/oidc/authority.json'
KEYS = {
 'axxium': ('AXXIUM_OIDC_CONFIG_FILE', 'AXXIUM_TRUST_PROXY', 'AXXIUM_PUBLIC_BASE_URL', 'AXXIUM_STATE_PATH'),
 'knoxx': ('KNOXX_AXXIUM_OIDC_ENABLED', 'KNOXX_AXXIUM_OIDC_ISSUER', 'KNOXX_PUBLIC_BASE_URL',
           'KNOXX_AXXIUM_OIDC_CLIENT_ID', 'KNOXX_AXXIUM_OIDC_CLIENT_SECRET', 'KNOXX_AXXIUM_OIDC_LINKS'),
 'proxx': ('AXXIUM_OIDC_ISSUER', 'AXXIUM_OIDC_PUBLIC_ORIGIN', 'AXXIUM_OIDC_CLIENT_ID',
           'AXXIUM_OIDC_CLIENT_SECRET', 'AXXIUM_OIDC_ACCOUNT_LINKS',
           'AXXIUM_RELAY_ISSUER', 'AXXIUM_RELAY_AUDIENCE', 'AXXIUM_RELAY_NODES',
           'AXXIUM_RELAY_NODE_ID', 'AXXIUM_RELAY_NODE_SECRET', 'AXXIUM_RELAY_NODE_ISSUER',
           'AXXIUM_RELAY_NODE_SUBJECT', 'AXXIUM_RELAY_NODE_AUDIENCE',
           'PROXX_SCOPED_RELAY_ROLE', 'FEDERATION_BRIDGE_RELAY_URL',
           'PROXX_EXISTING_BIND_ADDRESS','PROXX_DATABASE_URL','PROXX_DATA_PATH','PROXX_OPERATOR_ENV_FILE'),
}

COMPOSE_DOLLAR_SENTINEL = 'SERVICES_COMPOSE_DOLLAR_SENTINEL'


def compose_projection_model(service, projection):
 """Fixed JSON renderer canary; Compose still owns all dotenv semantics."""
 if COMPOSE_DOLLAR_SENTINEL in projection:
  raise ValueError('Reserved Compose projection key')
 return {'services':{service:{'image':'scratch',
         'environment':{**projection,COMPOSE_DOLLAR_SENTINEL:'$$'}}}}


def compose_projection_values(text, service, keys):
 """Decode only the literal-dollar rendering observed in this same model."""
 rendered=json.loads(text)['services'][service]['environment']
 if not isinstance(rendered,dict):
  raise ValueError('Unsupported Compose value rendering')
 marker=rendered.pop(COMPOSE_DOLLAR_SENTINEL,None)
 if marker not in ('$', '$$') or set(rendered)!=set(keys) or any(not isinstance(v,str) for v in rendered.values()):
  raise ValueError('Unsupported Compose value rendering')
 if marker=='$$':
  if any('$' in v.replace('$$','') for v in rendered.values()):
   raise ValueError('Inconsistent Compose value escaping')
  return {k:v.replace('$$','$') for k,v in rendered.items()}
 return rendered


def open_no_symlinks(path, flags):
 """Walk every component with directory descriptors, including parent components."""
 path = Path(path).absolute()
 descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
 try:
  for component in path.parts[1:-1]:
   child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
   os.close(descriptor); descriptor = child
  return os.open(path.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
 finally:
  os.close(descriptor)


def private_file(path, uid):
 try:
  fd = open_no_symlinks(path, os.O_RDONLY)
  try:
   info = os.fstat(fd)
   if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
       or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 1048576):
    raise ValueError()
  finally:
   os.close(fd)
 except (OSError, ValueError):
  raise ValueError('Private configuration requires expected UID, mode0600, bounded regular file') from None


def private_authority(directory, uid=1000):
 """Dedicated private directory, never the application or database state root."""
 try:
  fd = open_no_symlinks(directory, os.O_RDONLY | os.O_DIRECTORY)
  try:
   info = os.fstat(fd)
   if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
    raise ValueError()
   # Refuse accidental mounting of unrelated application/private state.
   if set(os.listdir(fd)) != {'authority.json'}:
    raise ValueError()
   authority = os.open('authority.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
   try:
    meta = os.fstat(authority)
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != uid
        or stat.S_IMODE(meta.st_mode) != 0o600 or not 0 < meta.st_size <= 1048576):
     raise ValueError()
   finally:
    os.close(authority)
  finally:
   os.close(fd)
 except (OSError, ValueError):
  raise ValueError('Authority requires dedicated UID1000 mode0700 directory and mode0600 bounded authority.json without symlinks') from None


def trusted_proxy(value):
 if not value:
  return False
 try:
  entries = [part.strip() for part in value.split(',')]
  if not entries or any(not part or '/' not in part and '.' not in part and ':' not in part for part in entries):
   return False
  networks = [ipaddress.ip_network(part, strict=False) for part in entries]
  return all(network.prefixlen > 0 for network in networks)
 except ValueError:
  return False


def validate(service, values):
 if service not in SERVICES:
  raise ValueError('Unsupported service')
 get = lambda key: values.get(key, '') or ''
 if service == 'axxium':
  config = get('AXXIUM_OIDC_CONFIG_FILE')
  proxy = get('AXXIUM_TRUST_PROXY')
  if proxy and not trusted_proxy(proxy):
   raise ValueError('Trust proxy must name actual proxy addresses or bounded networks')
  if not config:
   return False
  if config != AUTHORITY_FILE or get('AXXIUM_PUBLIC_BASE_URL') != ISSUER.removesuffix('/oidc') or not trusted_proxy(proxy):
   raise ValueError('Authority requires fixed private path, shared origin and explicit proxy hops')
  return True
 prefix = 'KNOXX_AXXIUM_OIDC_' if service == 'knoxx' else 'AXXIUM_OIDC_'
 enabled = get(prefix+'ENABLED') if service == 'knoxx' else bool(get(prefix+'CLIENT_ID'))
 if service == 'knoxx' and enabled not in ('', 'false', 'true'):
  raise ValueError('Invalid consumer enable flag')
 enabled = enabled == 'true' if service == 'knoxx' else enabled
 origin = get('KNOXX_PUBLIC_BASE_URL' if service == 'knoxx' else prefix+'PUBLIC_ORIGIN')
 links = get(prefix+('LINKS' if service == 'knoxx' else 'ACCOUNT_LINKS'))
 if service == 'proxx':
  validate_relay(values)
 if not enabled:
  # A default issuer and empty links are harmless legacy defaults. Fail partial credentials.
  if get(prefix+'CLIENT_ID') or get(prefix+'CLIENT_SECRET') or links not in ('', '[]') or (service == 'proxx' and origin):
   raise ValueError('Partial disabled consumer configuration')
  return False
 if (get(prefix+'ISSUER') != ISSUER or not origin.startswith('https://')
     or not get(prefix+'CLIENT_ID') or not get(prefix+'CLIENT_SECRET')):
  raise ValueError('Consumer requires shared issuer, HTTPS origin and complete client credentials')
 from urllib.parse import urlsplit
 url = urlsplit(origin)
 host_service = 'knoxx' if service == 'knoxx' else 'proxx'
 allowed_hosts = {f'{host_service}.promethean.rest'} | {f'{env}.{host_service}.promethean.rest' for env in ('testing','staging','stealth','yoga','production')}
 if url.hostname not in allowed_hosts or url.username or url.password or url.path or url.query or url.fragment or url.port not in (None, 443):
  raise ValueError('Consumer requires an exact HTTPS origin')
 try:
  parsed = json.loads(links or '[]')
  if not isinstance(parsed, list):
   raise ValueError()
 except (ValueError, TypeError):
  raise ValueError('Consumer links must be an explicit operator JSON array') from None
 # Subject existence, local account existence and link schema are application laws.
 return False


def validate_relay(values):
 get = lambda key: values.get(key, '') or ''
 role = get('PROXX_SCOPED_RELAY_ROLE')
 broker = ('AXXIUM_RELAY_ISSUER','AXXIUM_RELAY_AUDIENCE','AXXIUM_RELAY_NODES')
 node = ('AXXIUM_RELAY_NODE_ISSUER','AXXIUM_RELAY_NODE_AUDIENCE','AXXIUM_RELAY_NODE_ID',
         'AXXIUM_RELAY_NODE_SECRET','AXXIUM_RELAY_NODE_SUBJECT')
 audience = 'https://proxx.promethean.rest'
 if not role:
  if any(get(key) for key in broker+node):
   raise ValueError('Partial scoped relay configuration')
  return None
 if role not in ('broker','node'):
  raise ValueError('Unsupported scoped relay role')
 selected, other = (broker,node) if role == 'broker' else (node,broker)
 if any(get(key) for key in other) or not all(get(key) for key in selected):
  raise ValueError('Scoped relay requires complete, separate broker or node configuration')
 if role == 'broker':
  if get(broker[0]) != ISSUER or get(broker[1]) != audience:
   raise ValueError('Relay requires fixed shared issuer and broker audience')
  try:
   nodes=json.loads(get(broker[2]))
   if not isinstance(nodes,list) or not 1 <= len(nodes) <= 64:
    raise ValueError()
   # Detailed tenant/grant/delegation schemas belong to the application.
  except (ValueError,TypeError):
   raise ValueError('Relay nodes require an explicit operator JSON array') from None
 else:
  import re
  if (get(node[0]) != ISSUER or get(node[1]) != audience
      or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',get(node[2]))
      or not 32 <= len(get(node[3])) <= 512 or not 1 <= len(get(node[4])) <= 128
      or get('FEDERATION_BRIDGE_RELAY_URL') != 'wss://proxx.promethean.rest/api/ui/federation/bridge/ws'):
   raise ValueError('Relay node requires exact broker route and complete private node credential')
 return role


def slot_values(environment, service):
 if service == 'proxx':
  row = proxx_slot(environment)
  return {'PROXX_PUBLIC_HOST':row['hostname'], 'PROXX_LISTEN_PORT':str(row['host_port'])}
 if environment == 'production' and service == 'knoxx':
  return {'KNOXX_PUBLIC_BASE_URL':'https://knoxx.promethean.rest', 'KNOXX_LISTEN_PORT':'8080',
          'KNOXX_AXXIUM_ORIGIN':'https://axxium.promethean.rest'}
 if environment == 'production' and service == 'axxium':
  return {'AXXIUM_PUBLIC_BASE_URL':'https://axxium.promethean.rest', 'AXXIUM_LISTEN_PORT':'8787'}
 if environment not in ('testing', 'staging') or service not in SERVICES:
  raise ValueError('Unsupported receiver slot')
 index = int(environment == 'staging')
 origin = f'https://{environment}.{service}.promethean.rest'
 if service == 'axxium':
  return {'AXXIUM_PUBLIC_BASE_URL':origin, 'AXXIUM_LISTEN_PORT':str(18871+index)}
 if service == 'knoxx':
  return {'KNOXX_PUBLIC_BASE_URL':origin, 'KNOXX_LISTEN_PORT':str(18881+index),
          'KNOXX_AXXIUM_ORIGIN':f'https://{environment}.axxium.promethean.rest'}
 raise ValueError('Unsupported receiver slot')


def preflight(slot, service):
 if service not in SERVICES:
  raise ValueError('Unsupported service')
 if slot.is_symlink():
  raise ValueError('Deployment slot may not be a symlink')
 private_file(slot/'.env', os.getuid())
 expected = slot_values(slot.parent.name, service)
 keys=set(KEYS[service]) | set(expected)
 if service=='proxx' and slot.parent.name in ('production','stealth','yoga'):
  keys.update(('PROXY_AUTH_TOKEN','PROXY_TOKEN_PEPPER','PROXX_SESSION_SECRET'))
 projection = {key: '${'+key+'-}' for key in keys}
 model = compose_projection_model('preflight',projection)
 try:
  result = subprocess.run(['docker', 'compose', '--project-name', 'config-preflight',
     '--project-directory', str(slot), '--env-file', str(slot/'.env'), '-f', '-',
     'config', '--format', 'json'], input=json.dumps(model), text=True,
     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
     env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')}, check=True)
  values = compose_projection_values(result.stdout,'preflight',projection)
  if any(values.get(key) != value for key, value in expected.items()):
   raise ValueError('Operator slot hostname or listen port differs from declared slot')
  mounted = validate(service, values)
  if service in ('proxx','knoxx'):
   origin_key='AXXIUM_OIDC_PUBLIC_ORIGIN' if service=='proxx' else 'KNOXX_PUBLIC_BASE_URL'
   enabled=bool(values.get('AXXIUM_OIDC_CLIENT_ID')) if service=='proxx' else values.get('KNOXX_AXXIUM_OIDC_ENABLED')=='true'
   if enabled and values.get(origin_key)!='https://'+hostname(slot.parent.name,service):
    raise ValueError('Consumer origin differs from selected slot')
  if service == 'proxx' and slot.parent.name in ('production','stealth','yoga'):
   from protected_receiver import existing_state
   existing_state(slot, values)
 except (subprocess.SubprocessError, OSError, KeyError, TypeError, ValueError):
  raise ValueError('Operator configuration preflight refused; inspect private configuration locally') from None
 authority_directory=slot/'oidc'
 if service=='axxium' and slot.parent.name=='production':
  from protected_receiver import enrollment
  admitted=enrollment(slot,'production','axxium')
  state=values.get('AXXIUM_STATE_PATH')
  if not state or not Path(state).is_absolute() or state!=admitted['migration']['data']:
   raise ValueError('Production authority state path differs from enrolled existing state')
  authority_directory=Path(state)/'oidc'
 if not mounted and service=='axxium' and (authority_directory.exists() or authority_directory.is_symlink()):
  raise ValueError('Private authority directory requires explicit activation')
 if mounted:
  private_authority(authority_directory, 1000)
  return [str(slot/'axxium-oidc.compose.yaml')]
 overlays=[]
 if service == 'proxx':
  if slot.parent.name=='production':overlays.append(str(slot/'proxx-production-network.compose.yaml'))
  role = validate_relay(values)
  if role:overlays.append(str(slot/f'proxx-relay-{role}.compose.yaml'))
 return overlays
