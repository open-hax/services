"""GPL-3.0-or-later. Fixed *inert-edn-v1* validator, not a general EDN reader.

Independently checks services.inert-edn/v1 descriptor intent against one inert
EDN map containing :fixture/value and a string. No reader tags, evaluation,
imports, commands or producer success flags. Other formats stay unavailable.
"""
import json
import re

try:
    from . import local_generation_wire as wire
except ImportError:
    import local_generation_wire as wire


def validate(descriptor, payload):
    try:
        intent = wire.decode(descriptor)
        if (type(intent) is not dict or set(intent) != {'schema', 'value'} or
                intent['schema'] != 'services.inert-edn/v1' or type(intent['value']) is not str):
            return False
        text = payload.decode('utf-8', 'strict')
        # These escapes are precisely the shared inert string subset of EDN
        # and JSON. Escaped slash is deliberately NOT admitted as EDN.
        token = r'"(?:[^"\\\x00-\x1f]|\\(?:["\\bfnrt]|u[0-9a-fA-F]{4}))*"'
        match = re.fullmatch(r'\s*\{\s*:fixture/value\s+(' + token + r')\s*\}\s*', text)
        if not match:
            return False
        value = json.loads(match.group(1))
        wire.canonical(value)
        return value == intent['value']
    except (ValueError, UnicodeError, RecursionError):
        return False
