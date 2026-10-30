"""Fail closed unless the current process network namespace is loopback-only."""
from pathlib import Path
import socket


def require_isolated():
    if {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise RuntimeError('Fixture namespace has non-loopback interfaces')
    ipv4 = Path('/proc/net/route').read_text().splitlines()[1:]
    if any(line.strip() for line in ipv4):
        raise RuntimeError('Fixture namespace has an IPv4 route')
    for line in Path('/proc/net/ipv6_route').read_text().splitlines():
        parts = line.split()
        if len(parts) != 10 or parts[-1] != 'lo':
            raise RuntimeError('Fixture namespace has an unexpected IPv6 route')
        destination, prefix, flags = parts[0], parts[1], int(parts[8], 16)
        loopback = destination == '0' * 31 + '1' and prefix == '80'
        rejected_default = destination == '0' * 32 and prefix == '00' and flags & 0x200
        if not (loopback or rejected_default):
            raise RuntimeError('Fixture namespace has an IPv6 route')
