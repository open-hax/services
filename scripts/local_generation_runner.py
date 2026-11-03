"""GPL-3.0-or-later. Fixed isolated child of the owner BB facade only.

stdin: 8 ASCII decimal owner-byte length, owner bytes, then original untrusted
request bytes (EOF means recovery only). No executable/path/flags in data.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
# -I ignores PYTHONDONTWRITEBYTECODE. The facade explicitly supplies -B and
# this root-owned absent prefix also prevents loading an unpinned local pyc.
sys.dont_write_bytecode = True
sys.pycache_prefix = '/nonexistent/services-local-generations-cache'
sys.path.insert(0, str(HERE))
import local_generation_wire as wire

SOURCES = {
    'facade': HERE / 'local-generation.clj',
    'runner': HERE / 'local_generation_runner.py',
    'edge': HERE / 'local_generation_fs.py',
    'wire': HERE / 'local_generation_wire.py',
    'validator': HERE / 'local_generation_edn.py',
    'laws': HERE / 'local-generation-laws.clj',
    'pure': HERE.parent / 'src/services/local_generation.cljc',
}


def main():
    revision = '0' * 40
    engine = None
    try:
        header = sys.stdin.buffer.read(8)
        if len(header) != 8 or not header.isdigit():
            raise ValueError('shape')
        length = int(header)
        if not 0 < length <= wire.WIRE_LIMIT:
            raise wire.WireError('limit')
        owner_raw = sys.stdin.buffer.read(length)
        if len(owner_raw) != length:
            raise ValueError('shape')
        owner = wire.decode(owner_raw)
        if (type(owner) is not dict or set(owner) != {'schema', 'policy', 'revision', 'sources', 'python_sha256'} or
                owner['schema'] != 'services.local-launch/v1'):
            raise ValueError('shape')
        expected = [{'id': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for name, path in sorted(SOURCES.items())]
        if owner['sources'] != expected or owner['python_sha256'] != hashlib.sha256(Path('/usr/bin/python3').read_bytes()).hexdigest():
            raise wire.WireError('pin')
        import local_generation_fs as fs
        fs.shape('policy', owner['policy'])
        revision = owner['revision']
        if not isinstance(revision, str) or not fs.re.fullmatch('[a-f0-9]{40}', revision):
            revision = '0' * 40
            raise wire.WireError('pin')
        request_raw = sys.stdin.buffer.read(wire.WIRE_LIMIT + 1)
        if len(request_raw) > wire.WIRE_LIMIT:
            raise wire.WireError('limit')
        with fs.Engine(owner['policy'], revision) as engine:
            recovered = engine.recover()
            if recovered['status'] != 'checked' or not request_raw:
                result = recovered
            else:
                request = wire.decode(request_raw)
                if type(request) is dict and request.get('action') == 'preview':
                    fs.shape('preview-request', request)
                    result = engine.preview(request['seed'])
                else:
                    result = engine.execute(request)
        raw = wire.encode(result)
        sys.stdout.buffer.write(raw)
        return {'previewed': 0, 'checked': 0, 'applied': 0, 'rolled-back': 0,
                'denied': 2, 'unavailable': 3, 'failed-restored': 4, 'held': 5}[result['status']]
    except Exception as exc:
        # No path, payload or exception text crosses the fixed boundary.
        uncertain = engine is not None and engine.writes != 0
        code = getattr(exc, 'code', 'unsupported' if isinstance(exc, OSError) else 'shape')
        if code not in ('shape', 'limit', 'pin', 'validator', 'approval', 'expiry', 'replay', 'unsupported',
                        'ownership', 'dirty', 'ancestor-drift', 'destination-drift', 'current-drift', 'lock', 'io',
                        'interrupted', 'recovery-uncertain'):
            code = 'shape'
        unavailable = code == 'unsupported'
        result = {'schema': 'services.local-result/v1', 'action': 'check',
                  'status': 'held' if uncertain else 'unavailable' if unavailable else 'denied',
                  'plan': None, 'plan_sha256': None, 'services_revision': revision, 'before': None, 'after': None,
                  'target_writes': None if uncertain else 0, 'recovery': 'uncertain' if uncertain else 'not-needed',
                  'first_failure': {'code': 'recovery-uncertain' if uncertain else code, 'stage': 'input',
                                    'detail': 'Fixed local boundary refused input.'},
                  'recovery_failures': [], 'journal_sha256': None}
        sys.stdout.buffer.write(json.dumps(result, separators=(',', ':')).encode())
        return 5 if uncertain else 3 if unavailable else 2


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
