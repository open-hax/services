"""GPL-3.0-or-later. Experimental private Linux generation envelope OS edge.

Not installed; not a general filesystem promoter. Entry and tests are separate
from the existing DigitalOcean receiver. No shell, network or lifecycle API.
"""
import contextlib
import copy
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import time

try:
    from . import local_generation_edn as edn
    from . import local_generation_wire as wire
except ImportError:
    import local_generation_edn as edn
    import local_generation_wire as wire

HERE = Path(__file__).resolve().parent
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
CREATE = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
SCHEMAS = None


class Denial(Exception):
    def __init__(self, code='dirty', stage='admission', hold=None):
        super().__init__(code)
        self.code, self.stage = code, stage
        self.hold = hold


def law(operation, value=None):
    """Three fixed inert law operations; bounded response and hard runtime cap."""
    # Output is limited by the fixed program, not by caller-selected code.
    completed = subprocess.run(
        ['/usr/local/bin/bb', '-cp', str(HERE.parent / 'src'),
         str(HERE / 'local-generation-laws.clj'), operation],
        input=b'' if value is None else wire.encode(value), capture_output=True,
        timeout=10, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent'},
        cwd=HERE.parent, check=False)
    if completed.returncode or completed.stderr or len(completed.stdout) > 131072:
        raise Denial('unsupported', 'input')
    return wire.decode(completed.stdout, 131072)


def shape(name, value):
    """Native interpretation of the portable closed schemas, not a policy copy."""
    global SCHEMAS
    if SCHEMAS is None:
        SCHEMAS = {entry['name']: entry['shape'] for entry in law('schemas')}

    def valid(spec, v):
        if isinstance(spec, dict):
            return (type(v) is dict and set(v) == set(spec) and
                    all(valid(t, v[k]) for k, t in spec.items()))
        if isinstance(spec, list):
            if spec[0] == 'enum':
                return any(type(v) is type(x) and v == x for x in spec[1:])
            if spec[0] == 'or':
                return any(valid(t, v) for t in spec[1:])
            if spec[0] == 'vector':
                return type(v) is list and len(v) <= (spec[2] if len(spec) == 3 else 1100) and all(valid(spec[1], x) for x in v)
            return False
        if spec in SCHEMAS:
            return valid(SCHEMAS[spec], v)
        if spec == 'null':
            return v is None
        if spec == 'int':
            return type(v) is int and 0 <= v <= wire.MAX_INT
        if spec == 'string':
            return type(v) is str
        if spec == 'id':
            return type(v) is str and re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', v) is not None
        if spec in ('sha40', 'sha256'):
            return type(v) is str and re.fullmatch('[a-f0-9]{%d}' % (40 if spec == 'sha40' else 64), v) is not None
        if spec == 'u64':
            return wire.u64(v)
        if spec == 'ns64':
            return wire.ns64(v)
        if spec == 'component':
            return (type(v) is str and 1 <= len(v.encode()) <= 128 and v not in ('.', '..') and
                    not re.search(r'[/\\\x00-\x1f\x7f]', v))
        if spec == 'path':
            return type(v) is list and len(v) <= 16 and all(valid('component', x) for x in v)
        return False
    wire.canonical(value)
    if not valid(name, value):
        raise Denial('shape', 'input')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stable(identity):
    if identity is None:
        return None
    return {k: identity[k] for k in ('device', 'inode', 'mode', 'uid', 'gid')}


def rename_identity(identity):
    # Rename may change ctime, but no other identity fact. Complete accepted
    # metadata, including xattrs, is checked separately against the source.
    return None if identity is None else {k: v for k, v in identity.items() if k != 'ctime_ns'}


def failure(exc, stage='admission'):
    return {'code': (exc.code if isinstance(exc, Denial) else
                     getattr(exc, 'code', 'shape') if isinstance(exc, ValueError) else
                     'unsupported' if isinstance(exc, subprocess.SubprocessError) else 'io'),
            'stage': exc.stage if isinstance(exc, Denial) else stage,
            'detail': 'Local generation operation refused.'}


