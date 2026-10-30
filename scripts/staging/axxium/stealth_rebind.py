#!/usr/bin/env python3
"""Prepare reviewable, per-run fixture copies without touching Docker.

This is a test-only source transformation. It does not authorize or execute a
fixture, does not change production source, and intentionally leaves all
existing isolation/owner/incident checks in place. Docker bind sources must
use the returned host-visible root, not a mount-namespace-only pathname.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat


RUN = re.compile(r'[0-9a-f]{32}\Z')
FILES = (
    'scripts/axxium_minimal_release.py',
    'scripts/axxium_incident_guard.py',
    'scripts/axxium_interrupted_handoff.py',
    'scripts/axxium_oidc_ingress.py',
    'scripts/provision_axxium_colab_identity.py',
    'scripts/staging/axxium/full_recovery_probe.py',
    'scripts/staging/axxium/network_fixture.py',
    'scripts/staging/axxium/identity_fixture.py',
    'scripts/staging/axxium/fixture_netns.py',
)
PLAN = 'digitalocean/services/axxium/minimal-production-plan.json'
OLD_NAMES = ('axxium-axxium-db-1', 'axxium-axxium-1', 'axxium-pds-1', 'caddy-caddy-1')
OLD_KNOXX_NAME = 'knoxx-knoxx-backend-1'
OLD_KNOXX_ID = '92346c9e74b47fc816eefaae4294b0e9ed1a20545bf3e9a3f40263561095d7b5'
# Reviewed Services head 8efa62aa556766e7470b4f0d06969a5ab386277f. A source
# change requires updating and reviewing these pins before preparing a fixture.
SOURCE_DIGESTS = dict(zip((*FILES, PLAN), (
    '4445289085458976c3ca847900a409371c678af64e7728293d91f79b36b81337',
    '7045abd30e568cec46238e47dfd8c69e4ec807a2bb54f1ce5f3d5fa0451bc8b3',
    '690b8280e5c03214cdd28a82ed3becb8eeaa50b485ba1e77b1ddd37de4840ca5',
    'de2ecf6e499f3e4437cb73331504dc91f688613843221a2cdc93f7e8b21baa40',
    '1a66a5558c43644138419930d83f195bf3df8a64cb65c5fcb05d266d0a66b561',
    'e9975c819a4dbb921cff7c35d5181ea962c2d883827ba9c12feaff1c8344ed9c',
    '4c855117e49ab690b45c0921477084f122defdb096a49e40a05c3278c6a9846a',
    'a98898eb04c78ad8c6c0e614e1c67be20cf3c0ea9cd72907d6cbfc775f968edf',
    '79e0e7e4848fd61ed13028f8a6e70103de4bace4ade0b588d62accac15431431',
    '6025ff0257a28711cc7847ca6935b97ffc3b11e4ccef76c3eb27a89f16bcafe1',
)))


class Refused(ValueError):
    pass


def checked_root(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise Refused('Run directory must be an absolute non-symlink path')
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o700 or any(path.iterdir())):
        raise Refused('Run directory must be newly created, private and empty')
    if path == Path('/srv/open-hax') or Path('/srv/open-hax') in path.parents:
        raise Refused('Production roots are not fixture destinations')
    return path


class Rebind(ast.NodeTransformer):
    """Map a closed set of Python literals, preserving control flow."""
    def __init__(self, run_id, host_root):
        if not RUN.fullmatch(run_id):
            raise Refused('A 32-character lowercase run ID is required')
        self.prefix = 'axxium-fixture-' + run_id
        self.run_id = run_id
        self.root = str(host_root)
        self.label = 'open-hax.fixture-' + run_id
        self.names = {
            'axxium-axxium-db-1': self.prefix + '-axxium-db-1',
            'axxium-axxium-1': self.prefix + '-axxium-1',
            'axxium-pds-1': self.prefix + '-pds',
            'caddy-caddy-1': self.prefix + '-caddy',
            'open-hax': self.prefix + '-ingress',
            'axxium_database': self.prefix + '-database',
            OLD_KNOXX_NAME: self.prefix + '-not-a-live-knoxx',
            OLD_KNOXX_ID: hashlib.sha256((run_id + ':unusable-knoxx').encode()).hexdigest(),
        }
        self.counts = {}

    def mark(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1

    def visit_Constant(self, node):
        raw = node.value
        if not isinstance(raw, (str, bytes)):
            return node
        is_bytes = isinstance(raw, bytes)
        try:
            value = raw.decode('utf-8') if is_bytes else raw
        except UnicodeDecodeError:
            return node
        original = value
        if '/srv/open-hax' in value:
            value = value.replace('/srv/open-hax', self.root)
            self.mark('root')
        if value in self.names:
            self.mark(value)
            value = self.names[value]
        if 'open-hax.axxium-operation' in value:
            value = value.replace('open-hax.axxium-operation', self.label)
            self.mark('operation-label')
        if value == 'codex.axxium-recovery-fixture=synthetic':
            value = 'codex.axxium-recovery-fixture=' + self.run_id
            self.mark('fixture-label')
        if value.startswith('axxium-verify-'):
            value = self.prefix + '-verify-' + value[len('axxium-verify-'):]
            self.mark('rehearsal-name')
        if value == '--project-name\x00axxium\x00':
            value = '--project-name\x00' + self.prefix + '\x00'
            self.mark('project-process-marker')
        if value != original:
            return ast.copy_location(ast.Constant(value.encode('utf-8') if is_bytes else value), node)
        return node

    def _sequence(self, node):
        self.generic_visit(node)
        for i, item in enumerate(node.elts[:-1]):
            following = node.elts[i + 1]
            if (isinstance(item, ast.Constant) and item.value == '--project-name' and
                    isinstance(following, ast.Constant) and following.value == 'axxium'):
                node.elts[i + 1] = ast.copy_location(ast.Constant(self.prefix), following)
                self.mark('compose-project')
        # The reviewed probe and crypto subprocesses use Docker argv lists.
        # Keep every synthetic container identifiable even if an invocation's
        # outcome is uncertain; cleanup belongs to a separate checked step.
        if (len(node.elts) >= 2 and isinstance(node.elts[0], ast.Constant) and
                node.elts[0].value == 'docker' and isinstance(node.elts[1], ast.Constant) and
                node.elts[1].value == 'run'):
            literal = lambda x: x.value if isinstance(x, ast.Constant) else None
            if '--rm' in [literal(x) for x in node.elts]:
                node.elts = [x for x in node.elts if literal(x) != '--rm']
                self.mark('retain-uncertain-container')
            owned = [ast.Constant('--label'), ast.Constant('codex.axxium-recovery-fixture=' + self.run_id)]
            if '--name' not in [literal(x) for x in node.elts]:
                random_name = ast.BinOp(left=ast.Constant(self.prefix + '-crypto-'), op=ast.Add(),
                    right=ast.Call(func=ast.Attribute(value=ast.Name(id='secrets', ctx=ast.Load()),
                                                       attr='token_hex', ctx=ast.Load()),
                                   args=[ast.Constant(12)], keywords=[]))
                owned += [ast.Constant('--name'), random_name]
                self.mark('named-crypto')
            node.elts[2:2] = owned
            self.mark('owned-container')
        return node

    visit_List = _sequence
    visit_Tuple = _sequence

    def visit_BinOp(self, node):
        # The original fixture selects the tag from a fixed repo + SOURCE.
        # The archive retagging helper admits exactly this per-run tag.
        if (isinstance(node.op, ast.Add) and isinstance(node.left, ast.Constant) and
                node.left.value == 'promethean-axxium:' and
                ((isinstance(node.right, ast.Name) and node.right.id == 'SOURCE') or
                 (isinstance(node.right, ast.Constant) and node.right.value == 'candidate'))):
            self.mark('candidate-image-reference')
            return ast.copy_location(ast.Constant(self.prefix + ':fixture-' + self.run_id), node)
        return self.generic_visit(node)

    def visit_Call(self, node):
        self.generic_visit(node)
        if (isinstance(node.func, ast.Name) and node.func.id == 'command' and
                len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) and
                node.args[0].value == 'docker' and isinstance(node.args[1], ast.Constant) and
                node.args[1].value == 'run'):
            for left, right in zip(node.args, node.args[1:]):
                if (isinstance(left, ast.Constant) and left.value == '--name' and
                        isinstance(right, ast.Name) and right.id == 'CADDY'):
                    node.args[2:2] = [ast.Constant('--tmpfs'), ast.Constant('/data:rw,nosuid,nodev,size=32m'),
                                      ast.Constant('--tmpfs'), ast.Constant('/config:rw,nosuid,nodev,size=16m')]
                    self.mark('caddy-tmpfs')
                    break
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        call = node.left
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and
                call.func.attr == 'get' and isinstance(call.func.value, ast.Attribute) and
                isinstance(call.func.value.value, ast.Name) and call.func.value.value.id == 'os' and
                call.func.value.attr == 'environ' and len(call.args) == 1 and
                isinstance(call.args[0], ast.Constant) and len(node.comparators) == 1 and
                isinstance(node.comparators[0], ast.Constant)):
            current = (call.args[0].value, node.comparators[0].value)
            replacements = {('GITHUB_ACTIONS', 'true'): ('AXXIUM_FIXTURE_MODE', 'local-isolated-v1'),
                            ('RUNNER_ENVIRONMENT', 'github-hosted'): ('AXXIUM_FIXTURE_HOST', 'stealth')}
            if current in replacements:
                key, expected = replacements[current]
                call.args[0] = ast.copy_location(ast.Constant(key), call.args[0])
                node.comparators[0] = ast.copy_location(ast.Constant(expected), node.comparators[0])
                self.mark('local-fixture-identity')
        return node

    def visit_Dict(self, node):
        self.generic_visit(node)
        for i, key in enumerate(node.keys):
            if (isinstance(key, ast.Constant) and key.value == 'codex.axxium-recovery-fixture' and
                    isinstance(node.values[i], ast.Constant) and node.values[i].value == 'synthetic'):
                node.values[i] = ast.copy_location(ast.Constant(self.run_id), node.values[i])
                self.mark('fixture-label')
        return node


def transform(source, run_id, host_root):
    tree = ast.parse(source)
    mapper = Rebind(run_id, host_root)
    tree = mapper.visit(tree)
    ast.fix_missing_locations(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            value = node.value.decode('utf-8', 'replace') if isinstance(node.value, bytes) else node.value
            if ('/srv/open-hax' in value or any(name in value for name in OLD_NAMES) or
                    OLD_KNOXX_NAME in value or OLD_KNOXX_ID in value or
                    value == 'open-hax' or 'axxium_database' in value or
                    'open-hax.axxium-operation' in value or value.startswith('axxium-verify-') or
                    value in ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'promethean-axxium:') or
                    value == '--project-name\x00axxium\x00'):
                raise Refused('An unbound production target remains')
        if isinstance(node, (ast.List, ast.Tuple)):
            for left, right in zip(node.elts, node.elts[1:]):
                if (isinstance(left, ast.Constant) and left.value == '--project-name' and
                        isinstance(right, ast.Constant) and right.value == 'axxium'):
                    raise Refused('An unbound Compose project remains')
    output = ast.unparse(tree) + '\n'
    compile(output, '<fixture-copy>', 'exec')
    return output, mapper.counts


def prepare(source_root, run_root, run_id):
    source_root = Path(source_root).resolve(strict=True)
    run_root = checked_root(run_root)
    if not RUN.fullmatch(run_id):
        raise Refused('A 32-character lowercase run ID is required')
    reviewed = {}
    for relative in (*FILES, PLAN):
        source = source_root / relative
        if source.is_symlink() or not source.is_file():
            raise Refused('A reviewed fixture source file is unavailable')
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SOURCE_DIGESTS[relative]:
            raise Refused('Fixture input differs from its reviewed source hash')
        reviewed[relative] = raw
    destination = run_root / 'source'
    host_root = run_root / 'open-hax'
    destination.mkdir(mode=0o700)
    manifest = {'schema': 'axxium-stealth-fixture-rebinding/v1',
                'run_id': run_id, 'host_root': str(host_root), 'files': {}}
    for relative in FILES:
        raw = reviewed[relative]
        transformed, counts = transform(raw.decode('utf-8'), run_id, host_root)
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_text(transformed, encoding='utf-8')
        target.chmod(0o600)
        manifest['files'][relative] = {'input_sha256': hashlib.sha256(raw).hexdigest(),
            'output_sha256': hashlib.sha256(transformed.encode()).hexdigest(), 'rebindings': counts}
    plan = reviewed[PLAN]
    target = destination / PLAN
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_bytes(plan)
    target.chmod(0o600)
    manifest['files'][PLAN] = {'input_sha256': hashlib.sha256(plan).hexdigest(),
                               'output_sha256': hashlib.sha256(plan).hexdigest()}
    manifest_path = run_root / 'rebind-manifest.json'
    fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', closefd=False) as stream:
            json.dump(manifest, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    return manifest
