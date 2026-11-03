"""GPL-3.0-or-later. Bounded strict JSON and native canonical SHA256 edge.

No policy or filesystem authority. Wide native observations remain strings.
"""
import base64
import hashlib
import json
import re

MAX_INT = 9007199254740991
WIRE_LIMIT = 16 * 1024 * 1024
CAPSULE_LIMIT = 32 * 1024 * 1024


def _reject(*_):
    raise ValueError('shape')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _integer(text):
    if not re.fullmatch(r'0|[1-9][0-9]*', text) or len(text) > 16:
        _reject()
    value = int(text)
    if value > MAX_INT:
        _reject()
    return value


def canonical(value, depth=0):
    if depth > 64:
        _reject()
    if value is None:
        return 'N'
    if type(value) is bool:
        return 'T' if value else 'F'
    if type(value) is int and 0 <= value <= MAX_INT:
        return f'I{value};'
    if type(value) is str:
        raw = value.encode('utf-8', 'strict')
        if b'\x00' in raw:
            _reject()
        return f'S{len(raw)}:{value}'
    if type(value) is list:
        return f'V{len(value)}:' + ''.join(canonical(v, depth + 1) for v in value)
    if type(value) is dict:
        if not all(type(k) is str and re.fullmatch('[a-z][a-z0-9_]*', k) for k in value):
            _reject()
        return f'M{len(value)}:' + ''.join(canonical(k, depth + 1) +
            canonical(value[k], depth + 1) for k in sorted(value))
    _reject()


def decode(raw, limit=WIRE_LIMIT):
    if type(raw) is not bytes or len(raw) > limit:
        _reject()
    try:
        value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=_pairs,
                           parse_int=_integer, parse_float=_reject, parse_constant=_reject)
        canonical(value)
        return value
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise ValueError('shape') from exc


def encode(value, limit=WIRE_LIMIT):
    canonical(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(raw) > limit:
        _reject()
    return raw


def digest(value):
    return hashlib.sha256(('services.local/v1\n' + canonical(value)).encode('utf-8')).hexdigest()


def b64(raw):
    return base64.b64encode(raw).decode('ascii')


def unb64(text):
    if type(text) is not str:
        _reject()
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('shape') from exc
    if b64(raw) != text:
        _reject()
    return raw


def ns64(value):
    return (type(value) is str and bool(re.fullmatch(r'0|-?[1-9][0-9]{0,18}', value))
            and -(2**63) <= int(value) < 2**63)


def u64(value):
    return (type(value) is str and bool(re.fullmatch(r'0|[1-9][0-9]{0,19}', value))
            and int(value) < 2**64)
