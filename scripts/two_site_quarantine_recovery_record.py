"""Dormant pure quarantined-cycle recovery record codec under AE03.

Provides pure functional building, validation, serialization, and parsing of
version 1.0 quarantined-cycle recovery records bound to prior checkpoint/maintenance
identities, failed cycle evidence, repair commit, canonical repository facts,
and a declared contiguous follow-up commit chain.

Boundary and authority constraints:
1. Pure dormant codec only: contains NO storage writer, startup hook, Provider,
   Broker, monitor, wake, replay, Git, network, filesystem, or process work.
2. Does NOT authorize startup or acceptance: live Git verification and lock
   checks remain separate downstream storage/reader obligations.
3. Fixed error strings: error text is constant and does not echo untrusted payloads.
"""

from __future__ import annotations

import copy
import json
import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

try:
    from scripts.two_site_maintenance_transition_record import (
        MaintenanceTransitionRecordError,
        _validate_new_repository as _validate_repo_facts,
    )
except ImportError:
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        MaintenanceTransitionRecordError,
        _validate_new_repository as _validate_repo_facts,
    )

try:
    from scripts.two_site_proposal_runner import unique_json
except ImportError:
    try:
        from two_site_proposal_runner import unique_json  # type: ignore[no-redef]
    except ImportError:
        def unique_json(text: str) -> Any:
            def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                d: dict[str, Any] = {}
                for k, v in pairs:
                    if k in d:
                        raise ValueError(f"Duplicate key: {k}")
                    d[k] = v
                return d
            return json.loads(text, object_pairs_hook=_pairs)

MAX_QUARANTINE_RECOVERY_RECORD_BYTES: int = 16384
QUARANTINE_RECOVERY_SCHEMA_VERSION: str = "1.0"
QUARANTINE_RECOVERY_KIND: str = "QUARANTINED_CYCLE_RECOVERY"
MIN_FOLLOWUP_COMMITS: int = 1
MAX_FOLLOWUP_COMMITS: int = 8
MAX_COMMIT_PATHS: int = 256
MAX_PATH_LEN: int = 1024

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_PATH_RE = re.compile(
    r"^(?:scripts/two_site_[A-Za-z0-9_]+\.py|tests/test_two_site_[A-Za-z0-9_]+\.py)$"
)

_RECORD_KEYS = frozenset({
    "failed_commit_head",
    "failed_result_sha256",
    "failed_run_id",
    "final_validation_sha256",
    "followup_commits",
    "kind",
    "new_repository",
    "prior_checkpoint_sha256",
    "prior_maintenance_transition_sha256",
    "repair_commit_head",
    "schema_version",
})

_COMMIT_KEYS = frozenset({
    "head",
    "parent",
    "paths",
    "proposal_sha256",
    "reviewer_run_id",
    "test_evidence_sha256",
    "worker_run_id",
})


class QuarantineRecoveryRecordError(ValueError):
    """Raised when quarantine recovery record validation, serialization, or parsing fails."""


__all__ = [
    "MAX_FOLLOWUP_COMMITS",
    "MAX_QUARANTINE_RECOVERY_RECORD_BYTES",
    "MIN_FOLLOWUP_COMMITS",
    "QUARANTINE_RECOVERY_KIND",
    "QUARANTINE_RECOVERY_SCHEMA_VERSION",
    "QuarantineRecoveryRecordError",
    "build_quarantine_recovery_record",
    "parse_quarantine_recovery_record",
    "serialize_quarantine_recovery_record",
    "validate_quarantine_recovery_record",
]