class Engine:
    """Trusted owner invocation, only provisioned private synthetic envelopes.

    Policy and revision arrive out-of-band from the owner, never from render
    output. Internal test hooks cannot be selected through the wire interface.
    """
    def __init__(self, policy, revision):
        self.policy = copy.deepcopy(policy)
        self.revision = revision
        self.chain = []
        self.fds = {}
        self._test_hook = None
        self.writes = 0
        self.journal = self.capsule = self.tx = None
        self.stage = 'input'
        self.baseline = None
        self.old_tree = self.target_tree = None
        self.current_expected = None
        self.current_raw = self.current_metadata = None
        self.created = {}
        self.journal_identity = None
        self.journal_temp_identity = None
        self.candidate_verified = None
        self.pending_plan = None
        self.known_generations = set()
        self.record_identities = {}
        self.setup_capsule_identity = None
        self.authoritative_current = None
        self.source_hashes = {p: sha(p.read_bytes()) for p in (
            HERE / 'local_generation_fs.py', HERE / 'local_generation_wire.py',
            HERE / 'local_generation_edn.py', HERE / 'local-generation-laws.clj',
            HERE.parent / 'src/services/local_generation.cljc')}
        shape('policy', policy)
        if (revision != policy['services']['revision'] or
                policy['services']['digest'] != sha(Path(__file__).read_bytes())):
            raise Denial('pin', 'input')
        if not policy['root']:
            raise Denial('ownership', 'input')
        limits = {'max_artifacts': 64, 'max_file_bytes': 1048576,
                  'max_total_bytes': 8388608, 'max_approval_ttl_ms': 900000}
        if any(not 0 < policy[k] <= bound for k, bound in limits.items()):
            raise Denial('limit', 'input')
        try:
            root = os.open('/', DIRECTORY)
            self.chain.append(('/', root, self._identity(root)))
            for component in policy['root']:
                parent = self.chain[-1][1]
                fd = os.open(component, DIRECTORY, dir_fd=parent)
                self.chain.append((component, fd, self._identity(fd)))
            self.root = self.chain[-1][1]
            self.device = os.fstat(self.root).st_dev
            self.mount = self._mount_id(self.root)
            filesystem = None
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                fields = line.split()
                if fields[0] == self.mount:
                    filesystem = fields[fields.index('-') + 1]
                    break
            if filesystem not in ('ext4', 'xfs', 'btrfs', 'tmpfs', 'overlay'):
                raise Denial('unsupported')
            if stat.S_IMODE(os.fstat(self.root).st_mode) != 0o700 or os.fstat(self.root).st_uid != os.getuid():
                raise Denial('ownership')
            parent_stat = os.fstat(self.chain[-2][1])
            if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) != 0o700:
                raise Denial('ownership')
            for name in ('generations', 'transactions'):
                self.fds[name] = os.open(name, DIRECTORY, dir_fd=self.root)
                self._private(self.fds[name], True)
                if os.fstat(self.fds[name]).st_dev != self.device or self._mount_id(self.fds[name]) != self.mount:
                    raise Denial('unsupported')
            self.fds['lock'] = os.open('lock', READ, dir_fd=self.root)
            self._private(self.fds['lock'])
            self.lock_identity = self._identity(self.fds['lock'])
            self.dir_identities = {k: self._identity(self.fds[k]) for k in ('generations', 'transactions')}
            self.marker = self._read(self.root, 'owner', 65536)
            if self.marker != wire.encode({'schema': 'services.local-owner/v1',
                    'owner': policy['owner'], 'policy_sha256': wire.digest(policy)}):
                raise Denial('ownership')
            self.marker_identity = self._named_identity(self.root, 'owner')
            self._chain_check()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        for fd in list(self.fds.values()) + [entry[1] for entry in self.chain]:
            with contextlib.suppress(OSError):
                os.close(fd)
        self.fds.clear()
        self.chain.clear()

    def _private(self, fd, directory=False):
        s = os.fstat(fd)
        if (s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != (0o700 if directory else 0o600)
                or (not directory and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1))):
            raise Denial('ownership')

    def _mount_id(self, fd):
        # Linux fixed proc metadata only; never resolve/reopen a target by a
        # proc symlink. Same-device bind mounts must not bypass mount admission.
        rows = Path('/proc/self/fdinfo/' + str(fd)).read_text().splitlines()
        values = [row.split(':', 1)[1].strip() for row in rows if row.startswith('mnt_id:')]
        if len(values) != 1 or not values[0].isdigit():
            raise Denial('unsupported')
        return values[0]

    def _bytes(self, fd, limit=wire.CAPSULE_LIMIT):
        os.lseek(fd, 0, os.SEEK_SET)
        chunks, total = [], 0
        while True:
            raw = os.read(fd, min(65536, limit + 1 - total))
            if not raw:
                return b''.join(chunks)
            chunks.append(raw)
            total += len(raw)
            if total > limit:
                raise Denial('limit')

    def _identity(self, fd):
        before = os.fstat(fd)
        if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
            raise Denial('unsupported')
        if stat.S_ISREG(before.st_mode) and before.st_nlink != 1:
            raise Denial('ownership')
        value = {'device': str(before.st_dev), 'inode': str(before.st_ino),
                 'mode': before.st_mode, 'uid': before.st_uid, 'gid': before.st_gid,
                 'size': before.st_size, 'mtime_ns': str(before.st_mtime_ns),
                 'ctime_ns': str(before.st_ctime_ns),
                 'sha256': sha(self._bytes(fd)) if stat.S_ISREG(before.st_mode) else None}
        if stat.S_ISREG(before.st_mode) and os.fstat(fd) != before:
            # atime can legitimately change on a read; compare relevant fields.
            after = os.fstat(fd)
            if any(getattr(before, k) != getattr(after, k) for k in
                   ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
                raise Denial('dirty')
        shape('identity', value)
        return value

    def _named_identity(self, parent, name):
        try:
            with self._open(parent, name) as fd:
                return self._identity(fd)
        except FileNotFoundError:
            return None

    @contextlib.contextmanager
    def _open(self, parent, name, directory=False):
        fd = os.open(name, DIRECTORY if directory else READ, dir_fd=parent)
        try:
            a, b = os.fstat(fd), os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (a.st_dev, a.st_ino, a.st_mode) != (b.st_dev, b.st_ino, b.st_mode):
                raise Denial('ancestor-drift')
            yield fd
        finally:
            os.close(fd)

    def _read(self, parent, name, limit=wire.WIRE_LIMIT):
        with self._open(parent, name) as fd:
            self._private(fd)
            return self._bytes(fd, limit)

    def _metadata(self, fd):
        s = os.fstat(fd)
        attrs = []
        total = 0
        for name in sorted(os.listxattr(fd), key=os.fsencode):
            raw = os.getxattr(fd, name)
            total += len(os.fsencode(name)) + len(raw)
            if len(raw) > 65536 or total > 1048576:
                raise Denial('limit')
            attrs.append({'name_b64': wire.b64(os.fsencode(name)), 'value_b64': wire.b64(raw)})
        value = {'mode': s.st_mode, 'uid': s.st_uid, 'gid': s.st_gid,
                 'mtime_ns': str(s.st_mtime_ns), 'xattrs': attrs}
        shape('metadata', value)
        return value

    def _chain_check(self):
        if any(sha(path.read_bytes()) != value for path, value in self.source_hashes.items()):
            raise Denial('pin')
        expected_anchor = self.policy['anchor']
        if expected_anchor['components'] != ['/'] + self.policy['root']:
            raise Denial('ancestor-drift')
        if len(expected_anchor['identities']) != len(self.chain):
            raise Denial('ancestor-drift')
        for index, (name, fd, identity) in enumerate(self.chain):
            observed = self._identity(fd)
            if stable(observed) != stable(identity) or stable(observed) != stable(expected_anchor['identities'][index]):
                raise Denial('ancestor-drift')
            parent = self.chain[index - 1][1] if index else fd
            named = os.stat(name if index else '.', dir_fd=parent, follow_symlinks=False)
            if (str(named.st_dev), str(named.st_ino), named.st_mode) != (
                    observed['device'], observed['inode'], observed['mode']):
                raise Denial('ancestor-drift')
        for name, identity in self.dir_identities.items():
            if stable(self._named_identity(self.root, name)) != stable(identity):
                raise Denial('ancestor-drift')
        if self._named_identity(self.root, 'lock') != self.lock_identity:
            raise Denial('lock')
        if (self._named_identity(self.root, 'owner') != self.marker_identity or
                self._read(self.root, 'owner', 65536) != self.marker):
            raise Denial('ownership')
        if set(os.listdir(self.root)) - {'owner', 'lock', 'current', 'generations', 'transactions'}:
            raise Denial('dirty')

    def _manifest(self, manifest):
        shape('manifest', manifest)
        if manifest['owner'] != self.policy['owner']:
            raise Denial('ownership')
        artifacts = manifest['artifacts']
        if not 0 < len(artifacts) <= self.policy['max_artifacts']:
            raise Denial('limit')
        paths, total = [], 0
        for artifact in artifacts:
            path = artifact['path']
            if not path or path[0] in ('owner', 'lock', 'current', 'generations', 'transactions'):
                raise Denial('shape')
            if any(path[:len(p)] == p or p[:len(path)] == path for p in paths):
                raise Denial('shape')
            paths.append(path)
            raw, descriptor = wire.unb64(artifact['bytes_b64']), wire.unb64(artifact['descriptor_b64'])
            total += len(raw) + len(descriptor)
            if max(len(raw), len(descriptor)) > self.policy['max_file_bytes'] or total > self.policy['max_total_bytes']:
                raise Denial('limit')
            if sha(raw) != artifact['sha256'] or sha(descriptor) != artifact['descriptor_sha256']:
                raise Denial('pin')
            bindings = [b for b in self.policy['formats'] if b['format'] == artifact['format']]
            if len(bindings) != 1:
                raise Denial('unsupported')
            binding = bindings[0]
            if (binding['renderer'] != artifact['renderer'] or binding['validator'] != artifact['validator']
                    or binding['renderer']['id'] == binding['validator']['id']):
                raise Denial('pin')
            if binding['entry'] != 'inert-edn-v1' or artifact['format'] != 'edn':
                raise Denial('unsupported')
            if binding['validator']['digest'] != sha((HERE / 'local_generation_edn.py').read_bytes()):
                raise Denial('pin')
            if not edn.validate(descriptor, raw):
                raise Denial('validator')

    def _selection(self):
        identity = self._named_identity(self.root, 'current')
        if identity is None:
            return None, None, None, None
        raw = self._read(self.root, 'current', 65536)
        value = wire.decode(raw, 65536)
        shape('selection', value)
        if value['owner'] != self.policy['owner']:
            raise Denial('ownership')
        with self._open(self.root, 'current') as fd:
            metadata = self._metadata(fd)
        return value, identity, raw, metadata

    def _tree(self, selection):
        if selection is None:
            return None
        entries, attr_bytes = [], 0
        with self._open(self.fds['generations'], selection['generation'], True) as tree:
            def walk(fd, path):
                nonlocal attr_bytes
                identity, metadata = self._identity(fd), self._metadata(fd)
                if any(identity[key] != metadata[key] for key in ('mode', 'uid', 'gid', 'mtime_ns')):
                    raise Denial('dirty')
                if (int(identity['device']) != self.device or self._mount_id(fd) != self.mount or identity['uid'] != os.getuid() or
                        identity['mode'] & 0o111 and stat.S_ISREG(identity['mode'])):
                    raise Denial('ownership')
                attr_bytes += sum(len(wire.unb64(a['name_b64'])) + len(wire.unb64(a['value_b64'])) for a in metadata['xattrs'])
                entries.append({'path': path, 'identity': identity, 'metadata': metadata})
                if len(entries) > 1100 or attr_bytes > 1048576:
                    raise Denial('limit')
                if stat.S_ISDIR(identity['mode']):
                    for name in sorted(os.listdir(fd)):
                        shape('artifact', {'path': [name], 'format': 'edn', 'bytes_b64': '', 'sha256': '0'*64,
                            'mode': 384, 'renderer': self.policy['services'], 'validator': self.policy['services'],
                            'descriptor_b64': '', 'descriptor_sha256': '0'*64})
                        s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        with self._open(fd, name, stat.S_ISDIR(s.st_mode)) as child:
                            walk(child, path + [name])
                # Retained tree directories DO require complete stable native
                # observations. Operational ancestors such as /tmp do not.
                if self._identity(fd) != identity:
                    raise Denial('dirty')
            walk(tree, [])
            manifest = wire.decode(self._read(tree, 'manifest'))
            self._manifest(manifest)
            if (manifest['generation'] != selection['generation'] or
                    wire.digest(manifest) != selection['manifest_sha256']):
                raise Denial('dirty')
            expected = {(), ('manifest',), ('payload',)}
            for artifact in manifest['artifacts']:
                path = ('payload', *artifact['path'])
                expected.update(path[:n] for n in range(1, len(path) + 1))
                entry = next((e for e in entries if tuple(e['path']) == path), None)
                if entry is None or entry['identity']['sha256'] != artifact['sha256'] or stat.S_IMODE(entry['identity']['mode']) != 0o600:
                    raise Denial('dirty')
            if {tuple(e['path']) for e in entries} != expected:
                raise Denial('dirty')
        entries.sort(key=lambda e: e['path'])
        projection = [{'path': e['path'], 'type': 'directory' if stat.S_ISDIR(e['identity']['mode']) else 'file',
                       'sha256': e['identity']['sha256'], 'metadata': e['metadata']} for e in entries]
        proof = {'selection': selection, 'root_identity': entries[0]['identity'],
                 'entries': entries, 'tree_sha256': wire.digest(projection)}
        shape('tree-proof', proof)
        return proof

    def _observe(self, manifest, operation):
        self._chain_check()
        selection, current, raw, metadata = self._selection()
        if self.authoritative_current is not None and self.authoritative_current != {'selection': selection, 'identity': current}:
            raise Denial('current-drift', self.stage)
        old_tree = self._tree(selection)
        destination = self._named_identity(self.fds['generations'], manifest['generation'])
        if operation == 'apply':
            if destination is not None:
                raise Denial('destination-drift')
            target = None
            dest = {'kind': 'new', 'identity': None}
        else:
            target_selection = {'schema': 'services.local-selection/v1', 'owner': manifest['owner'],
                                'generation': manifest['generation'], 'manifest_sha256': wire.digest(manifest)}
            if destination is None or selection == target_selection:
                raise Denial('destination-drift')
            target = self._tree(target_selection)
            dest = {'kind': 'retained', 'proof': target}
        anchor = {'components': ['/'] + self.policy['root'],
                  'identities': [self._identity(e[1]) for e in self.chain]}
        observation = {'anchor': anchor, 'envelope': self._identity(self.root),
                       'lock': self.lock_identity, 'current': current, 'selection': selection,
                       'owned_tree_sha256': wire.digest(None) if old_tree is None else old_tree['tree_sha256'],
                       'destination': dest}
        self.old_tree, self.target_tree = old_tree, target
        self.current_expected, self.current_raw, self.current_metadata = current, raw, metadata
        return observation

    def _compare_observation(self, expected, observed):
        a, b = copy.deepcopy(expected), copy.deepcopy(observed)
        for v in (a, b):
            v['anchor']['identities'] = [stable(i) for i in v['anchor']['identities']]
            v['envelope'] = stable(v['envelope'])
        if a != b:
            raise Denial('dirty')

    def _result(self, action, status, plan=None, plan_hash=None, before=None, after=None,
                first=None, recovery='not-needed', errors=None):
        result = {'schema': 'services.local-result/v1', 'action': action, 'status': status,
                  'plan': plan, 'plan_sha256': plan_hash, 'services_revision': self.revision,
                  'before': before, 'after': after, 'target_writes': self.writes,
                  'recovery': recovery, 'first_failure': first, 'recovery_failures': errors or [],
                  'journal_sha256': wire.digest(self.journal) if self.journal and action != 'preview' else None}
        shape('result', result)
        return result

    @contextlib.contextmanager
    def _locked(self, exclusive):
        fcntl.flock(self.fds['lock'], fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            self._chain_check()
            yield
        finally:
            fcntl.flock(self.fds['lock'], fcntl.LOCK_UN)

    def _seed(self, seed):
        shape('seed', seed)
        if (seed['owner'] != self.policy['owner'] or seed['root'] != self.policy['root'] or
                seed['services'] != self.policy['services'] or seed['policy_sha256'] != wire.digest(self.policy)):
            raise Denial('pin')
        now = int(time.time() * 1000)
        if not seed['created_at'] <= now < seed['expires_at']:
            raise Denial('expiry')
        self._manifest(seed['manifest'])

    def preview(self, seed):
        self.writes = 0
        self.stage = 'preview'
        observed = None
        try:
            self._seed(seed)
            with self._locked(False):
                self._records(False)
                observed = self._observe(seed['manifest'], seed['operation'])
                if (observed['selection'] or {}).get('generation') != seed['expected_generation']:
                    raise Denial('current-drift')
                plan = {k: copy.deepcopy(v) for k, v in seed.items() if k != 'expected_generation'}
                plan.update(schema='services.local-plan/v1', expected=observed,
                            prior_manifest_sha256=(observed['selection'] or {}).get('manifest_sha256'))
                shape('plan', plan)
                return self._result('preview', 'previewed', plan, wire.digest(plan),
                                    observed['selection'], observed['selection'])
        except (Denial, OSError, ValueError, subprocess.SubprocessError) as exc:
            status = ('held' if getattr(exc, 'hold', False) or getattr(exc, 'code', '') == 'recovery-uncertain' else
                      'unavailable' if getattr(exc, 'code', '') == 'unsupported' else 'denied')
            selection = observed['selection'] if observed is not None else None
            return self._result('preview', status, before=selection,
                                after=None if status == 'held' else selection, first=failure(exc, 'preview'),
                                recovery='uncertain' if status == 'held' else 'not-needed')

    def _hook(self, name):
        if self._test_hook is not None:
            self._test_hook(name)

    def _guard(self):
        self._chain_check()
        if self.journal is not None and int(time.time() * 1000) < self.journal['last_time']:
            raise Denial('expiry', self.stage)
        allowed_transactions = set(self.record_identities)
        if self.tx in self.fds:
            allowed_transactions.add(self.tx)
        if set(os.listdir(self.fds['transactions'])) != allowed_transactions:
            raise Denial('recovery-uncertain', self.stage)
        for tx, accepted in self.record_identities.items():
            if tx == self.tx:
                continue
            with self._open(self.fds['transactions'], tx, True) as fd:
                if (stable(self._identity(fd)) != accepted['directory'] or
                        self._named_identity(fd, 'recovery') != accepted['recovery'] or
                        self._named_identity(fd, 'journal') != accepted['journal']):
                    raise Denial('recovery-uncertain', self.stage)
        if self.tx in self.fds:
            if stable(self._named_identity(self.fds['transactions'], self.tx)) != stable(self._identity(self.fds[self.tx])):
                raise Denial('recovery-uncertain', self.stage)
            txfd = self.fds[self.tx]
            self._private(txfd, True)
            if self._mount_id(txfd) != self.mount or set(os.listdir(txfd)) - {'recovery', 'journal', 'journal-next', 'selection', 'restoration'}:
                raise Denial('recovery-uncertain', self.stage)
            if self._named_identity(txfd, 'recovery') != self.setup_capsule_identity:
                raise Denial('recovery-uncertain', self.stage)
            if self._named_identity(txfd, 'journal-next') != self.journal_temp_identity:
                raise Denial('recovery-uncertain', self.stage)
            if self._named_identity(txfd, 'journal') != self.journal_identity:
                raise Denial('recovery-uncertain', self.stage)
            for name in ('selection', 'restoration'):
                actual = self._named_identity(txfd, name)
                if actual is not None and actual != self.created.get(('transactions', self.tx, name)):
                    raise Denial('recovery-uncertain', self.stage)
        if self._named_identity(self.root, 'current') != self.current_expected:
            raise Denial('current-drift', self.stage)
        if self.current_expected is not None:
            with self._open(self.root, 'current') as fd:
                if self._metadata(fd) != self.current_metadata:
                    raise Denial('current-drift', self.stage)
        for proof in (self.old_tree, self.target_tree):
            if proof is not None and self._tree(proof['selection']) != proof:
                raise Denial('destination-drift', self.stage)
        if self.capsule is not None and self.tx in self.fds:
            if self._named_identity(self.fds[self.tx], 'recovery') != self.journal['capsule_identity']:
                raise Denial('recovery-uncertain', self.stage)
            if self.journal_identity is not None and self._named_identity(self.fds[self.tx], 'journal') != self.journal_identity:
                raise Denial('recovery-uncertain', self.stage)
        plan = self.capsule['plan'] if self.capsule is not None else self.pending_plan
        allowed_generations = set(self.known_generations)
        if plan is not None:
            allowed_generations.add(plan['manifest']['generation'])
        if set(os.listdir(self.fds['generations'])) - allowed_generations:
            raise Denial('dirty', self.stage)
        if plan is not None and plan['operation'] == 'apply':
            generation = plan['manifest']['generation']
            candidate = self._named_identity(self.fds['generations'], generation)
            expected = self.created.get(('generations', generation))
            if stable(candidate) != stable(expected):
                raise Denial('destination-drift', self.stage)
            if candidate is not None:
                def verify_created(parent, name, path):
                    identity = self._named_identity(parent, name)
                    accepted = self.created.get(tuple(path))
                    if accepted is None or (stable(identity) != stable(accepted) if stat.S_ISDIR(accepted['mode']) else identity != accepted):
                        raise Denial('destination-drift', self.stage)
                    if stat.S_ISDIR(accepted['mode']):
                        with self._open(parent, name, True) as fd:
                            children = {p[len(path)] for p in self.created
                                        if len(p) == len(path) + 1 and list(p[:len(path)]) == path}
                            if set(os.listdir(fd)) != children:
                                raise Denial('destination-drift', self.stage)
                            for child in sorted(children):
                                verify_created(fd, child, path + [child])
                verify_created(self.fds['generations'], generation, ['generations', generation])
            if self.candidate_verified is not None and candidate is not None:
                if self._tree(self.candidate_verified['selection']) != self.candidate_verified:
                    raise Denial('destination-drift', self.stage)

    def _native(self, name, fn, *, check=True):
        self._hook('before:' + name)
        if check:
            self._guard()
        self.writes += 1
        result = fn()
        # Hook is deliberately after the real syscall. Callers must resolve its
        # outcome independently if this point throws or the process is killed.
        self._hook('after:' + name)
        if check:
            self._guard()
        return result

    def _sync(self, fd, name, check=True, path=None):
        if self.capsule is not None and not name.startswith('journal'):
            if path is None:
                path = [] if fd == self.root else next(([key] for key, value in self.fds.items()
                    if value == fd and key in ('generations', 'transactions')), None)
            if path is None:
                raise Denial('unsupported', self.stage)
            def synced():
                os.fsync(fd)
                return self._identity(fd)
            self._recorded('fsync', path, name, synced, before=self._identity(fd))
        else:
            self._native(name, lambda: os.fsync(fd), check=check)

    def _write_all(self, fd, raw):
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            if count <= 0:
                raise OSError(errno.EIO, 'short write')
            offset += count

    def _new_file(self, parent, name, raw, label, check=True, path=None):
        holder = []
        recorded = self.capsule is not None and label != 'journal'
        def run(kind, suffix, fn, before=None):
            if recorded:
                if path is None:
                    raise Denial('unsupported', self.stage)
                return self._recorded(kind, path, label + suffix, fn, before=before)
            return self._native(label + suffix, fn, check=check)
        def opened():
            holder.append(os.open(name, CREATE, 0o600, dir_fd=parent))
            identity = self._identity(holder[0])
            if label == 'journal':
                self.journal_temp_identity = identity
            if label == 'capsule':
                self.setup_capsule_identity = identity
            if path is not None:
                self.created[tuple(path)] = identity
            return identity
        try:
            run('write', '-open', opened)
            fd = holder[0]
            self._private(fd)
            offset = 0
            while offset < len(raw):
                count = []
                def written():
                    count.append(os.pwrite(fd, raw[offset:], offset))
                    if count[0] <= 0:
                        raise OSError(errno.EIO, 'short write')
                    identity = self._identity(fd)
                    if label == 'journal':
                        self.journal_temp_identity = identity
                    if label == 'capsule':
                        self.setup_capsule_identity = identity
                    if path is not None:
                        self.created[tuple(path)] = identity
                    return identity
                run('write', '-write', written, self._identity(fd))
                offset += count[0]
            def synced():
                os.fsync(fd)
                return self._identity(fd)
            run('fsync', '-fsync', synced, self._identity(fd))
            return self._identity(fd)
        finally:
            for fd in holder:
                os.close(fd)

    def _save_journal(self):
        now = int(time.time() * 1000)
        if now < self.journal['last_time']:
            raise Denial('expiry', self.stage)
        self.journal['last_time'] = now
        shape('journal', self.journal)
        parent = self.fds[self.tx]
        name = 'journal-next'
        if self._named_identity(parent, name) is not None:
            raise Denial('recovery-uncertain', self.stage)
        expected = self._new_file(parent, name, wire.encode(self.journal, wire.CAPSULE_LIMIT), 'journal')
        with self._open(parent, name) as source:
            source_metadata = self._metadata(source)
        def renamed():
            os.rename(name, 'journal', src_dir_fd=parent, dst_dir_fd=parent)
            actual = self._named_identity(parent, 'journal')
            if rename_identity(actual) != rename_identity(expected):
                raise Denial('recovery-uncertain', self.stage)
            with self._open(parent, 'journal') as destination:
                if self._metadata(destination) != source_metadata:
                    raise Denial('recovery-uncertain', self.stage)
            self.journal_identity = actual
            self.journal_temp_identity = None
        self._native('journal-rename', renamed)
        self._sync(parent, 'journal-parent-fsync')

    def _recorded(self, kind, path, label, fn, before=None, anticipated=None):
        operation = {'index': len(self.journal['operations']), 'kind': kind, 'path': path,
                     'before': before, 'after': anticipated, 'state': 'pending'}
        self.journal['operations'].append(operation)
        self._save_journal()
        result = self._native(label, fn)
        operation['state'] = 'succeeded'
        if isinstance(result, dict):
            operation['after'] = result
        self._save_journal()
        return result

    def execute(self, request):
        self.writes = 0
        self.stage = 'admission'
        if self.tx in self.fds:
            os.close(self.fds.pop(self.tx))
        self.journal = self.capsule = None
        self.tx = None
        self.created = {}
        self.journal_identity = self.journal_temp_identity = None
        self.candidate_verified = None
        self.pending_plan = None
        self.setup_capsule_identity = None
        action, plan_hash, before = 'check', None, None
        try:
            shape('request', request)
            action, plan = request['action'], request['plan']
            plan_hash = wire.digest(plan)
            if request['plan_sha256'] != plan_hash:
                raise Denial('pin')
            seed = {k: v for k, v in plan.items() if k not in ('schema', 'expected', 'prior_manifest_sha256')}
            seed['expected_generation'] = (plan['expected']['selection'] or {}).get('generation')
            self._seed(seed)
            with self._locked(action != 'check'):
                before = self._selection()[0]
                consumed = self._records(False)
                if plan['nonce'] in consumed and action != 'check':
                    raise Denial('replay')
                observed = self._observe(plan['manifest'], plan['operation'])
                before = observed['selection']
                self._compare_observation(plan['expected'], observed)
                self.pending_plan = plan
                if plan['prior_manifest_sha256'] != (before or {}).get('manifest_sha256'):
                    raise Denial('dirty')
                if action == 'check':
                    if request['approval'] is not None:
                        raise Denial('approval')
                    return self._result(action, 'checked', plan_hash=plan_hash, before=before, after=before)
                if action != plan['operation']:
                    raise Denial('approval')
                approval = request['approval']
                if not law('approval', {'plan': plan, 'approval': approval, 'policy': self.policy,
                    'plan_hash': plan_hash, 'root_hash': wire.digest({'root': plan['root'], 'anchor': plan['expected']['anchor']}),
                    'policy_hash': wire.digest(self.policy), 'now': int(time.time() * 1000)}):
                    raise Denial('approval')
                try:
                    self._transaction(plan, approval)
                    after = self._selection()[0]
                    return self._result(action, 'applied' if action == 'apply' else 'rolled-back',
                                        plan_hash=plan_hash, before=before, after=after)
                except (Denial, OSError, ValueError, subprocess.SubprocessError) as exc:
                    if self.capsule is None:
                        raise
                    # Never drop the cooperative writer lease between a failed
                    # effect and its compensation.
                    first = failure(exc, self.stage)
                    try:
                        self._restore(first)
                        return self._result(action, 'failed-restored', plan_hash=plan_hash, before=before,
                                            after=before, first=first, recovery='restored')
                    except (Denial, OSError, ValueError, subprocess.SubprocessError) as recovery_exc:
                        recovery_failure = failure(recovery_exc, 'restoring')
                        self.journal['first_failure'] = self.journal['first_failure'] or first
                        self.journal['recovery_failures'].append(recovery_failure)
                        self.stage = self.journal['stage'] = 'held'
                        with contextlib.suppress(Denial, OSError, ValueError, subprocess.SubprocessError):
                            self._save_journal()
                        result = self._result(action, 'held', plan_hash=plan_hash, before=before,
                            first=first, recovery='uncertain', errors=[recovery_failure])
                        result['target_writes'] = None
                        return result
        except (Denial, OSError, ValueError, subprocess.SubprocessError) as exc:
            first = failure(exc, self.stage if self.stage != 'input' else 'admission')
            held = self.writes or getattr(exc, 'hold', False) or getattr(exc, 'code', '') == 'recovery-uncertain'
            status = 'held' if held else 'unavailable' if getattr(exc, 'code', '') == 'unsupported' else 'denied'
            return self._result(action, status,
                                plan_hash=plan_hash, before=before, after=None if held else before, first=first,
                                recovery='uncertain' if held else 'not-needed')

    def _transaction(self, plan, approval):
        self.stage = 'prepared'
        # Consume the complete nonce in the journal; never truncate caller
        # identity into an aliasing filesystem name.
        self.tx = 'tx-' + os.urandom(16).hex()
        candidate = {'schema': 'services.local-selection/v1', 'owner': plan['owner'],
                     'generation': plan['manifest']['generation'], 'manifest_sha256': wire.digest(plan['manifest'])}
        capsule = {'schema': 'services.local-recovery/v1', 'transaction': self.tx, 'plan': plan,
            'approval': approval, 'policy': self.policy, 'plan_sha256': wire.digest(plan),
            'approval_sha256': wire.digest(approval), 'policy_sha256': wire.digest(self.policy),
            'services': self.policy['services'], 'before': plan['expected']['selection'],
            'before_current': self.current_expected,
            'before_current_bytes_b64': wire.b64(self.current_raw) if self.current_raw is not None else None,
            'before_metadata': self.current_metadata, 'old_tree': self.old_tree, 'rollback_target': self.target_tree}
        shape('capsule', capsule)
        raw = wire.encode(capsule, wire.CAPSULE_LIMIT)
        def transaction_created():
            os.mkdir(self.tx, 0o700, dir_fd=self.fds['transactions'])
            self.fds[self.tx] = os.open(self.tx, DIRECTORY, dir_fd=self.fds['transactions'])
            self._private(self.fds[self.tx], True)
        self._native('transaction-mkdir', transaction_created)
        identity = self._new_file(self.fds[self.tx], 'recovery', raw, 'capsule')
        self._sync(self.fds[self.tx], 'capsule-parent-fsync')
        self._sync(self.fds['transactions'], 'transactions-fsync')
        self.journal = {'schema': 'services.local-journal/v1', 'transaction': self.tx,
            'plan_sha256': wire.digest(plan), 'approval_sha256': wire.digest(approval), 'nonce': plan['nonce'],
            'owner': plan['owner'], 'services': self.policy['services'], 'policy_sha256': wire.digest(self.policy),
            'capsule_sha256': wire.digest(capsule), 'capsule_identity': identity,
            'before': capsule['before'], 'before_current': capsule['before_current'],
            'before_current_bytes_b64': capsule['before_current_bytes_b64'], 'before_metadata': capsule['before_metadata'],
            'candidate': candidate, 'stage': 'prepared', 'operations': [], 'first_failure': None,
            'recovery_failures': [], 'last_time': int(time.time() * 1000)}
        self._save_journal()
        self.capsule = capsule
        self._hook('durable-intent')
        if plan['operation'] == 'apply':
            self._build(plan['manifest'])
        self.stage = self.journal['stage'] = 'verified'
        self._save_journal()
        if plan['operation'] == 'apply':
            self.candidate_verified = self._tree(candidate)
        else:
            if self._tree(candidate) != self.target_tree:
                raise Denial('destination-drift', 'verified')
        self.stage = self.journal['stage'] = 'selecting'
        self._save_journal()
        self._select(wire.encode(candidate), None, 'selection')
        self.stage = self.journal['stage'] = 'selected'
        self._save_journal()
        self._guard()
        if self._selection()[0] != candidate:
            raise Denial('current-drift', 'selected')
        self._tree(candidate)
        self.stage = self.journal['stage'] = 'committed'
        self._save_journal()
        self._guard()

    def _build(self, manifest):
        self.stage = self.journal['stage'] = 'building'
        self._save_journal()
        generation = manifest['generation']
        parent = self.fds['generations']
        def mkdir():
            os.mkdir(generation, 0o700, dir_fd=parent)
            identity = self._named_identity(parent, generation)
            self.created[('generations', generation)] = identity
            return identity
        self._recorded('mkdir', ['generations', generation], 'candidate-mkdir', mkdir)
        with self._open(parent, generation, True) as tree:
            self._new_file(tree, 'manifest', wire.encode(manifest), 'manifest-file',
                           path=['generations', generation, 'manifest'])
            self._recorded('mkdir', ['generations', generation, 'payload'], 'payload-mkdir',
                lambda: self._mkdir_identity(tree, 'payload', ['generations', generation, 'payload']))
            with self._open(tree, 'payload', True) as payload:
                made = set()
                for artifact in manifest['artifacts']:
                    fds = []
                    directory = payload
                    try:
                        for index, component in enumerate(artifact['path'][:-1]):
                            path = tuple(artifact['path'][:index + 1])
                            if path not in made:
                                self._recorded('mkdir', ['generations', generation, 'payload', *path], 'payload-dir-mkdir',
                                    lambda p=directory, n=component, a=path: self._mkdir_identity(p, n, ['generations', generation, 'payload', *a]))
                                made.add(path)
                            directory = os.open(component, DIRECTORY, dir_fd=directory)
                            fds.append(directory)
                        self._new_file(directory, artifact['path'][-1], wire.unb64(artifact['bytes_b64']), 'payload-file',
                                       path=['generations', generation, 'payload', *artifact['path']])
                        self._sync(directory, 'payload-directory-fsync',
                                   path=['generations', generation, 'payload', *artifact['path'][:-1]])
                    finally:
                        for fd in reversed(fds):
                            os.close(fd)
                self._sync(payload, 'payload-root-fsync', path=['generations', generation, 'payload'])
            # Every intermediate directory needs bottom-up durability too.
            def sync_directories(fd, path):
                for name in sorted(os.listdir(fd)):
                    s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(s.st_mode):
                        with self._open(fd, name, True) as child:
                            sync_directories(child, path + [name])
                self._sync(fd, 'candidate-directory-fsync', path=path)
            sync_directories(tree, ['generations', generation])
        self._sync(parent, 'generations-fsync')

    def _mkdir_identity(self, parent, name, path):
        os.mkdir(name, 0o700, dir_fd=parent)
        identity = self._named_identity(parent, name)
        self.created[tuple(path)] = identity
        return identity

    def _select(self, raw, metadata, label):
        txfd = self.fds[self.tx]
        identity = self._new_file(txfd, label, raw, label + '-file',
                                  path=['transactions', self.tx, label])
        if metadata is not None:
            with self._open(txfd, label) as fd:
                self._restore_metadata(fd, metadata, ['transactions', self.tx, label])
                self._sync(fd, label + '-metadata-fsync', path=['transactions', self.tx, label])
                identity = self._identity(fd)
        # The pending rename records the exact already-known source identity.
        # A fresh process can distinguish old current, our renamed inode, and
        # foreign current without adopting observations as new authority.
        with self._open(txfd, label) as source:
            source_metadata = self._metadata(source)
        def rename():
            os.rename(label, 'current', src_dir_fd=txfd, dst_dir_fd=self.root)
            actual = self._named_identity(self.root, 'current')
            if rename_identity(actual) != rename_identity(identity):
                raise Denial('current-drift', self.stage)
            with self._open(self.root, 'current') as fd:
                if self._metadata(fd) != source_metadata:
                    raise Denial('current-drift', self.stage)
            self.current_expected = actual
            self.current_raw = raw
            self.current_metadata = source_metadata
            return self.current_expected
        self._recorded('rename', ['current'], 'current-rename', rename,
                       before=self.current_expected, anticipated=identity)
        self._sync(self.root, 'selection-parent-fsync')

    def _restore_metadata(self, fd, metadata, path):
        s = os.fstat(fd)
        def mutate(label, fn):
            def effect():
                fn()
                value = self._identity(fd)
                self.created[tuple(path)] = value
                return value
            return self._recorded('restore-metadata', path, label, effect, before=self._identity(fd))
        if (s.st_uid, s.st_gid) != (metadata['uid'], metadata['gid']):
            mutate('restore-owner', lambda: os.fchown(fd, metadata['uid'], metadata['gid']))
        mutate('restore-mode', lambda: os.fchmod(fd, stat.S_IMODE(metadata['mode'])))
        attrs = {os.fsdecode(wire.unb64(x['name_b64'])): wire.unb64(x['value_b64']) for x in metadata['xattrs']}
        for name in os.listxattr(fd):
            if name not in attrs:
                mutate('restore-remove-xattr', lambda n=name: os.removexattr(fd, n))
        for name, value in attrs.items():
            mutate('restore-set-xattr', lambda n=name, v=value: os.setxattr(fd, n, v))
        mutate('restore-mtime', lambda: os.utime(fd, ns=(s.st_atime_ns, int(metadata['mtime_ns']))))
        if self._metadata(fd) != metadata:
            raise Denial('recovery-uncertain', 'restoring')
        return self._identity(fd)

    def _records(self, recover):
        try:
            return self._records_checked(recover)
        except Denial as exc:
            # An invalid *operational record* is not merely a bad new request.
            # Preserve its first cause while blocking every new admission.
            if exc.hold is None:
                exc.hold = True
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise Denial(failure(exc, self.stage)['code'], self.stage, hold=True) from exc

    def _records_checked(self, recover):
        """Incomplete setup or malformed/unknown owner state never disappears."""
        seen = set()
        self.known_generations = set()
        self.record_identities = {}
        self.authoritative_current = None
        pending = []
        terminal = []
        for tx in sorted(os.listdir(self.fds['transactions'])):
            if not re.fullmatch('tx-[a-z0-9-]{1,50}', tx):
                raise Denial('recovery-uncertain')
            with self._open(self.fds['transactions'], tx, True) as txfd:
                self._private(txfd, True)
                if set(os.listdir(txfd)) - {'recovery', 'journal', 'selection', 'restoration'}:
                    raise Denial('recovery-uncertain')
                try:
                    journal = wire.decode(self._read(txfd, 'journal', wire.CAPSULE_LIMIT), wire.CAPSULE_LIMIT)
                    capsule = wire.decode(self._read(txfd, 'recovery', wire.CAPSULE_LIMIT), wire.CAPSULE_LIMIT)
                except (OSError, ValueError) as exc:
                    raise Denial('recovery-uncertain') from exc
                shape('journal', journal)
                shape('capsule', capsule)
                if (journal['transaction'] != tx or capsule['transaction'] != tx or
                        capsule['policy'] != self.policy or capsule['services'] != self.policy['services'] or
                        journal['services'] != self.policy['services'] or
                        journal['capsule_identity'] != self._named_identity(txfd, 'recovery') or
                        journal['capsule_sha256'] != wire.digest(capsule) or
                        capsule['plan_sha256'] != wire.digest(capsule['plan']) or
                        capsule['approval_sha256'] != wire.digest(capsule['approval']) or
                        capsule['policy_sha256'] != wire.digest(self.policy) or
                        any(journal[k] != capsule[k] for k in ('plan_sha256', 'approval_sha256', 'policy_sha256',
                            'before', 'before_current', 'before_current_bytes_b64', 'before_metadata')) or
                        journal['nonce'] != capsule['plan']['nonce'] or journal['nonce'] in seen or
                        int(time.time() * 1000) < journal['last_time']):
                    raise Denial('recovery-uncertain')
                plan, approval = capsule['plan'], capsule['approval']
                expected_candidate = {'schema': 'services.local-selection/v1', 'owner': plan['owner'],
                    'generation': plan['manifest']['generation'], 'manifest_sha256': wire.digest(plan['manifest'])}
                if (journal['owner'] != self.policy['owner'] or journal['candidate'] != expected_candidate or
                        capsule['before'] != plan['expected']['selection'] or
                        capsule['before_current'] != plan['expected']['current'] or
                        capsule['rollback_target'] != (plan['expected']['destination'].get('proof') if plan['operation'] == 'rollback' else None) or
                        not law('approval', {'plan': plan, 'approval': approval, 'policy': self.policy,
                            'plan_hash': wire.digest(plan),
                            'root_hash': wire.digest({'root': plan['root'], 'anchor': plan['expected']['anchor']}),
                            'policy_hash': wire.digest(self.policy), 'now': approval['issued_at']})):
                    raise Denial('recovery-uncertain')
                if capsule['before'] is None:
                    if any(capsule[k] is not None for k in ('before_current', 'before_current_bytes_b64', 'before_metadata', 'old_tree')):
                        raise Denial('recovery-uncertain')
                    if plan['expected']['owned_tree_sha256'] != wire.digest(None):
                        raise Denial('recovery-uncertain')
                else:
                    if any(capsule[k] is None for k in ('before_current', 'before_current_bytes_b64', 'before_metadata', 'old_tree')):
                        raise Denial('recovery-uncertain')
                    raw = wire.unb64(capsule['before_current_bytes_b64'])
                    if (wire.decode(raw) != capsule['before'] or sha(raw) != capsule['before_current']['sha256'] or
                            capsule['old_tree']['selection'] != capsule['before'] or
                            capsule['old_tree']['tree_sha256'] != plan['expected']['owned_tree_sha256']):
                        raise Denial('recovery-uncertain')
                    for key in ('mode', 'uid', 'gid', 'mtime_ns'):
                        if capsule['before_metadata'][key] != capsule['before_current'][key]:
                            raise Denial('recovery-uncertain')
                if journal['stage'] == 'committed' and plan['operation'] == 'apply':
                    # Final file and bottom-up directory fsync outcomes preserve
                    # the accepted physical identity, including native ctime.
                    # A later preview must not adopt drift as a fresh baseline.
                    prefix = ['generations', plan['manifest']['generation']]
                    accepted = {tuple(op['path'][2:]): op['after'] for op in journal['operations']
                                if op['state'] == 'succeeded' and op['kind'] == 'fsync' and
                                op['path'][:2] == prefix and op['after'] is not None}
                    actual = self._tree(journal['candidate'])
                    if ({tuple(e['path']) for e in actual['entries']} != set(accepted) or
                            any(e['identity'] != accepted[tuple(e['path'])] for e in actual['entries'])):
                        raise Denial('dirty', self.stage, hold=False)
                seen.add(journal['nonce'])
                if journal['stage'] != 'restored':
                    self.known_generations.add(plan['manifest']['generation'])
                if capsule['before'] is not None:
                    self.known_generations.add(capsule['before']['generation'])
                self.record_identities[tx] = {'directory': stable(self._identity(txfd)),
                    'journal': self._named_identity(txfd, 'journal'),
                    'recovery': self._named_identity(txfd, 'recovery')}
                if journal['stage'] not in ('committed', 'restored'):
                    if not recover or journal['stage'] == 'held':
                        raise Denial('recovery-uncertain')
                    pending.append((tx, journal, capsule))
                else:
                    selected = journal['candidate'] if journal['stage'] == 'committed' else capsule['before']
                    operations = [op for op in journal['operations'] if op['state'] == 'succeeded' and
                                  op['path'] == ['current'] and op['kind'] in ('rename', 'unlink')]
                    if journal['stage'] == 'committed' and (not operations or operations[-1]['kind'] != 'rename'):
                        raise Denial('recovery-uncertain')
                    identity = operations[-1]['after'] if operations else capsule['before_current']
                    if (selected is None) != (identity is None):
                        raise Denial('recovery-uncertain')
                    if selected is not None:
                        raw = wire.encode(selected) if journal['stage'] == 'committed' else wire.unb64(capsule['before_current_bytes_b64'])
                        if identity['sha256'] != sha(raw):
                            raise Denial('recovery-uncertain')
                    terminal.append({'state': {'selection': selected, 'identity': identity},
                                     'committed': journal['stage'] == 'committed'})
        if set(os.listdir(self.fds['generations'])) - self.known_generations:
            raise Denial('dirty')
        if len(pending) > 1:
            raise Denial('recovery-uncertain')
        if not pending:
            selection, identity, _, _ = self._selection()
            observed = {'selection': selection, 'identity': identity}
            if not law('current', {'current': observed, 'terminal': terminal}):
                raise Denial('dirty', self.stage, hold=False)
            self.authoritative_current = observed
        for tx, journal, capsule in pending:
            self.tx, self.journal, self.capsule = tx, journal, capsule
            self.fds[tx] = os.open(tx, DIRECTORY, dir_fd=self.fds['transactions'])
            self.journal_identity = self.record_identities[tx]['journal']
            self.setup_capsule_identity = self.record_identities[tx]['recovery']
            self.created = {}
            for op in journal['operations']:
                if op['state'] == 'succeeded':
                    if op['kind'] in ('mkdir', 'write', 'restore-metadata') and op['after'] is not None:
                        self.created[tuple(op['path'])] = op['after']
                    if op['kind'] == 'unlink':
                        self.created.pop(tuple(op['path']), None)
            self._restore({'code': 'interrupted', 'stage': journal['stage'],
                           'detail': 'Interrupted local transaction.'})
            self.record_identities[tx]['journal'] = self.journal_identity
        return seen

    def _restore(self, first):
        capsule, journal = self.capsule, self.journal
        self.old_tree, self.target_tree = capsule['old_tree'], capsule['rollback_target']
        self._chain_check()
        if self._named_identity(self.fds[self.tx], 'recovery') != journal['capsule_identity']:
            raise Denial('recovery-uncertain', 'restoring')
        for proof in (self.old_tree, self.target_tree):
            if proof is not None and self._tree(proof['selection']) != proof:
                raise Denial('recovery-uncertain', 'restoring')
        current = self._named_identity(self.root, 'current')
        allowed = [capsule['before_current']]
        for op in journal['operations']:
            if op['kind'] == 'rename' and op['path'] == ['current'] and op['after'] is not None:
                # rename changes ctime on some filesystems; it never changes
                # byte hash/mtime/ownership/size or the already-recorded inode.
                allowed.append(op['after'])
        if not any(rename_identity(current) == rename_identity(i) for i in allowed):
            raise Denial('recovery-uncertain', 'restoring')
        self.current_expected = current
        if current is not None:
            with self._open(self.root, 'current') as fd:
                self.current_metadata = self._metadata(fd)
        journal['first_failure'] = journal['first_failure'] or first
        self.stage = journal['stage'] = 'restoring'
        # In-process cleanup can prove its own temporary inode. Fresh-process
        # re-entry cannot and holds on journal-next instead.
        temporary = self._named_identity(self.fds[self.tx], 'journal-next')
        if temporary is not None:
            if temporary != self.journal_temp_identity:
                raise Denial('recovery-uncertain', 'restoring')
            def remove_temporary():
                os.unlink('journal-next', dir_fd=self.fds[self.tx])
                self.journal_temp_identity = None
            self._native('journal-temp-cleanup', remove_temporary)
        self._save_journal()
        if current != capsule['before_current']:
            if capsule['before'] is None:
                self._recorded('unlink', ['current'], 'restore-absence',
                    lambda: self._unlink_current(), before=current)
                self._sync(self.root, 'restore-absence-fsync')
            else:
                self._select(wire.unb64(capsule['before_current_bytes_b64']), capsule['before_metadata'], 'restoration')
        actual, identity, raw, metadata = self._selection()
        if (actual != capsule['before'] or
                raw != (wire.unb64(capsule['before_current_bytes_b64']) if capsule['before_current_bytes_b64'] is not None else None) or
                metadata != capsule['before_metadata']):
            raise Denial('recovery-uncertain', 'restoring')
        self.current_expected = identity
        # Cleanup is intentionally not a general recursive remover. A candidate
        # is removable only if every node's recorded creation can be proved.
        if capsule['plan']['operation'] == 'apply':
            self._cleanup_candidate()
        self.stage = journal['stage'] = 'restored'
        self._save_journal()
        self._guard()

    def _unlink_current(self):
        os.unlink('current', dir_fd=self.root)
        self.current_expected = None
        self.current_metadata = self.current_raw = None

    def _cleanup_candidate(self):
        generation = self.capsule['plan']['manifest']['generation']
        if self._named_identity(self.fds['generations'], generation) is None:
            return
        created = {tuple(op['path']): op['after'] for op in self.journal['operations']
                   if op['kind'] in ('mkdir', 'write') and op['state'] == 'succeeded' and
                   op['path'][:2] == ['generations', generation] and op['after'] is not None}
        created.update({path: identity for path, identity in self.created.items()
                        if path[:2] == ('generations', generation)})
        if ('generations', generation) not in created:
            raise Denial('recovery-uncertain', 'restoring')
        def remove(parent, name, path):
            observed = self._named_identity(parent, name)
            expected = created.get(tuple(path))
            if expected is None or (stable(observed) != stable(expected) if stat.S_ISDIR(expected['mode']) else observed != expected):
                raise Denial('recovery-uncertain', 'restoring')
            if stat.S_ISDIR(expected['mode']):
                with self._open(parent, name, True) as fd:
                    for child in sorted(os.listdir(fd)):
                        remove(fd, child, path + [child])
                def removed():
                    os.rmdir(name, dir_fd=parent)
                    self.created.pop(tuple(path), None)
                self._recorded('unlink', path, 'cleanup-directory', removed, before=observed)
            else:
                def unlinked():
                    os.unlink(name, dir_fd=parent)
                    self.created.pop(tuple(path), None)
                self._recorded('unlink', path, 'cleanup-file', unlinked, before=observed)
        self.candidate_verified = None
        remove(self.fds['generations'], generation, ['generations', generation])
        self.created.pop(('generations', generation), None)
        self._sync(self.fds['generations'], 'cleanup-parent-fsync')

    def recover(self):
        self.writes = 0
        self.stage = 'restoring'
        if self.tx in self.fds:
            os.close(self.fds.pop(self.tx))
        self.tx = self.journal = self.capsule = None
        self.journal_identity = self.journal_temp_identity = self.setup_capsule_identity = None
        self.created = {}
        self.candidate_verified = self.pending_plan = None
        try:
            with self._locked(True):
                self._records(True)
            if self.journal and self.journal['stage'] == 'restored':
                return self._result(self.capsule['plan']['operation'], 'failed-restored',
                    plan_hash=self.journal['plan_sha256'], before=self.capsule['before'], after=self.capsule['before'],
                    first=self.journal['first_failure'], recovery='restored')
            return self._result('check', 'checked')
        except (Denial, OSError, ValueError, subprocess.SubprocessError) as exc:
            result = self._result('check', 'held', first=failure(exc, 'restoring'), recovery='uncertain')
            result['target_writes'] = None
            return result
