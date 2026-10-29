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
