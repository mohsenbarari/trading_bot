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
    "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md",
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
    r"^(?:scripts/two_site_[A-Za-z0-9_]+\.py|tests/test_two_site_[A-Za-z0-9_]+\.py|scripts/two_site_autopilot_monitor\.sh|scripts/two_site_autopilot_tmux\.sh|docs/refactor/two-server/PROPOSAL_AUTOMATION\.md|docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT\.md|docs/refactor/two-server/templates/GEMINI_TASK_PROMPT\.md|docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT\.md)$"
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

# One owner-authorized Codex maintenance package, not a general Runner whitelist.
_BOOTSTRAP_CHECKPOINT = "4d0b2482a47809827db196a22e05840a3884bc23bb02bb43887f3b6cf8e80cba"
_BOOTSTRAP_PRIOR_HEAD = "3a0f5db6d948c3aa17797e1cf5d771b76145e4e1"
_BOOTSTRAP_PATH_GROUPS = (
    [
        "scripts/two_site_maintenance_transition_publish.py",
        "scripts/two_site_maintenance_transition_record.py",
        "scripts/two_site_maintenance_transition_storage.py",
        "tests/test_two_site_runtime_bootstrap_custody.py",
    ],
    [
        "scripts/two_site_proposal_runner.py",
        "scripts/two_site_proposal_runtime_profile.py",
        "tests/test_two_site_proposal_runner.py",
        "tests/test_two_site_runtime_proposal_profile.py",
    ],
)
_BOOTSTRAP_RUNNER_PATH = "scripts/two_site_proposal_runner.py"


def _exact_keyset(value: object, keys: frozenset[str]) -> bool:
    # Cardinality and all key identities precede every set/hash/lookup operation.
    return (
        type(value) is dict
        and len(value) == len(keys)
        and all(type(key) is str for key in value)
        and set(value) == keys
    )


def _plain_record_tree(value: object, depth: int, remaining: list[int]) -> bool:
    """Bounded identity-only preflight; never invokes foreign value/key hooks."""
    remaining[0] -= 1
    if remaining[0] < 0 or depth > 8:
        return False
    kind = type(value)
    if kind is str:
        return len(value) <= MAX_FIELD_LEN
    if kind is list:
        return len(value) <= 256 and all(
            _plain_record_tree(item, depth + 1, remaining) for item in value
        )
    if kind is dict:
        if len(value) > 256 or not all(type(key) is str for key in value):
            return False
        return all(
            len(key) <= MAX_FIELD_LEN
            and _plain_record_tree(item, depth + 1, remaining)
            for key, item in value.items()
        )
    return False


def maintenance_control_paths_for_record(record: object) -> frozenset[str]:
    """Project controls for exactly the sealed chain; this is NOT acceptance."""
    default = ALLOWED_MAINTENANCE_CONTROL_PATHS
    if not _exact_keyset(record, _RECORD_KEYS):
        return default
    repo = record["new_repository"]
    if not _exact_keyset(repo, frozenset(REPOSITORY_FACT_KEYS)):
        return default
    if not _exact_keyset(repo["assignment_scope"], frozenset(ASSIGNMENT_SCOPE_KEYS_ORDER)):
        return default
    commits = record["maintenance_commits"]
    if type(commits) is not list or len(commits) != 2:
        return default
    if not all(_exact_keyset(commit, _COMMIT_KEYS) for commit in commits):
        return default
    if not _plain_record_tree(record, 0, [4096]):
        return default
    if (
        record["schema_version"] != MAINTENANCE_TRANSITION_SCHEMA_VERSION
        or record["kind"] != MAINTENANCE_TRANSITION_KIND
        or record["prior_checkpoint_sha256"] != _BOOTSTRAP_CHECKPOINT
        or record["prior_head"] != _BOOTSTRAP_PRIOR_HEAD
        or repo["assignment_scope"]["stage_id"] != "P1-00b"
    ):
        return default
    for index, phase in enumerate(("custody", "runtime")):
        commit = commits[index]
        paths = commit["paths"]
        expected = _BOOTSTRAP_PATH_GROUPS[index]
        if type(paths) is not list or len(paths) != len(expected):
            return default
        if not all(type(path) is str for path in paths) or paths != expected:
            return default
        prefix = "ae00-runtime-tool-bootstrap-20261005-330-" + phase
        for key, role in (("worker_run_id", "author"), ("reviewer_run_id", "reviewer")):
            actor = commit[key]
            if type(actor) is not str or len(actor) > 128:
                return default
            suffix = r"-author-[0-2]" if role == "author" else r"-reviewer-[0-2](?:-retry-[12])?"
            if not re.fullmatch(re.escape(prefix) + suffix, actor):
                return default
    permitted = default | {_BOOTSTRAP_RUNNER_PATH}
    try:
        normalized = _validate_new_repository(repo)
        _validate_maintenance_commits(
            commits, record["prior_head"], normalized["head"],
            set(normalized["control_blobs"]), permitted,
        )
        _bounded_canonical_bytes(record)
    except MaintenanceTransitionRecordError:
        return default
    return permitted


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
    "maintenance_control_paths_for_record",
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


def _validate_commit_paths(
    paths: Any, control_blob_paths: set[str],
    permitted_controls: frozenset[str] = ALLOWED_MAINTENANCE_CONTROL_PATHS,
) -> list[str]:
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
            and norm_p not in permitted_controls
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
    if not _exact_keyset(new_repository, frozenset(REPOSITORY_FACT_KEYS)):
        raise MaintenanceTransitionRecordError(
            "new_repository keys mismatch"
        )

    head = new_repository.get("head")
    if type(head) is not str or not _HEX_40_RE.fullmatch(head):
        raise MaintenanceTransitionRecordError(
            "repository head must be 40 lowercase hex characters"
        )

    if type(new_repository.get("status")) is not str or new_repository["status"] != "":
        raise MaintenanceTransitionRecordError(
            "repository status must be empty clean-tree string"
        )

    protected_refs = new_repository.get("protected_refs")
    if (
        not _exact_keyset(protected_refs, frozenset(PROTECTED_REFS))
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
        not _exact_keyset(scope, frozenset(ASSIGNMENT_SCOPE_KEYS_ORDER))
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
    permitted_controls: frozenset[str] = ALLOWED_MAINTENANCE_CONTROL_PATHS,
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
        if not _exact_keyset(raw_commit, _COMMIT_KEYS):
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

        val_paths = _validate_commit_paths(raw_commit.get("paths"), control_blob_paths, permitted_controls)

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
    if not _exact_keyset(record, _RECORD_KEYS):
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
        permitted_controls=maintenance_control_paths_for_record(record),
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