def _bounded_canonical_bytes(
    obj: Any, max_bytes: int = MAX_QUARANTINE_RECOVERY_RECORD_BYTES
) -> bytes:
    try:
        raw_text = (
            json.dumps(
                obj,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        encoded = raw_text.encode("utf-8")
        if len(encoded) > max_bytes:
            raise QuarantineRecoveryRecordError("Canonical JSON size exceeds limit")
        return encoded
    except QuarantineRecoveryRecordError:
        raise
    except (TypeError, ValueError) as exc:
        raise QuarantineRecoveryRecordError(
            "Record cannot be serialized to canonical JSON"
        ) from exc


def _validate_run_id(val: Any, role: str) -> str:
    if type(val) is not str:
        raise QuarantineRecoveryRecordError(f"{role} must be a string")
    if not _RUN_ID_RE.fullmatch(val):
        raise QuarantineRecoveryRecordError(f"{role} format invalid")
    return val


def _validate_commit_path(p: Any) -> str:
    if type(p) is not str:
        raise QuarantineRecoveryRecordError("commit path must be a string")
    if not (1 <= len(p) <= MAX_PATH_LEN):
        raise QuarantineRecoveryRecordError("commit path length out of bounds")
    if p != p.strip():
        raise QuarantineRecoveryRecordError("commit path must not have leading or trailing whitespace")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise QuarantineRecoveryRecordError("commit path must not contain control characters or NUL")
    if "\\" in p:
        raise QuarantineRecoveryRecordError("commit path must not contain backslashes")
    if p.startswith("/"):
        raise QuarantineRecoveryRecordError("commit path must be a relative path")
    if any(c in "*?[]{}" for c in p):
        raise QuarantineRecoveryRecordError("commit path must not contain glob characters")
    if re.match(r"^[a-zA-Z]:", p) or ":" in p:
        raise QuarantineRecoveryRecordError("commit path must not contain Windows drive prefix or colon")
    segments = p.split("/")
    if any(seg in (".", "..", "") for seg in segments):
        raise QuarantineRecoveryRecordError("commit path must not contain dot segments or empty components")
    if posixpath.normpath(p) != p or PurePosixPath(p).as_posix() != p:
        raise QuarantineRecoveryRecordError("commit path must be a canonical normalized POSIX path")
    if not _COMMIT_PATH_RE.fullmatch(p):
        raise QuarantineRecoveryRecordError("commit path does not match allowed pattern")
    return p


def _validate_commit_paths(paths: Any) -> list[str]:
    if type(paths) is not list:
        raise QuarantineRecoveryRecordError("commit paths must be a list")
    if not (1 <= len(paths) <= MAX_COMMIT_PATHS):
        raise QuarantineRecoveryRecordError("commit paths count out of bounds")
    validated_paths: list[str] = []
    seen: set[str] = set()
    for p in paths:
        norm_p = _validate_commit_path(p)
        if norm_p in seen:
            raise QuarantineRecoveryRecordError("duplicate commit path detected")
        seen.add(norm_p)
        validated_paths.append(norm_p)
    if validated_paths != sorted(validated_paths):
        raise QuarantineRecoveryRecordError("commit paths must be sorted")
    return validated_paths


def _validate_new_repository(new_repository: Any) -> dict[str, Any]:
    try:
        return _validate_repo_facts(new_repository)
    except MaintenanceTransitionRecordError as exc:
        raise QuarantineRecoveryRecordError(str(exc)) from exc


def _validate_followup_commits(
    commits: Any,
    repair_commit_head: str,
    failed_commit_head: str,
    new_repo_head: str,
) -> list[dict[str, Any]]:
    if type(commits) is not list:
        raise QuarantineRecoveryRecordError("followup_commits must be a list")
    if not (MIN_FOLLOWUP_COMMITS <= len(commits) <= MAX_FOLLOWUP_COMMITS):
        raise QuarantineRecoveryRecordError("followup_commits count out of bounds")

    validated_commits: list[dict[str, Any]] = []
    seen_heads: set[str] = set()

    for idx, raw_commit in enumerate(commits):
        if type(raw_commit) is not dict:
            raise QuarantineRecoveryRecordError("followup commit entry must be a dict")
        if set(raw_commit.keys()) != _COMMIT_KEYS or len(raw_commit) != len(_COMMIT_KEYS):
            raise QuarantineRecoveryRecordError("followup commit keys mismatch")

        head = raw_commit.get("head")
        if type(head) is not str or not _HEX_40_RE.fullmatch(head):
            raise QuarantineRecoveryRecordError("commit head must be 40 lowercase hex characters")

        parent = raw_commit.get("parent")
        if type(parent) is not str or not _HEX_40_RE.fullmatch(parent):
            raise QuarantineRecoveryRecordError("commit parent must be 40 lowercase hex characters")

        if head in seen_heads:
            raise QuarantineRecoveryRecordError("commit head is not distinct")
        seen_heads.add(head)

        if head == parent:
            raise QuarantineRecoveryRecordError("commit head cannot equal parent")

        if head == failed_commit_head:
            raise QuarantineRecoveryRecordError("commit head cannot equal failed_commit_head")

        if head == repair_commit_head:
            raise QuarantineRecoveryRecordError("commit head cannot equal repair_commit_head")

        prop_sha = raw_commit.get("proposal_sha256")
        if type(prop_sha) is not str or not _HEX_64_RE.fullmatch(prop_sha):
            raise QuarantineRecoveryRecordError("commit proposal_sha256 must be 64 lowercase hex characters")

        ev_sha = raw_commit.get("test_evidence_sha256")
        if type(ev_sha) is not str or not _HEX_64_RE.fullmatch(ev_sha):
            raise QuarantineRecoveryRecordError("commit test_evidence_sha256 must be 64 lowercase hex characters")

        w_run_id = _validate_run_id(raw_commit.get("worker_run_id"), "worker_run_id")
        r_run_id = _validate_run_id(raw_commit.get("reviewer_run_id"), "reviewer_run_id")
        if w_run_id == r_run_id:
            raise QuarantineRecoveryRecordError("commit worker_run_id and reviewer_run_id must be distinct")

        val_paths = _validate_commit_paths(raw_commit.get("paths"))

        if idx == 0:
            if parent != repair_commit_head:
                raise QuarantineRecoveryRecordError("first followup commit parent must match repair_commit_head")
        else:
            prev_head = validated_commits[idx - 1]["head"]
            if parent != prev_head:
                raise QuarantineRecoveryRecordError("commit parent does not match previous commit head")

        validated_commits.append({
            "head": head,
            "parent": parent,
            "paths": val_paths,
            "proposal_sha256": prop_sha,
            "reviewer_run_id": r_run_id,
            "test_evidence_sha256": ev_sha,
            "worker_run_id": w_run_id,
        })

    final_head = validated_commits[-1]["head"]
    if final_head != new_repo_head:
        raise QuarantineRecoveryRecordError("final commit head must match new_repository head")

    return validated_commits


def build_quarantine_recovery_record(
    prior_checkpoint_sha256: str,
    prior_maintenance_transition_sha256: str,
    failed_run_id: str,
    failed_result_sha256: str,
    failed_commit_head: str,
    repair_commit_head: str,
    new_repository: dict[str, Any],
    followup_commits: list[dict[str, Any]],
    final_validation_sha256: str,
) -> dict[str, Any]:
    """Build and validate a version 1.0 quarantined-cycle recovery record."""
    record: dict[str, Any] = {
        "failed_commit_head": failed_commit_head,
        "failed_result_sha256": failed_result_sha256,
        "failed_run_id": failed_run_id,
        "final_validation_sha256": final_validation_sha256,
        "followup_commits": followup_commits,
        "kind": QUARANTINE_RECOVERY_KIND,
        "new_repository": new_repository,
        "prior_checkpoint_sha256": prior_checkpoint_sha256,
        "prior_maintenance_transition_sha256": prior_maintenance_transition_sha256,
        "repair_commit_head": repair_commit_head,
        "schema_version": QUARANTINE_RECOVERY_SCHEMA_VERSION,
    }
    return validate_quarantine_recovery_record(record)


def validate_quarantine_recovery_record(record: object) -> dict[str, Any]:
    """Validate a quarantine recovery candidate record and return detached copy."""
    if type(record) is not dict:
        raise QuarantineRecoveryRecordError("record must be a dict")
    if set(record.keys()) != _RECORD_KEYS or len(record) != len(_RECORD_KEYS):
        raise QuarantineRecoveryRecordError("record keys mismatch or unknown fields present")

    schema_version = record.get("schema_version")
    if type(schema_version) is not str or schema_version != QUARANTINE_RECOVERY_SCHEMA_VERSION:
        raise QuarantineRecoveryRecordError("schema_version must be 1.0")

    kind = record.get("kind")
    if type(kind) is not str or kind != QUARANTINE_RECOVERY_KIND:
        raise QuarantineRecoveryRecordError("kind must be QUARANTINED_CYCLE_RECOVERY")

    prior_checkpoint_sha256 = record.get("prior_checkpoint_sha256")
    if type(prior_checkpoint_sha256) is not str or not _HEX_64_RE.fullmatch(prior_checkpoint_sha256):
        raise QuarantineRecoveryRecordError("prior_checkpoint_sha256 must be 64 lowercase hex characters")

    prior_maintenance_transition_sha256 = record.get("prior_maintenance_transition_sha256")
    if (
        type(prior_maintenance_transition_sha256) is not str
        or not _HEX_64_RE.fullmatch(prior_maintenance_transition_sha256)
    ):
        raise QuarantineRecoveryRecordError(
            "prior_maintenance_transition_sha256 must be 64 lowercase hex characters"
        )

    failed_run_id = _validate_run_id(record.get("failed_run_id"), "failed_run_id")

    failed_result_sha256 = record.get("failed_result_sha256")
    if type(failed_result_sha256) is not str or not _HEX_64_RE.fullmatch(failed_result_sha256):
        raise QuarantineRecoveryRecordError("failed_result_sha256 must be 64 lowercase hex characters")

    failed_commit_head = record.get("failed_commit_head")
    if type(failed_commit_head) is not str or not _HEX_40_RE.fullmatch(failed_commit_head):
        raise QuarantineRecoveryRecordError("failed_commit_head must be 40 lowercase hex characters")

    repair_commit_head = record.get("repair_commit_head")
    if type(repair_commit_head) is not str or not _HEX_40_RE.fullmatch(repair_commit_head):
        raise QuarantineRecoveryRecordError("repair_commit_head must be 40 lowercase hex characters")

    if failed_commit_head == repair_commit_head:
        raise QuarantineRecoveryRecordError("failed_commit_head cannot equal repair_commit_head")

    final_validation_sha256 = record.get("final_validation_sha256")
    if type(final_validation_sha256) is not str or not _HEX_64_RE.fullmatch(final_validation_sha256):
        raise QuarantineRecoveryRecordError("final_validation_sha256 must be 64 lowercase hex characters")

    norm_repo = _validate_new_repository(record.get("new_repository"))

    if failed_commit_head == norm_repo["head"]:
        raise QuarantineRecoveryRecordError("failed_commit_head cannot equal new_repository head")

    norm_commits = _validate_followup_commits(
        record.get("followup_commits"),
        repair_commit_head=repair_commit_head,
        failed_commit_head=failed_commit_head,
        new_repo_head=norm_repo["head"],
    )

    validated_record: dict[str, Any] = {
        "failed_commit_head": failed_commit_head,
        "failed_result_sha256": failed_result_sha256,
        "failed_run_id": failed_run_id,
        "final_validation_sha256": final_validation_sha256,
        "followup_commits": norm_commits,
        "kind": QUARANTINE_RECOVERY_KIND,
        "new_repository": norm_repo,
        "prior_checkpoint_sha256": prior_checkpoint_sha256,
        "prior_maintenance_transition_sha256": prior_maintenance_transition_sha256,
        "repair_commit_head": repair_commit_head,
        "schema_version": QUARANTINE_RECOVERY_SCHEMA_VERSION,
    }

    _bounded_canonical_bytes(validated_record, MAX_QUARANTINE_RECOVERY_RECORD_BYTES)
    return copy.deepcopy(validated_record)


def serialize_quarantine_recovery_record(record: object) -> bytes:
    """Validate and serialize a quarantine recovery record to canonical JSON bytes."""
    validated = validate_quarantine_recovery_record(record)
    return _bounded_canonical_bytes(validated, MAX_QUARANTINE_RECOVERY_RECORD_BYTES)


def parse_quarantine_recovery_record(raw: bytes) -> dict[str, Any]:
    """Parse, check canonical bytes, and validate a quarantine recovery record."""
    if type(raw) is not bytes:
        raise QuarantineRecoveryRecordError("raw must be bytes")
    if len(raw) > MAX_QUARANTINE_RECOVERY_RECORD_BYTES:
        raise QuarantineRecoveryRecordError("raw bytes exceed maximum allowed length")
    if len(raw) == 0:
        raise QuarantineRecoveryRecordError("raw bytes cannot be empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QuarantineRecoveryRecordError("raw bytes are not valid UTF-8") from exc

    try:
        parsed = unique_json(text)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise QuarantineRecoveryRecordError(
            "Failed to parse JSON or duplicate key detected"
        ) from exc

    if type(parsed) is not dict:
        raise QuarantineRecoveryRecordError("Parsed JSON root must be an object")

    validated = validate_quarantine_recovery_record(parsed)

    canonical = _bounded_canonical_bytes(
        validated, MAX_QUARANTINE_RECOVERY_RECORD_BYTES
    )
    if raw != canonical:
        raise QuarantineRecoveryRecordError(
            "raw bytes do not match canonical JSON serialization"
        )

    return validated
