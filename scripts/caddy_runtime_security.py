"""Conservative security subset for the unqualified Caddy bootstrap candidate.

Unknown Compose service fields are refused. Only the explicitly modeled Docker
security fields below are checked; this is not a complete Docker configuration
attestation and does not qualify the production bootstrap.
"""
from identity_ingress_transaction import IngressTransactionError

# The approved Compose shape in this candidate deliberately has no extra
# capabilities, security overrides, devices, or namespace sharing.
SERVICE_KEYS = frozenset({'image', 'user', 'command', 'entrypoint', 'healthcheck',
                          'volumes', 'networks', 'environment', 'restart', 'ports',
                          'container_name', 'network_mode'})


def check_compose_security(service):
    if not isinstance(service, dict) or set(service) - SERVICE_KEYS:
        raise IngressTransactionError('Unsupported Compose service options require qualification')


# These are Docker Engine inspect values for the explicitly supported default
# security profile. Docker omits unset Sysctls and Init; all other missing
# fields are unverified and therefore rejected.
HOST_PROFILE = {
    'Privileged': (False,),
    'CapAdd': (None, []), 'CapDrop': (None, []),
    'SecurityOpt': (None, []), 'Devices': (None, []),
    'DeviceRequests': (None, []), 'DeviceCgroupRules': (None, []),
    'PidMode': ('',), 'IpcMode': ('private',), 'UTSMode': ('',),
    'UsernsMode': ('',), 'CgroupnsMode': ('private',),
    'ReadonlyRootfs': (False,), 'Sysctls': (None, {}),
    'Dns': (None, []), 'DnsOptions': (None, []), 'DnsSearch': (None, []),
    'ExtraHosts': (None, []), 'GroupAdd': (None, []), 'VolumesFrom': (None, []),
    'Runtime': ('runc',), 'Init': (None, False), 'AutoRemove': (False,),
    'PublishAllPorts': (False,), 'Cgroup': ('',), 'CgroupParent': ('',),
    'Isolation': ('',), 'OomKillDisable': (None, False),
    'Memory': (0,), 'MemorySwap': (0,), 'CpuShares': (0,), 'CpuQuota': (0,),
    'CpuPeriod': (0,), 'CpusetCpus': ('',), 'CpusetMems': ('',), 'PidsLimit': (None,),
}


OMITTED_IF_UNSET = frozenset({'Sysctls', 'Init', 'PidsLimit'})
MASKED_REQUIRED = frozenset({'/proc/acpi', '/proc/asound', '/proc/kcore', '/proc/keys', '/proc/scsi', '/sys/firmware'})
READONLY_REQUIRED = frozenset({'/proc/bus', '/proc/fs', '/proc/irq', '/proc/sys', '/proc/sysrq-trigger'})

def check_inspected_security(container):
    host = container.get('HostConfig')
    if not isinstance(host, dict):
        raise IngressTransactionError('Inspected Docker security configuration is missing')
    for key, permitted in HOST_PROFILE.items():
        if key not in host and key in OMITTED_IF_UNSET:
            continue
        if key not in host or not any(type(host[key]) is type(value) and host[key] == value for value in permitted):
            raise IngressTransactionError('Inspected Docker security setting differs: ' + key)
    for field, required in (('MaskedPaths', MASKED_REQUIRED), ('ReadonlyPaths', READONLY_REQUIRED)):
        actual = host.get(field)
        if not isinstance(actual, list) or not all(isinstance(value, str) for value in actual) or not required.issubset(actual):
            raise IngressTransactionError('Inspected Docker security setting differs: ' + field)


def reviewed_runtime(service):
    """Translate the supported explicit Compose health and restart settings."""
    import re
    health = service.get('healthcheck')
    if not isinstance(health, dict) or set(health) != {'test', 'interval', 'timeout', 'start_period', 'retries'}:
        raise IngressTransactionError('Compose healthcheck is not fully specified or supported')
    if (not isinstance(health['test'], list) or not all(isinstance(v, str) for v in health['test']) or
            type(health['retries']) is not int or health['retries'] <= 0):
        raise IngressTransactionError('Compose healthcheck is malformed')
    units = {'ns': 1, 'us': 1000, 'ms': 1000000, 's': 1000000000,
             'm': 60000000000, 'h': 3600000000000}
    def duration(value):
        if not isinstance(value, str):
            raise IngressTransactionError('Unsupported Compose health duration')
        match = re.fullmatch(r'([0-9]+)(ns|us|ms|s|m|h)', value)
        if match is None:
            raise IngressTransactionError('Unsupported Compose health duration')
        result = int(match.group(1)) * units[match.group(2)]
        if result <= 0 or result > 9223372036854775807:
            raise IngressTransactionError('Unsupported Compose health duration')
        return result
    restart = service.get('restart')
    if restart not in ('no', 'always', 'unless-stopped'):
        raise IngressTransactionError('Unsupported Compose restart policy')
    return {'healthcheck': {'Test': health['test'], 'Interval': duration(health['interval']),
                            'Timeout': duration(health['timeout']), 'StartPeriod': duration(health['start_period']),
                            'Retries': health['retries']},
            'restart': {'Name': restart, 'MaximumRetryCount': 0}}


def check_inspected_runtime(container, contract, *, validator=False):
    import json
    config, host = container.get('Config') or {}, container.get('HostConfig') or {}
    restart = {'Name': 'no', 'MaximumRetryCount': 0} if validator else contract['restart']
    if json.dumps(config.get('Healthcheck'), sort_keys=True) != json.dumps(contract['healthcheck'], sort_keys=True):
        raise IngressTransactionError('Inspected Docker healthcheck differs from reviewed model')
    if json.dumps(host.get('RestartPolicy'), sort_keys=True) != json.dumps(restart, sort_keys=True):
        raise IngressTransactionError('Inspected Docker restart policy differs from reviewed model')
