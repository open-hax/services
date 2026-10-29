#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Archive only the measured79903 Stage claim that created zero containers.

This one explicit operator decision does not retry a deployment. It retains
every selector/journal byte and private credential, then clears the hold last.
An existing archive, unknown state or uncertain recovery requires a new review.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat

from colab_transport_stage import (PROJECT, RUNTIME, STATE, OWNER_UID, controlled_directory,
                                    private_read, require, run, sync_directory, validate_client)

FAILED_CONTROLLER = '79903f7d01e734ac0eb678f99d546f178f2b609a'
SOURCE = '19e1adadb1b24eacca993de92f6370b400cf3d33'
IMAGE = 'ghcr.io/open-hax/cloud-cuckoo@sha256:8cd5bf61555d7c1096a053715ab39b78d028958ed62f0e20555e158096d7c767'
PLAN_SHA = '5c396ae3a3a82268347e34d4cf6effed2fb3850be3463cb632ee8c8666cadc86'
COMPOSE_SHA = 'adbaccba619de5f84bc912393bed7422bb600a5733a2c1f8c21e5a9e935018ec'


def present(path):
    return path.exists() or path.is_symlink()


def require_identity():
    require(os.getuid() == OWNER_UID and Path('/proc/self/loginuid').read_text().strip() == '1002',
            'Audited Codex transition to the existing deploy account is required')


def empty_runtime():
    require(not run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT],
                    timeout=20).strip(), 'Stage containers exist; empty-start recovery is forbidden')
    require(not run(['docker', 'ps', '-aq', '--filter', 'name=^/' + PROJECT + '-'], timeout=20).strip(),
            'Stage-named container exists; empty-start recovery is forbidden')
    for port in (18771, 18773, 18775, 18776):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    require(not present(RUNTIME / 'stage-receipt.json'), 'A healthy receipt forbids empty-start recovery')
    controlled_directory(STATE)
    require({p.name for p in STATE.iterdir()} == {'axxium-client.json', 'fixture-token', 'oauth'})
    validate_client(private_read(STATE / 'axxium-client.json'))
    require(len(private_read(STATE / 'fixture-token').strip()) >= 32)
    controlled_directory(STATE / 'oauth')
    require(not any((STATE / 'oauth').iterdir()), 'OAuth state exists; owned session review is required')


def archive_file(source, destination, expected):
    # Hard-link creation is exclusive and retains exact bytes. Persist the
    # archive before removing its original name; a crash leaves a refusal.
    require(not present(destination) and private_read(source) == expected)
    os.link(source, destination, follow_symlinks=False)
    sync_directory(RUNTIME)
    require(source.read_bytes() == destination.read_bytes() == expected)
    source.unlink()
    sync_directory(RUNTIME)


def recover():
    require_identity()
    controlled_directory(RUNTIME)
    require(not any(p.is_symlink() for p in (RUNTIME, STATE, *RUNTIME.parents, *STATE.parents)))
    fd = os.open(RUNTIME / '.lock', os.O_WRONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'w') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER_UID and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal, selector = RUNTIME / '.pending', RUNTIME / 'environment'
        journal_raw, selector_raw = private_read(journal), private_read(selector)
        require(json.loads(journal_raw) == {'schema': 'colab-stage-pending/v1', 'controller_sha': FAILED_CONTROLLER,
                'source_sha': SOURCE, 'image_reference': IMAGE} and
                selector_raw == ('COLAB_MCP_IMAGE=' + IMAGE + '\n').encode())
        controller = RUNTIME / 'controllers' / FAILED_CONTROLLER / 'digitalocean/services/colab-mcp'
        require(hashlib.sha256(private_read(controller / 'transport-stage-plan.json')).hexdigest() == PLAN_SHA and
                hashlib.sha256(private_read(controller / 'compose.transport-stage.yaml')).hexdigest() == COMPOSE_SHA)
        archived_selector = RUNTIME / ('failed-empty-start-' + FAILED_CONTROLLER + '.environment')
        archived_journal = RUNTIME / ('failed-empty-start-' + FAILED_CONTROLLER + '.pending.json')
        require(not present(archived_selector) and not present(archived_journal))
        empty_runtime()
        client, token = private_read(STATE / 'axxium-client.json'), private_read(STATE / 'fixture-token')
        archive_file(selector, archived_selector, selector_raw)
        # The pending hold stays until the last operation. Recheck both the
        # runtime and private inputs; never remove/replace a credential.
        empty_runtime()
        require(private_read(STATE / 'axxium-client.json') == client and private_read(STATE / 'fixture-token') == token)
        archive_file(journal, archived_journal, journal_raw)
        require(private_read(archived_journal) == journal_raw and private_read(archived_selector) == selector_raw)
        return {'schema': 'colab-stage-empty-start-recovery/v1', 'recovered': True,
                'failed_controller': FAILED_CONTROLLER, 'runtime_was_empty': True,
                'selector_archived_first': True, 'pending_archived_last': True,
                'private_credentials_preserved': True, 'deployment_retried': False,
                'production_changed': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--recover-empty-start', action='store_true', required=True)
    parser.parse_args()
    try:
        print(json.dumps(recover(), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'recovered': False, 'reason': 'empty-start recovery refused; preserve hold for review'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
