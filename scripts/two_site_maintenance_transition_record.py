"""Dormant pure reviewed maintenance-transition record codec under AE03.

Provides pure functional building, validation, serialization, and parsing of
version 1.0 reviewed maintenance-transition records bound to prior checkpoint/head
identities, canonical repository facts, and a declared contiguous maintenance commit chain.

Boundary and authority constraints:
1. Pure dormant codec only: contains NO storage writer, startup hook, Provider,
   Broker, monitor, wake, replay, Git, network, filesystem, or environment mutation.
2. Does NOT authorize startup or replay: verification of actual checkpoint,
   Git repo, Broker, STOP file, and locks remain later storage/reader obligations.
3. Fixed error strings: error text is constant and does not echo untrusted payloads.
4. Narrow path compatibility only, never startup/replay authorization.
"""

from __future__ import annotations

import copy
import json
import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        ASSIGNMENT_SCOPE_KEYS_ORDER,
        PROTECTED_REFS,
        REPOSITORY_FACT_KEYS,
    )
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        ASSIGNMENT_SCOPE_KEYS_ORDER,
        PROTECTED_REFS,
        REPOSITORY_FACT_KEYS,
    )

try:
    from scripts.two_site_proposal_runner import unique_json
except ImportError:
    from two_site_proposal_runner import unique_json  # type: ignore[no-redef]

MAX_MAINTENANCE_TRANSITION_RECORD_BYTES: int = 16384
MAINTENANCE_TRANSITION_SCHEMA_VERSION: str = "1.0"
MAINTENANCE_TRANSITION_KIND: str = "REVIEWED_MAINTENANCE_TRANSITION"
MIN_MAINTENANCE_COMMITS: int = 1
MAX_MAINTENANCE_COMMITS: int = 12

MAX_COMMIT_PATHS: int = 256
MAX_CONTROL_BLOBS: int = 256
MAX_ALLOWED_PATHS: int = 256
MAX_FORBIDDEN_PATHS: int = 256
MAX_PATH_LEN: int = 1024
MAX_FIELD_LEN: int = 1024

ALLOWED_MAINTENANCE_CONTROL_PATHS: frozenset[str] = frozenset({
    "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
    "scripts/two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.sh",
    "scripts/two_site_codex_autopilot.py",
    "scripts/two_site_cycle_metrics.py",
    "scripts/two_site_cycle_tools.py",
})
ALLOWED_CONTROL_PATHS: frozenset[str] = ALLOWED_MAINTENANCE_CONTROL_PATHS

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_PATH_RE = re.compile(
    r"^(?:scripts/two_site_[A-Za-z0-9_]+\.py|tests/test_two_site_[A-Za-z0-9_]+\.py|scripts/two_site_autopilot_monitor\.sh|scripts/two_site_autopilot_tmux\.sh|docs/refactor/two-server/PROPOSAL_AUTOMATION\.md|docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT\.md|docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT\.md)$"
)

_RECORD_KEYS = frozenset({
    "schema_version",
    "kind",
    "prior_checkpoint_sha256",
    "prior_head",
    "new_repository",
    "maintenance_commits",
})

_COMMIT_KEYS = frozenset({
    "head",
    "parent",
    "paths",
    "worker_run_id",
    "reviewer_run_id",
    "proposal_sha256",
    "test_evidence_sha256",
})


class MaintenanceTransitionRecordError(ValueError):
    """Raised when maintenance-transition record building, validation, or parsing fails."""


__all__ = [
    "ALLOWED_CONTROL_PATHS",
    "ALLOWED_MAINTENANCE_CONTROL_PATHS",
    "ASSIGNMENT_SCOPE_KEYS_ORDER",
    "MAX_MAINTENANCE_COMMITS",
    "MAX_MAINTENANCE_TRANSITION_RECORD_BYTES",
    "MAINTENANCE_TRANSITION_KIND",
    "MAINTENANCE_TRANSITION_SCHEMA_VERSION",
    "MIN_MAINTENANCE_COMMITS",
    "MaintenanceTransitionRecordError",
    "PROTECTED_REFS",
    "REPOSITORY_FACT_KEYS",
    "build_maintenance_transition_record",
    "parse_maintenance_transition_record",
    "serialize_maintenance_transition_record",
    "validate_maintenance_transition_record",
]


