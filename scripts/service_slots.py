"""Services-owned slot declarations. Ports are candidate reservations, not live probes."""
import json
from pathlib import Path

LAW = Path(__file__).with_name('service_slots.json')


def load_slots(path=LAW):
    data = json.loads(path.read_text())
    slots = data['proxx']
    if data.get('version') != 1 or set(slots) != {'testing', 'staging', 'production', 'stealth', 'yoga'}:
        raise ValueError('Incomplete Proxx slot law')
    caddy = set(data['reserved_caddy_ports'])
    targets = {18871, 18872, 18881, 18882}
    hosts = set()
    for env, row in slots.items():
        if (set(row) != {'host', 'hostname', 'app_port', 'host_port', 'droplet_target', 'caddy_port', 'role', 'project'}
                or row['host'] != (env if env in ('stealth', 'yoga') else 'droplet')
                or row['hostname'] != ('proxx.promethean.rest' if env == 'production' else f'{env}.proxx.promethean.rest')
                or row['project'] != {'testing':'proxx-testing','staging':'proxx-staging','production':'proxx','stealth':'proxx-local','yoga':'proxx-yoga'}[env]
                or row['app_port'] != 8789 or row['role'] != ('isolated' if env in ('testing', 'staging') else 'existing-stack')):
            raise ValueError('Invalid Proxx host or role')
        for key in ('host_port', 'app_port'):
            if type(row[key]) is not int or not 1024 <= row[key] <= 65535:
                raise ValueError('Invalid slot port')
        host_port = (row['host'], row['host_port'])
        if host_port in hosts:
            raise ValueError('Colliding host application port')
        hosts.add(host_port)
        if env == 'production':
            if row['caddy_port'] is not None or row['droplet_target'] is not None:
                raise ValueError('Production uses existing direct Caddy network')
            continue
        for key, used in (('caddy_port', caddy), ('droplet_target', targets)):
            port = row[key]
            if type(port) is not int or not 1024 <= port <= 65535 or port in used:
                raise ValueError('Colliding or invalid ingress port')
            used.add(port)
        if env in ('testing', 'staging') and row['host_port'] != row['droplet_target']:
            raise ValueError('Isolated app must use declared local target')
    return slots


def proxx_slot(environment):
    try:
        return load_slots()[environment].copy()
    except KeyError:
        raise ValueError('Unsupported Proxx slot') from None


def hostname(environment, service):
    if service == 'proxx':
        return proxx_slot(environment)['hostname']
    if environment not in ('testing', 'staging', 'production', 'stealth', 'yoga') or service not in ('axxium', 'knoxx'):
        raise ValueError('Unsupported environment or service')
    return f'{service}.promethean.rest' if environment == 'production' else f'{environment}.{service}.promethean.rest'
