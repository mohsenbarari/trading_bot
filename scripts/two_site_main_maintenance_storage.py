"""Actual owner-bound full-history transition storage; no startup/dispatch authority.

Caller continuously holds Supervisor custody. Existing Broker idle hold remains
continuous across actual checkpoint/owner/repository/evidence and private record
inspection. Publication is one-shot; any post-attempt uncertainty is ambiguous.
There is no legacy/quarantine fallback for an invalid main-mode record.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from scripts.two_site_cold_start_broker_quiescence import hold_idle_broker_client_lock_for_cold_adoption
from scripts.two_site_cold_start_adoption_storage import _load_and_audit_accepted_checkpoint, _open_validated_state_root
from scripts.two_site_maintenance_transition_storage import (
    _pin_validated_root, _check_stop_and_pending, _recheck_repository,
    _recheck_checkpoint, _type_sensitive_equal,
)
from scripts.two_site_maintenance_transition_dir import open_maintenance_transitions_dir, list_maintenance_transition_leaves
from scripts.two_site_maintenance_transition_file import (
    read_maintenance_transition_bytes, publish_maintenance_transition_bytes_noreplace,
    MaintenanceTransitionAmbiguousError,
)
from scripts.two_site_main_maintenance_record import (
    build_main_maintenance_record, parse_main_maintenance_record, serialize_main_maintenance_record,
)
from scripts.two_site_main_maintenance_evidence import verify_main_maintenance_evidence_locked
from scripts.two_site_protected_main_approval_reader import read_protected_main_approval_locked
from scripts.two_site_codex_autopilot import (
    validate_repository, validate_accepted_repository, _validate_persisted_repository, rescope_epoch_basename,
)

ERROR = "invalid main maintenance storage"
MAIN = "refs/heads/main"
STATE = Path(".local/two-site-refactor/autopilot")


class MainMaintenanceStorageError(ValueError):
    """Fixed rejection; no inferred approval or replay permission."""


def _owner(root, prior, current, checkpoint):
    return read_protected_main_approval_locked(
        root, prior_main_sha=prior["protected_refs"][MAIN],
        approved_main_sha=current["protected_refs"][MAIN],
        prior_checkpoint_sha256=checkpoint, prior_head=prior["head"])


def _path_identity(path, fd):
    before, pinned = path.lstat(), os.fstat(fd)
    if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
            or (before.st_dev, before.st_ino, before.st_mode, before.st_uid)
               != (pinned.st_dev, pinned.st_ino, pinned.st_mode, pinned.st_uid)):
        raise ValueError


@contextmanager
def _context(root, state_root):
    if not isinstance(root, Path) or not root.is_absolute() or state_root != root / STATE:
        raise ValueError
    with hold_idle_broker_client_lock_for_cold_adoption(root):
        root_fd = _pin_validated_root(root)
        try:
            state_fd = _open_validated_state_root(state_root)
            try:
                _check_stop_and_pending(root_fd, state_fd, state_root)
                current = validate_accepted_repository(validate_repository(root, require_clean=True))
                checkpoint_obj, checkpoint = _load_and_audit_accepted_checkpoint(state_fd, state_root)
                prior = _validate_persisted_repository(checkpoint_obj["repository"])
                if (current["head"] == prior["head"] or current["protected_refs"][MAIN] == prior["protected_refs"][MAIN]
                        or current["assignment_scope"]["assignment_id"] == prior["assignment_scope"]["assignment_id"]
                        or any(current["protected_refs"][ref] != digest for ref, digest in prior["protected_refs"].items() if ref != MAIN)):
                    raise ValueError
                bindings = {"prior_checkpoint_sha256":checkpoint, "prior_repository":prior,
                            "owner_approval":_owner(root, prior, current, checkpoint)}
                def recheck():
                    _path_identity(root, root_fd)
                    _path_identity(state_root, state_fd)
                    _check_stop_and_pending(root_fd, state_fd, state_root)
                    _recheck_repository(root, current)
                    _recheck_checkpoint(state_fd, state_root, checkpoint, prior)
                    if not _type_sensitive_equal(_owner(root, prior, current, checkpoint), bindings["owner_approval"]):
                        raise ValueError
                recheck()
                yield state_fd, current, bindings, recheck
                recheck()
            finally:
                os.close(state_fd)
        finally:
            os.close(root_fd)


def _leaf(current):
    return rescope_epoch_basename(current) + ".json"


def _read(root, fd, leaf, current, bindings):
    raw = read_maintenance_transition_bytes(fd, leaf, main_bindings=bindings)
    if raw is None:
        raise ValueError
    record = parse_main_maintenance_record(raw, **bindings)
    if not _type_sensitive_equal(record["new_repository"], current):
        raise ValueError
    verify_main_maintenance_evidence_locked(root, record, **bindings)
    return raw, record


def read_main_maintenance_transition_locked(root: Path, state_root: Path) -> dict:
    """Read exact current epoch's genuine record; missing/foreign/drift is never None.

    Upper routing may select this only for owner-approved main change; invalid
    main evidence must not fall through to legacy or quarantine acceptance.
    """
    try:
        with _context(root, state_root) as (state_fd, current, bindings, recheck):
            transitions_fd = open_maintenance_transitions_dir(state_fd, create=False)
            if transitions_fd is None:
                raise ValueError
            try:
                leaf = _leaf(current)
                _path_identity(state_root / "maintenance-transitions", transitions_fd)
                if list_maintenance_transition_leaves(transitions_fd) != [leaf]:
                    raise ValueError
                raw, record = _read(root, transitions_fd, leaf, current, bindings)
                recheck()
                if (list_maintenance_transition_leaves(transitions_fd) != [leaf]
                        or read_maintenance_transition_bytes(transitions_fd, leaf, main_bindings=bindings) != raw):
                    raise ValueError
                _path_identity(state_root / "maintenance-transitions", transitions_fd)
                result = copy.deepcopy(record)
            finally:
                os.close(transitions_fd)
        return result  # Includes successful context and Broker exits.
    except Exception:
        raise MainMaintenanceStorageError(ERROR) from None


def publish_main_maintenance_transition_locked(
    root: Path, state_root: Path, *, pre_main_commits: object, main_integration: object, post_main_commits: object,
) -> dict:
    """One-shot actual full-history publication, never owner approval or Git mutation.

    Caller provides actual independently accepted entries, not Source/hash claims.
    After publication attempt, any ordinary failure is ambiguous; no auto retry.
    BaseException propagates untouched and likewise never authorizes a retry.
    """
    attempted = False
    try:
        with _context(root, state_root) as (state_fd, current, bindings, recheck):
            record = build_main_maintenance_record(
                **bindings, new_repository=current, pre_main_commits=pre_main_commits,
                main_integration=main_integration, post_main_commits=post_main_commits)
            verify_main_maintenance_evidence_locked(root, record, **bindings)
            raw = serialize_main_maintenance_record(record, **bindings)
            recheck()
            transitions_fd = open_maintenance_transitions_dir(state_fd, create=True)
            if transitions_fd is None:
                raise ValueError
            try:
                _path_identity(state_root / "maintenance-transitions", transitions_fd)
                if list_maintenance_transition_leaves(transitions_fd):
                    raise ValueError
                leaf = _leaf(current)
                recheck()
                attempted = True
                publish_maintenance_transition_bytes_noreplace(transitions_fd, leaf, raw, main_bindings=bindings)
                if list_maintenance_transition_leaves(transitions_fd) != [leaf]:
                    raise ValueError
                actual_raw, result = _read(root, transitions_fd, leaf, current, bindings)
                if actual_raw != raw or not _type_sensitive_equal(result, record):
                    raise ValueError
                _path_identity(state_root / "maintenance-transitions", transitions_fd)
                recheck()
                result = copy.deepcopy(result)
            finally:
                os.close(transitions_fd)
        return result
    except Exception:
        if attempted:
            raise MaintenanceTransitionAmbiguousError("main transition publication is ambiguous") from None
        raise MainMaintenanceStorageError(ERROR) from None