def _bounded_canonical_bytes(
    obj: Any, max_bytes: int = MAX_MAINTENANCE_TRANSITION_RECORD_BYTES
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
            raise MaintenanceTransitionRecordError("Canonical JSON size exceeds limit")
        return encoded
    except MaintenanceTransitionRecordError:
        raise
    except (TypeError, ValueError) as exc:
        raise MaintenanceTransitionRecordError(
            "Record cannot be serialized to canonical JSON"
        ) from exc


def _validate_run_id(val: Any, role: str) -> str:
    if role == "worker_run_id":
        if type(val) is not str:
            raise MaintenanceTransitionRecordError("worker_run_id must be a string")
        if not _RUN_ID_RE.fullmatch(val):
            raise MaintenanceTransitionRecordError("worker_run_id format invalid")
    else:
        if type(val) is not str:
            raise MaintenanceTransitionRecordError("reviewer_run_id must be a string")
        if not _RUN_ID_RE.fullmatch(val):
            raise MaintenanceTransitionRecordError("reviewer_run_id format invalid")
    return val


def _validate_commit_path(p: Any) -> str:
    if type(p) is not str:
        raise MaintenanceTransitionRecordError("commit path must be a string")
    if not (1 <= len(p) <= MAX_PATH_LEN):
        raise MaintenanceTransitionRecordError(
            "commit path length out of bounds"
        )
    if p != p.strip():
        raise MaintenanceTransitionRecordError(
            "commit path must not have leading or trailing whitespace"
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise MaintenanceTransitionRecordError(
            "commit path must not contain control characters or NUL"
        )
    if "\\" in p:
        raise MaintenanceTransitionRecordError("commit path must not contain backslashes")
    if p.startswith("/"):
        raise MaintenanceTransitionRecordError("commit path must be a relative path")
    if any(c in "*?[]{}" for c in p):
        raise MaintenanceTransitionRecordError("commit path must not contain glob characters")
    if re.match(r"^[a-zA-Z]:", p) or ":" in p:
        raise MaintenanceTransitionRecordError(
            "commit path must not contain Windows drive prefix or colon"
        )
    segments = p.split("/")
    if any(seg in (".", "..", "") for seg in segments):
        raise MaintenanceTransitionRecordError(
            "commit path must not contain dot segments or empty components"
        )
    if posixpath.normpath(p) != p or PurePosixPath(p).as_posix() != p:
        raise MaintenanceTransitionRecordError(
            "commit path must be a canonical normalized POSIX path"
        )
    if not _COMMIT_PATH_RE.fullmatch(p):
        raise MaintenanceTransitionRecordError(
            "commit path does not match allowed pattern"
        )
    return p


def _validate_commit_paths(paths: Any, control_blob_paths: set[str]) -> list[str]:
    if type(paths) is not list:
        raise MaintenanceTransitionRecordError("commit paths must be a list")
    if not (1 <= len(paths) <= MAX_COMMIT_PATHS):
        raise MaintenanceTransitionRecordError("commit paths count out of bounds")
    validated_paths: list[str] = []
    seen: set[str] = set()
    for p in paths:
        norm_p = _validate_commit_path(p)
        if (
            norm_p in control_blob_paths
            and norm_p not in ALLOWED_MAINTENANCE_CONTROL_PATHS
        ):
            raise MaintenanceTransitionRecordError(
                "commit path cannot be a control path"
            )
        if norm_p in seen:
            raise MaintenanceTransitionRecordError("duplicate commit path detected")
        seen.add(norm_p)
        validated_paths.append(norm_p)
    if validated_paths != sorted(validated_paths):
        raise MaintenanceTransitionRecordError("commit paths must be sorted")
    return validated_paths


def _validate_control_path(p: Any) -> str:
    if type(p) is not str:
        raise MaintenanceTransitionRecordError("control_blobs path must be a string")
    if not (1 <= len(p) <= MAX_PATH_LEN):
        raise MaintenanceTransitionRecordError(
            "control_blobs path length out of bounds"
        )
    if p != p.strip():
        raise MaintenanceTransitionRecordError(
            "control_blobs path must not have leading or trailing whitespace"
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise MaintenanceTransitionRecordError(
            "control_blobs path must not contain control characters or NUL"
        )
    if "\\" in p:
        raise MaintenanceTransitionRecordError("control_blobs path must not contain backslashes")
    if p.startswith("/"):
        raise MaintenanceTransitionRecordError("control_blobs path must be a relative path")
    if any(c in "*?[]{}" for c in p):
        raise MaintenanceTransitionRecordError("control_blobs path must not contain glob characters")
    if re.match(r"^[a-zA-Z]:", p) or ":" in p:
        raise MaintenanceTransitionRecordError(
            "control_blobs path must not contain Windows drive prefix or colon"
        )
    segments = p.split("/")
    if any(seg in (".", "..", "") for seg in segments):
        raise MaintenanceTransitionRecordError(
            "control_blobs path must not contain dot segments or empty components"
        )
    if posixpath.normpath(p) != p or PurePosixPath(p).as_posix() != p:
        raise MaintenanceTransitionRecordError(
            "control_blobs path must be a canonical normalized POSIX path"
        )
    return p


def _validate_new_repository(new_repository: Any) -> dict[str, Any]:
    if type(new_repository) is not dict:
        raise MaintenanceTransitionRecordError("new_repository must be a dict")
    if (
        set(new_repository.keys()) != REPOSITORY_FACT_KEYS
        or len(new_repository) != len(REPOSITORY_FACT_KEYS)
    ):
        raise MaintenanceTransitionRecordError(
            "new_repository keys mismatch"
        )

    head = new_repository.get("head")
    if type(head) is not str or not _HEX_40_RE.fullmatch(head):
        raise MaintenanceTransitionRecordError(
            "repository head must be 40 lowercase hex characters"
        )

    if new_repository.get("status") != "":
        raise MaintenanceTransitionRecordError(
            "repository status must be empty clean-tree string"
        )

    protected_refs = new_repository.get("protected_refs")
    if (
        type(protected_refs) is not dict
        or set(protected_refs.keys()) != set(PROTECTED_REFS)
        or len(protected_refs) != len(set(PROTECTED_REFS))
    ):
        raise MaintenanceTransitionRecordError(
            "protected_refs keys mismatch"
        )
    for ref_name in sorted(PROTECTED_REFS):
        ref_sha = protected_refs.get(ref_name)
        if type(ref_sha) is not str or not _HEX_40_RE.fullmatch(ref_sha):
            raise MaintenanceTransitionRecordError(
                "protected ref identity must be 40 lowercase hex characters"
            )

    control_blobs = new_repository.get("control_blobs")
    if type(control_blobs) is not dict:
        raise MaintenanceTransitionRecordError("control_blobs must be a dict")
    if not (1 <= len(control_blobs) <= MAX_CONTROL_BLOBS):
        raise MaintenanceTransitionRecordError("control_blobs count out of bounds")
    for path_key, blob_sha in control_blobs.items():
        _validate_control_path(path_key)
        if type(blob_sha) is not str or not _HEX_40_RE.fullmatch(blob_sha):
            raise MaintenanceTransitionRecordError(
                "control blob identity must be 40 lowercase hex characters"
            )

    scope = new_repository.get("assignment_scope")
    if (
        type(scope) is not dict
        or set(scope.keys()) != set(ASSIGNMENT_SCOPE_KEYS_ORDER)
        or len(scope) != len(ASSIGNMENT_SCOPE_KEYS_ORDER)
    ):
        raise MaintenanceTransitionRecordError(
            "assignment_scope keys mismatch"
        )

    aid = scope.get("assignment_id")
    if type(aid) is not str or not (1 <= len(aid) <= MAX_FIELD_LEN) or aid != aid.strip():
        raise MaintenanceTransitionRecordError(
            "assignment_scope assignment_id must be a bounded non-empty string"
        )

    sid = scope.get("stage_id")
    if type(sid) is not str or not (1 <= len(sid) <= MAX_FIELD_LEN) or sid != sid.strip():
        raise MaintenanceTransitionRecordError(
            "assignment_scope stage_id must be a bounded non-empty string"
        )

    scope_sha = scope.get("sha256")
    if type(scope_sha) is not str or not _HEX_64_RE.fullmatch(scope_sha):
        raise MaintenanceTransitionRecordError(
            "assignment_scope sha256 must be 64 lowercase hex characters"
        )

    exp = scope.get("expires_at")
    if type(exp) is not str or not (1 <= len(exp) <= MAX_FIELD_LEN) or exp != exp.strip():
        raise MaintenanceTransitionRecordError(
            "assignment_scope expires_at must be a bounded non-empty string"
        )

    allowed_paths = scope.get("allowed_paths")
    if type(allowed_paths) is not list:
        raise MaintenanceTransitionRecordError(
            "assignment_scope allowed_paths must be a list"
        )
    if len(allowed_paths) > MAX_ALLOWED_PATHS:
        raise MaintenanceTransitionRecordError(
            "assignment_scope allowed_paths count out of bounds"
        )
    for p in allowed_paths:
        if type(p) is not str or not (1 <= len(p) <= MAX_PATH_LEN):
            raise MaintenanceTransitionRecordError(
                "assignment_scope allowed_paths entry must be a bounded string"
            )

    forbidden_paths = scope.get("forbidden_paths")
    if type(forbidden_paths) is not list:
        raise MaintenanceTransitionRecordError(
            "assignment_scope forbidden_paths must be a list"
        )
    if len(forbidden_paths) > MAX_FORBIDDEN_PATHS:
        raise MaintenanceTransitionRecordError(
            "assignment_scope forbidden_paths count out of bounds"
        )
    for p in forbidden_paths:
        if type(p) is not str or not (1 <= len(p) <= MAX_PATH_LEN):
            raise MaintenanceTransitionRecordError(
                "assignment_scope forbidden_paths entry must be a bounded string"
            )

    return {
        "assignment_scope": {
            key: copy.deepcopy(scope[key]) for key in ASSIGNMENT_SCOPE_KEYS_ORDER
        },
        "control_blobs": {
            path: control_blobs[path] for path in sorted(control_blobs.keys())
        },
        "head": head,
        "protected_refs": {
            ref: protected_refs[ref] for ref in sorted(PROTECTED_REFS)
        },
        "status": "",
    }


def _validate_maintenance_commits(
    commits: Any,
    prior_head: str,
    new_repo_head: str,
    control_blob_paths: set[str],
) -> list[dict[str, Any]]:
    if type(commits) is not list:
        raise MaintenanceTransitionRecordError("maintenance_commits must be a list")
    if not (MIN_MAINTENANCE_COMMITS <= len(commits) <= MAX_MAINTENANCE_COMMITS):
        raise MaintenanceTransitionRecordError(
            "maintenance_commits count out of bounds"
        )

    validated_commits: list[dict[str, Any]] = []
    seen_heads: set[str] = set()

    for idx, raw_commit in enumerate(commits):
        if type(raw_commit) is not dict:
            raise MaintenanceTransitionRecordError(
                "maintenance commit entry must be a dict"
            )
        if set(raw_commit.keys()) != _COMMIT_KEYS or len(raw_commit) != len(_COMMIT_KEYS):
            raise MaintenanceTransitionRecordError(
                "maintenance commit keys mismatch"
            )

        head = raw_commit.get("head")
        if type(head) is not str or not _HEX_40_RE.fullmatch(head):
            raise MaintenanceTransitionRecordError(
                "commit head must be 40 lowercase hex characters"
            )

        parent = raw_commit.get("parent")
        if type(parent) is not str or not _HEX_40_RE.fullmatch(parent):
            raise MaintenanceTransitionRecordError(
                "commit parent must be 40 lowercase hex characters"
            )

        if head in seen_heads:
            raise MaintenanceTransitionRecordError(
                "commit head is not distinct"
            )
        seen_heads.add(head)

        if head == parent:
            raise MaintenanceTransitionRecordError(
                "commit head cannot equal parent"
            )

        if head == prior_head:
            raise MaintenanceTransitionRecordError(
                "commit head cannot equal prior_head"
            )

        prop_sha = raw_commit.get("proposal_sha256")
        if type(prop_sha) is not str or not _HEX_64_RE.fullmatch(prop_sha):
            raise MaintenanceTransitionRecordError(
                "commit proposal_sha256 must be 64 lowercase hex characters"
            )

        ev_sha = raw_commit.get("test_evidence_sha256")
        if type(ev_sha) is not str or not _HEX_64_RE.fullmatch(ev_sha):
            raise MaintenanceTransitionRecordError(
                "commit test_evidence_sha256 must be 64 lowercase hex characters"
            )

        w_run_id = _validate_run_id(raw_commit.get("worker_run_id"), "worker_run_id")
        r_run_id = _validate_run_id(raw_commit.get("reviewer_run_id"), "reviewer_run_id")
        if w_run_id == r_run_id:
            raise MaintenanceTransitionRecordError(
                "commit worker_run_id and reviewer_run_id must be distinct"
            )

        val_paths = _validate_commit_paths(raw_commit.get("paths"), control_blob_paths)

        if idx == 0:
            if parent != prior_head:
                raise MaintenanceTransitionRecordError(
                    "first maintenance commit parent must match prior_head"
                )
        else:
            prev_head = validated_commits[idx - 1]["head"]
            if parent != prev_head:
                raise MaintenanceTransitionRecordError(
                    "commit parent does not match previous commit head"
                )

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
        raise MaintenanceTransitionRecordError(
            "final commit head must match new_repository head"
        )

    if prior_head == final_head:
        raise MaintenanceTransitionRecordError(
            "prior_head cannot equal final commit head"
        )

    return validated_commits


def build_maintenance_transition_record(
    prior_checkpoint_sha256: str,
    prior_head: str,
    new_repository: dict[str, Any],
    maintenance_commits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build and validate a version 1.0 reviewed maintenance-transition record."""
    record: dict[str, Any] = {
        "schema_version": MAINTENANCE_TRANSITION_SCHEMA_VERSION,
        "kind": MAINTENANCE_TRANSITION_KIND,
        "prior_checkpoint_sha256": prior_checkpoint_sha256,
        "prior_head": prior_head,
        "new_repository": new_repository,
        "maintenance_commits": maintenance_commits,
    }
    return validate_maintenance_transition_record(record)


def validate_maintenance_transition_record(record: object) -> dict[str, Any]:
    """Validate a maintenance-transition candidate record and return detached copy."""
    if type(record) is not dict:
        raise MaintenanceTransitionRecordError("record must be a dict")
    if set(record.keys()) != _RECORD_KEYS or len(record) != len(_RECORD_KEYS):
        raise MaintenanceTransitionRecordError(
            "record keys mismatch or unknown fields present"
        )

    schema_version = record.get("schema_version")
    if (
        type(schema_version) is not str
        or schema_version != MAINTENANCE_TRANSITION_SCHEMA_VERSION
    ):
        raise MaintenanceTransitionRecordError(
            "schema_version must be 1.0"
        )

    kind = record.get("kind")
    if type(kind) is not str or kind != MAINTENANCE_TRANSITION_KIND:
        raise MaintenanceTransitionRecordError(
            "kind must be REVIEWED_MAINTENANCE_TRANSITION"
        )

    prior_checkpoint_sha256 = record.get("prior_checkpoint_sha256")
    if (
        type(prior_checkpoint_sha256) is not str
        or not _HEX_64_RE.fullmatch(prior_checkpoint_sha256)
    ):
        raise MaintenanceTransitionRecordError(
            "prior_checkpoint_sha256 must be 64 lowercase hex characters"
        )

    prior_head = record.get("prior_head")
    if type(prior_head) is not str or not _HEX_40_RE.fullmatch(prior_head):
        raise MaintenanceTransitionRecordError(
            "prior_head must be 40 lowercase hex characters"
        )

    norm_repo = _validate_new_repository(record.get("new_repository"))
    control_blob_paths = set(norm_repo["control_blobs"].keys())

    norm_commits = _validate_maintenance_commits(
        record.get("maintenance_commits"),
        prior_head=prior_head,
        new_repo_head=norm_repo["head"],
        control_blob_paths=control_blob_paths,
    )

    validated_record: dict[str, Any] = {
        "kind": MAINTENANCE_TRANSITION_KIND,
        "maintenance_commits": norm_commits,
        "new_repository": norm_repo,
        "prior_checkpoint_sha256": prior_checkpoint_sha256,
        "prior_head": prior_head,
        "schema_version": MAINTENANCE_TRANSITION_SCHEMA_VERSION,
    }

    _bounded_canonical_bytes(validated_record, MAX_MAINTENANCE_TRANSITION_RECORD_BYTES)
    return copy.deepcopy(validated_record)


def serialize_maintenance_transition_record(record: object) -> bytes:
    """Validate and serialize a reviewed maintenance-transition record to canonical JSON bytes."""
    validated = validate_maintenance_transition_record(record)
    return _bounded_canonical_bytes(validated, MAX_MAINTENANCE_TRANSITION_RECORD_BYTES)


def parse_maintenance_transition_record(raw: bytes) -> dict[str, Any]:
    """Parse, check canonical bytes, and validate a reviewed maintenance-transition record."""
    if type(raw) is not bytes:
        raise MaintenanceTransitionRecordError("raw must be bytes")
    if len(raw) > MAX_MAINTENANCE_TRANSITION_RECORD_BYTES:
        raise MaintenanceTransitionRecordError("raw bytes exceed maximum allowed length")
    if len(raw) == 0:
        raise MaintenanceTransitionRecordError("raw bytes cannot be empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaintenanceTransitionRecordError("raw bytes are not valid UTF-8") from exc

    try:
        parsed = unique_json(text)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise MaintenanceTransitionRecordError(
            "Failed to parse JSON or duplicate key detected"
        ) from exc

    if type(parsed) is not dict:
        raise MaintenanceTransitionRecordError("Parsed JSON root must be an object")

    validated = validate_maintenance_transition_record(parsed)

    canonical = _bounded_canonical_bytes(
        validated, MAX_MAINTENANCE_TRANSITION_RECORD_BYTES
    )
    if raw != canonical:
        raise MaintenanceTransitionRecordError(
            "raw bytes do not match canonical JSON serialization"
        )

    return validated
