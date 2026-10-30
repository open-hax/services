#!/usr/bin/env python3
"""Derive an exclusively named fixture image archive from the admitted ZIP.

Only top-level repository/tag metadata is changed. OCI content blobs must
retain their original bytes, including the candidate manifest and config. This
does not load an image, contact Docker, or grant deployment authorization.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import axxium_production_qualification as qualification

SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
MANIFEST = '6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
CONFIG = 'c45e58aadb02622183ccf60a971da2669fc033231b677c12e963920234b9b542'
ORIGINAL_TAG = 'promethean-axxium:' + SOURCE
CHANGED = {'index.json', 'manifest.json', 'repositories'}


class Refused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def private_output(path):
    parent = path.parent
    info = parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == 0o700 and not parent.is_symlink(),
            'Output directory must be private and owned by the caller')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    return os.fdopen(fd, 'wb')


def rewritten(name, data, tag):
    value = qualification.strict_json(data)
    repository, label = tag.split(':', 1)
    if name == 'index.json':
        descriptors = value['manifests']
        require(len(descriptors) == 1 and descriptors[0]['digest'] == 'sha256:' + MANIFEST,
                'Candidate OCI descriptor differs')
        annotations = descriptors[0]['annotations']
        require(annotations['io.containerd.image.name'] == 'docker.io/library/' + ORIGINAL_TAG and
                annotations['org.opencontainers.image.ref.name'] == SOURCE,
                'Candidate OCI reference differs')
        annotations['io.containerd.image.name'] = 'docker.io/library/' + tag
        annotations['org.opencontainers.image.ref.name'] = label
    elif name == 'manifest.json':
        require(len(value) == 1 and value[0]['RepoTags'] == [ORIGINAL_TAG] and
                value[0]['Config'] == 'blobs/sha256/' + CONFIG, 'Docker manifest differs')
        value[0]['RepoTags'] = [tag]
    elif name == 'repositories':
        require(set(value) == {'promethean-axxium'} and
                set(value['promethean-axxium']) == {SOURCE}, 'Repository mapping differs')
        value = {repository: {label: value['promethean-axxium'][SOURCE]}}
    else:
        raise Refused('Unexpected metadata name')
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def fingerprint_blobs(archive):
    found = {}
    with tarfile.open(archive, mode='r:') as source:
        for entry in source:
            match = re.fullmatch(r'blobs/sha256/([0-9a-f]{64})', entry.name)
            if not match:
                continue
            require(entry.isfile() and entry.name not in found, 'Invalid or duplicate blob')
            stream = source.extractfile(entry)
            digest, size = qualification.stream_digest(stream, qualification.MAX_TAR)
            require(digest == match.group(1) and size == entry.size, 'Derived blob differs')
            found[entry.name] = (size, digest)
    require('blobs/sha256/' + MANIFEST in found and 'blobs/sha256/' + CONFIG in found,
            'Candidate manifest or config is absent')
    return found


def derive(plan_path, archive_path, output_path, run_id):
    require(re.fullmatch(r'[0-9a-f]{32}', run_id) is not None, 'Exact random run ID required')
    plan_raw = plan_path.read_bytes()
    require(hashlib.sha256(plan_raw).hexdigest() == qualification.WAIVER_PLAN_SHA256,
            'Reviewed candidate plan differs')
    plan = json.loads(plan_raw)
    qualification.validate_plan(plan)
    qualification.verify_archive(plan, archive_path)
    # The archive hash and every OCI blob have been verified before any output.
    tag = 'axxium-fixture-' + run_id + ':fixture-' + run_id
    source_blobs = {}
    changed = {}
    created = False
    try:
        with private_output(output_path) as out:
            created = True
            with zipfile.ZipFile(archive_path) as package, package.open('images.tar') as inner, \
                 tarfile.open(fileobj=inner, mode='r|') as source, \
                 tarfile.open(fileobj=out, mode='w|') as derived:
                for entry in source:
                    if entry.name in CHANGED:
                        require(entry.isfile() and entry.name not in changed and entry.size <= qualification.MAX_JSON,
                                'Unexpected candidate metadata')
                        raw = source.extractfile(entry).read(qualification.MAX_JSON + 1)
                        require(len(raw) == entry.size, 'Truncated candidate metadata')
                        value = rewritten(entry.name, raw, tag)
                        changed[entry.name] = value
                        entry.size = len(value)
                        derived.addfile(entry, io.BytesIO(value))
                    elif entry.isfile():
                        require(re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', entry.name) is not None or
                                entry.name == 'oci-layout', 'Unexpected candidate content')
                        # The final scan re-hashes all output blobs and compares
                        # them with their content-addressed names.
                        if entry.name.startswith('blobs/sha256/'):
                            require(entry.name not in source_blobs, 'Duplicate candidate blob')
                            source_blobs[entry.name] = (entry.size, entry.name.rsplit('/', 1)[-1])
                        derived.addfile(entry, source.extractfile(entry))
                    elif entry.isdir():
                        require(entry.name.rstrip('/') in ('blobs', 'blobs/sha256'), 'Unexpected directory')
                        derived.addfile(entry)
                    else:
                        raise Refused('Unsafe candidate member type')
            out.flush()
            os.fsync(out.fileno())
        require(set(changed) == CHANGED, 'Candidate reference metadata is incomplete')
        require(fingerprint_blobs(output_path) == source_blobs, 'Derived image blob set differs')
        with tarfile.open(output_path, mode='r:') as result:
            for name, expected in changed.items():
                require(result.extractfile(name).read() == expected, 'Derived reference differs')
        # Ensure the source remained the accepted immutable ZIP through copying.
        with archive_path.open('rb') as src:
            actual, _ = qualification.stream_digest(src, qualification.MAX_ZIP)
        require(actual == plan['artifact_sha256'], 'Source ZIP changed during derivation')
        with output_path.open('rb') as derived:
            digest = hashlib.file_digest(derived, 'sha256').hexdigest()
        return {'status': 'prepared', 'source_archive_sha256': plan['artifact_sha256'],
                'derived_archive_sha256': digest, 'manifest_sha256': MANIFEST,
                'config_sha256': CONFIG, 'tag': tag, 'blob_count': len(source_blobs)}
    except BaseException:
        if created:
            output_path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.plan, args.archive, args.output, args.run_id), sort_keys=True))


if __name__ == '__main__':
    main()
