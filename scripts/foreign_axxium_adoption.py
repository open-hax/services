"""Candidate transition law for one-time adoption of a foreign Axxium runtime.

This is NOT a host driver or receiver entry point. No production adapter,
retirement verifier, durable journal implementation or trusted lock is
provided here. Until separately reviewed implementations exist this module
cannot activate a deployment or establish any production evidence.
"""
import hashlib
import json
import re
from types import MappingProxyType


class Refused(ValueError):
    """Preconditions or an uncertain outcome require operator review."""


class KnownFailure(Exception):
    """Adapter proved an operation failed with a known, stable outcome."""


SHA = re.compile(r'[0-9a-f]{64}\Z')
GIT = re.compile(r'[0-9a-f]{40}\Z')
IMAGE = re.compile(r'sha256:[0-9a-f]{64}\Z')


def _hash(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def _closed(value, fields):
    return isinstance(value, dict) and set(value) == set(fields)


def validate_plan(raw):
    """Admit one canonical, closed JSON plan with explicitly unknown old source."""
    try:
        if not isinstance(raw, bytes) or len(raw) > 65536:
            raise ValueError()
        plan = json.loads(raw, object_pairs_hook=_unique)
        if raw != (json.dumps(plan, sort_keys=True, separators=(',', ':')) + '\n').encode():
            raise ValueError()
        if not _closed(plan, ('schema', 'host', 'incumbent', 'candidate', 'retirement_sha256',
                              'rollback_compatibility_sha256', 'backup_policy_sha256')):
            raise ValueError()
        old, new = plan['incumbent'], plan['candidate']
        if (plan['schema'] != 'axxium-foreign-adoption/v1' or not isinstance(plan['host'], str)
                or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}', plan['host'])
                or not all(_hash(plan[k]) for k in ('retirement_sha256',
                    'rollback_compatibility_sha256', 'backup_policy_sha256'))
                or not _closed(old, ('container_id', 'image_id', 'source_sha', 'runtime_sha256',
                                     'database_sha256', 'environment_sha256'))
                or not _hash(old['container_id']) or not isinstance(old['image_id'], str)
                or not IMAGE.fullmatch(old['image_id']) or old['source_sha'] is not None
                or not all(_hash(old[k]) for k in ('runtime_sha256', 'database_sha256', 'environment_sha256'))
                or not _closed(new, ('source_sha', 'image_id', 'archive_sha256', 'controller_sha'))
                or not isinstance(new['source_sha'], str) or not GIT.fullmatch(new['source_sha'])
                or not isinstance(new['controller_sha'], str) or not GIT.fullmatch(new['controller_sha'])
                or not isinstance(new['image_id'], str) or not IMAGE.fullmatch(new['image_id'])
                or new['image_id'] == old['image_id'] or not _hash(new['archive_sha256'])):
            raise ValueError()
        return plan, hashlib.sha256(raw).hexdigest()
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise Refused('Invalid or noncanonical adoption plan') from None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def execute(raw, backend, journal):
    """Model one non-replayable transaction under an adapter-provided trusted lock.

    Both injected interfaces are trusted and MUST be root-controlled. The backend
    must verify native provenance, actual resource state and writer exclusion;
    truthy caller-provided strings are never evidence. Journal create/record must
    be durable, serialize with all receiver and ingress writers and refuse a
    second transaction after success, failure, or crash. A production adapter
    must implement those contracts before this transition is installable.

    Backups are independently verified. No database restore is ever automatic.
    An ambiguous command/persistence outcome stops immediately; only a proven
    KnownFailure after candidate start enters the fenced rollback path.
    """
    plan, digest = validate_plan(raw)
    # A backend cannot mutate the admitted content after its digest is checked.
    plan = _freeze(plan)
    # The native lock must cover receiver and ingress writers as well as this
    # process. An in-memory or per-process lock is insufficient on a host.
    with journal.lock():
        return _execute_locked(raw, plan, digest, backend, journal)


def _execute_locked(raw, plan, digest, backend, journal):
    # Claim before *any* observation or effect: an interrupted transaction is
    # never replayed, and an existing journal cannot be overwritten.
    if journal.claim(digest, raw) is not True:
        raise Refused('Adoption already attempted or journal unavailable')

    def step(name, action):
        journal.record(digest, name, 'pending')
        try:
            result = action()
            if result is not True:
                raise Refused(name + ' failed closed')
        except KnownFailure:
            journal.record(digest, name, 'known_failure')
            raise
        except Exception:
            # Persistence or command uncertainty cannot authorize further work.
            try:
                journal.record(digest, name, 'uncertain')
            except Exception:
                pass
            raise Refused(name + ' outcome uncertain; operator review required') from None
        journal.record(digest, name, 'succeeded')

    def recheck(phase):
        step('verify_' + phase, lambda: backend.verify(plan, digest, phase))

    def snapshot(label):
        # The adapter enforces a write fence and verifies backup content and
        # independent retention; these method results are not bare attestations.
        recheck(label + '_fence')
        step('backup_' + label, lambda: backend.backup(plan, digest, label))
        step('verify_backup_' + label, lambda: backend.verify_backup(plan, digest, label))

    recheck('initial')
    snapshot('before')
    recheck('pre_stop')
    step('stop_incumbent', lambda: backend.stop_incumbent(plan, digest))
    try:
        recheck('pre_start')
        step('start_candidate', lambda: backend.start_candidate(plan, digest))
        step('candidate_health', lambda: backend.verify_candidate_health(plan, digest))
    except KnownFailure:
        # Candidate may have migrated or written. Fence all writers and capture
        # a verified post-failure backup before considering image-only rollback.
        snapshot('failed')
        step('rollback_compatibility', lambda: backend.verify_rollback(plan, digest))
        recheck('pre_rollback')
        step('rollback_incumbent', lambda: backend.rollback_incumbent(plan, digest))
        step('incumbent_health', lambda: backend.verify_incumbent_health(plan, digest))
        journal.record(digest, 'transaction', 'rolled_back')
        return 'rolled_back'
    snapshot('after')
    recheck('pre_commit')
    step('candidate_health_final', lambda: backend.verify_candidate_health(plan, digest))
    # One durable adapter transaction must publish matching release and healthy
    # receiver receipt; a timeout/crash holds the journal for operator review.
    step('commit_healthy_selection', lambda: backend.commit_healthy_selection(plan, digest))
    journal.record(digest, 'transaction', 'healthy')
    return 'healthy'
