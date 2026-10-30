#!/usr/bin/env python3
"""Verify the existing candidate artifact before loading it in isolated CI."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import axxium_production_qualification as qualification


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_bytes())
    qualification.validate_plan(plan)
    qualification.verify_native_stage(plan, datetime.now(timezone.utc))
    qualification.verify_archive(plan, args.archive)
    # The archive validator has already required exactly these two entries,
    # bounded their sizes and verified every OCI blob. Do not extract paths.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as out, zipfile.ZipFile(args.archive) as package:
        with package.open('images.tar') as image:
            shutil.copyfileobj(image, out, length=1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    print('PASS native candidate custody and OCI digest verification')


if __name__ == '__main__':
    main()
