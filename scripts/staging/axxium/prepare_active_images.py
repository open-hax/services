"""Fixed archive custody for the disposable active-upgrade fixture, never admission."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import axxium_active_mailbox_release as active
import axxium_production_qualification as custody

INCUMBENT_MANIFEST = 'sha256:199f65be321b9d3c1fa0c667f5fd628b35e64843f6d7470253df97ca0067ca50'
INCUMBENT_CONFIG = 'sha256:c4489c885da2e33958e6adf14ed1395632fc9f98e8864479facaf80dfd409a30'


def role_plan(role):
    if role == 'candidate':
        return deepcopy(active.PLAN)
    custody.require(role == 'incumbent', 'Unknown active fixture image role')
    plan = custody.strict_json(active.PRIOR_PLAN.read_bytes())
    custody.require(hashlib.sha256(active.PRIOR_PLAN.read_bytes()).hexdigest()
                    == active.PLAN['incumbent']['plan_sha256'], 'Incumbent protected plan differs')
    return {**plan, 'image_manifest_digest': INCUMBENT_MANIFEST, 'image_config_digest': INCUMBENT_CONFIG}


def verify_role(role, archive):
    plan = role_plan(role)
    custody.verify_archive(plan, Path(archive))
    return {key: plan[key] for key in ('source_sha', 'image_id', 'image_manifest_digest',
                                     'image_config_digest', 'artifact_sha256')}


def runtime_id(role, observed):
    plan = role_plan(role)
    custody.require(observed in {plan['image_id'], plan['image_manifest_digest'], plan['image_config_digest']},
                    'Loaded fixture image differs from its verified descriptor chain')
    return observed


def extract_role(role, archive, output):
    binding = verify_role(role, archive)
    with zipfile.ZipFile(archive) as package:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as out, package.open('images.tar') as source:
            shutil.copyfileobj(source, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
    return binding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--incumbent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for role in ('candidate', 'incumbent'):
        extract_role(role, getattr(args, role), args.output / (role + '-images.tar'))
    print(json.dumps({'status': 'verified', 'scope': 'fixed synthetic fixture archive custody'}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Active fixture archive custody refused.', file=sys.stderr)
        raise SystemExit(2)
