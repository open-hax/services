#!/usr/bin/env python3
"""Native operator adapter to the SAME fixed archive receiver, no shell lane.

Only an already root-enrolled current user/host can proceed; native current-main
qualification and fresh exact archive owner authorization are re-read by receiver.
"""
import importlib.util
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    if len(sys.argv) != 5 or os.environ.get('SSH_ORIGINAL_COMMAND'):
        raise SystemExit('Usage: receive-service-image-local.py ENVIRONMENT SERVICE SOURCE_SHA - < IMAGE.tar')
    # Fourth argument is deliberately the literal stdin marker, never a script.
    environment, service, sha, stream = sys.argv[1:]
    if environment not in ('production', 'stealth', 'yoga') or stream != '-':
        raise SystemExit('A protected fixed receiver slot and stdin image archive are required')
    path = Path(__file__).with_name('receive-service-environment.py')
    spec = importlib.util.spec_from_file_location('receiver', path)
    receiver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(receiver)
    sys.argv = [str(path), environment, service]
    os.environ['SSH_ORIGINAL_COMMAND'] = f'receive {environment} {service} {sha}'
    raise SystemExit(receiver.main())
