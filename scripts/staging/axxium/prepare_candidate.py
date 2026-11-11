#!/usr/bin/env python3
"""Verify the archived historical candidate before loading it in isolated CI."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import axxium_production_qualification as qualification


FIXTURE_PLAN_SHA256 = '6025ff0257a28711cc7847ca6935b97ffc3b11e4ccef76c3eb27a89f16bcafe1'
# The classic Docker save also emitted unreferenced legacy layer JSON records.
# These are the exact blob names in the historical fixture, never runnable
# image data. The strict OCI verifier checks the projected archive separately.
LEGACY_LAYER_METADATA = frozenset('blobs/sha256/' + digest for digest in (
    '05ebb58fc71038f0063523ef6f23ef3b51d4f07f0149f48b092676ce3da0d7fd',
    '30a7ed937ab3c0c0c06cf8968ca222a545e4333273eee8915b2af8ae721d6a0e',
    '6b20faf0102614b1290e22d7dbe66115ea1cb6d3edcdde901ad3f42c7b68089c',
    '8bd87858499b8778b26ed35a9cc052fea5798d6b61664c0059a53fd451e07f89',
    'ac6cab210f037bd9e93403f75c93cf24e4cb034fd351a830308a69573c2177a0',
    'bddbf4942d1ab783b05a2e3236eae2421716bc495afe94ced7427180b1756eb7',
    'c97b03879397d8cde4b4cb3eaee9686dbccda3affa7a55448bcf9d42c3cabffe',
    'ca33e2ee30da2a366fea20ce00bf7945c2a3634c6f2b46dec3aa594d9018201b',
    'e39ae583f7c887dd4347dcfb7f35080daa4af10379d1d86466025645b927256b',
    'e7e153ad5535ed1231d4f5816471d555e38fce149ca7b2eb47aac3665f0c93d3',
))


def project_fixture_tar(plan, archive, output):
    """Verify the retained original ZIP and omit only its pinned legacy metadata."""
    with archive.open('rb') as stream:
        digest, _ = qualification.stream_digest(stream, qualification.MAX_ZIP)
        qualification.require(digest == plan['artifact_sha256'], 'recovery fixture differs from native digest')
        with zipfile.ZipFile(stream) as package:
            members = package.infolist()
            qualification.require(len(members) == 2 and {item.filename for item in members}
                                  == {'images.tar', 'images.sha256'}, 'unexpected fixture archive members')
            qualification.require(package.getinfo('images.sha256').file_size == 65
                                  and 0 < package.getinfo('images.tar').file_size <= qualification.MAX_TAR,
                                  'fixture archive member size differs')
            with package.open('images.tar') as image:
                tar_digest, _ = qualification.stream_digest(image, qualification.MAX_TAR)
            qualification.require(package.read('images.sha256') == (tar_digest + '\n').encode('ascii'),
                                  'fixture inner archive checksum differs')
            omitted = set()
            with package.open('images.tar') as image, tarfile.open(fileobj=image, mode='r|') as source, \
                    tarfile.open(output, mode='x') as target:
                for member in source:
                    if member.name in LEGACY_LAYER_METADATA:
                        qualification.require(member.isfile() and member.name not in omitted,
                                              'fixture legacy layer metadata differs')
                        omitted.add(member.name)
                    else:
                        target.addfile(member, source.extractfile(member) if member.isfile() else None)
            qualification.require(omitted == LEGACY_LAYER_METADATA, 'fixture legacy layer metadata is incomplete')


def verify_projection(plan, image, package):
    """Apply the unchanged production OCI validator to the synthetic image projection."""
    with image.open('rb') as stream:
        tar_digest, _ = qualification.stream_digest(stream, qualification.MAX_TAR)
    with zipfile.ZipFile(package, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as projected:
        projected.write(image, 'images.tar')
        projected.writestr('images.sha256', tar_digest + '\n')
    with package.open('rb') as stream:
        projected_digest, _ = qualification.stream_digest(stream, qualification.MAX_ZIP)
    qualification.verify_archive({**plan, 'artifact_sha256': projected_digest}, package)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    raw = args.plan.read_bytes()
    qualification.require(hashlib.sha256(raw).hexdigest() == FIXTURE_PLAN_SHA256,
                          'historical recovery fixture plan differs')
    plan = qualification.strict_json(raw)
    qualification.validate_plan(plan)
    # The mirror preserves the reviewed artifact bytes after Actions retention
    # expires. Production admission separately requires a live native artifact.
    qualification.verify_native_stage_run(plan, datetime.now(timezone.utc))
    with tempfile.TemporaryDirectory(prefix='axxium-recovery-image.') as directory:
        image = Path(directory) / 'images.tar'
        project_fixture_tar(plan, args.archive, image)
        verify_projection(plan, image, Path(directory) / 'projected.zip')
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out, image.open('rb') as verified:
            shutil.copyfileobj(verified, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
    print('PASS retained candidate custody and projected OCI digest verification')


if __name__ == '__main__':
    main()
