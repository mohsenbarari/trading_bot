"""Dormant read-only builder for file-bound accepted lineage candidate records.

This module provides a bounded, dormant read-only helper to inspect raw untrusted
result.json custody from an existing cycle run directory (resolved via pinned
custody reader under root), bind its exact raw SHA-256 against independently
validated transition and upstream evidence, and build an accepted lineage record
candidate without granting file creation, accepted-lineage publication, lock,
Provider, ack, wake, or replay authority.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        AutopilotError,
        RESULT_KEYS,
        validate_result,
    )
    from scripts.two_site_provider_broker import CYCLE_RUN_RE
    from scripts.two_site_quota_accepted_lineage_record import (
        AcceptedLineageRecordError,
        build_accepted_lineage_record,
    )
    from scripts.two_site_quota_codex_result_custody import (
        CodexResultCustodyError,
        read_untrusted_codex_result_candidate,
    )
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore
        AutopilotError,
        RESULT_KEYS,
        validate_result,
    )
    from two_site_provider_broker import CYCLE_RUN_RE  # type: ignore
    from two_site_quota_accepted_lineage_record import (  # type: ignore
        AcceptedLineageRecordError,
        build_accepted_lineage_record,
    )
    from two_site_quota_codex_result_custody import (  # type: ignore
        CodexResultCustodyError,
        read_untrusted_codex_result_candidate,
    )

_EXPECTED_TRANSITION_KEYS = frozenset(
    RESULT_KEYS | {"head_before", "head_after", "head_changed"}
)


class AcceptedLineageFileproofError(AcceptedLineageRecordError, CodexResultCustodyError):
    """Raised when file-bound accepted lineage candidate creation or verification fails."""


FileproofError = AcceptedLineageFileproofError

__all__ = [
    "AcceptedLineageFileproofError",
    "FileproofError",
    "build_file_bound_accepted_lineage_candidate",
]


def _type_sensitive_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return bool(a == b)


def build_file_bound_accepted_lineage_candidate(
    root: Path,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate a file-bound accepted lineage record candidate.

    Dormant read-only helper. The caller must already have independently validated
    `expected_transition_result` with `validate_transition`, revalidated a clean
    repository/three protected refs/assignment under Supervisor lock, and collected
    `upstream` from protected wait/claim/submission/terminal receipt evidence; this
    helper must NEVER infer expectations from result.json or claim it performed those
    external validations.

    Does not read mutable status, does not ask caller for a result SHA, creates no
    files, and grants no publication, lock, Provider, ack, wake, or replay authority.
    """
    if not isinstance(root, Path):
        raise AcceptedLineageFileproofError(
            f"root must be a Path, got {type(root).__name__}"
        )
    if type(continuation_controller_run_id) is not str:
        raise AcceptedLineageFileproofError(
            f"continuation_controller_run_id must be exact str, got {type(continuation_controller_run_id).__name__}"
        )
    if not continuation_controller_run_id:
        raise AcceptedLineageFileproofError(
            "continuation_controller_run_id must be non-empty"
        )
    if not CYCLE_RUN_RE.fullmatch(continuation_controller_run_id):
        raise AcceptedLineageFileproofError(
            f"continuation_controller_run_id {continuation_controller_run_id!r} is not a valid cycle run ID"
        )

    if type(expected_transition_result) is not dict:
        raise AcceptedLineageFileproofError(
            f"expected_transition_result must be dict, got {type(expected_transition_result).__name__}"
        )
    if set(expected_transition_result.keys()) != _EXPECTED_TRANSITION_KEYS:
        raise AcceptedLineageFileproofError(
            f"expected_transition_result must contain exact 14 keys, "
            f"missing={sorted(_EXPECTED_TRANSITION_KEYS - set(expected_transition_result.keys()))}, "
            f"extra={sorted(set(expected_transition_result.keys()) - _EXPECTED_TRANSITION_KEYS)}"
        )

    if type(acceptance_context) is not dict:
        raise AcceptedLineageFileproofError(
            f"acceptance_context must be dict, got {type(acceptance_context).__name__}"
        )
    ctx_run_id = acceptance_context.get("controller_run_id")
    if type(ctx_run_id) is not str or not CYCLE_RUN_RE.fullmatch(ctx_run_id):
        raise AcceptedLineageFileproofError(
            f"acceptance_context controller_run_id must be valid cycle run ID, got {ctx_run_id!r}"
        )
    if ctx_run_id != continuation_controller_run_id:
        raise AcceptedLineageFileproofError(
            f"acceptance_context controller_run_id ({ctx_run_id!r}) does not match "
            f"continuation_controller_run_id ({continuation_controller_run_id!r})"
        )

    if type(upstream) is not dict:
        raise AcceptedLineageFileproofError(
            f"upstream must be dict, got {type(upstream).__name__}"
        )
    ups_cont_run_id = upstream.get("continuation_controller_run_id")
    if type(ups_cont_run_id) is not str or not CYCLE_RUN_RE.fullmatch(ups_cont_run_id):
        raise AcceptedLineageFileproofError(
            f"upstream continuation_controller_run_id must be valid cycle run ID, got {ups_cont_run_id!r}"
        )
    if ups_cont_run_id != continuation_controller_run_id:
        raise AcceptedLineageFileproofError(
            f"upstream continuation_controller_run_id ({ups_cont_run_id!r}) does not match "
            f"continuation_controller_run_id ({continuation_controller_run_id!r})"
        )

    try:
        raw_bytes, exact_raw_sha, raw_payload = read_untrusted_codex_result_candidate(
            root, continuation_controller_run_id
        )
    except CodexResultCustodyError as exc:
        raise AcceptedLineageFileproofError(
            f"failed to read untrusted codex result candidate: {exc}"
        ) from exc

    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_sha != exact_raw_sha:
        raise AcceptedLineageFileproofError(
            f"raw bytes SHA256 mismatch: {computed_sha} != {exact_raw_sha}"
        )

    if type(raw_payload) is not dict or set(raw_payload.keys()) != RESULT_KEYS:
        raise AcceptedLineageFileproofError(
            f"raw result must contain exact 11 RESULT_KEYS, "
            f"missing={sorted(RESULT_KEYS - set(raw_payload.keys() if isinstance(raw_payload, dict) else ()))}, "
            f"extra={sorted(set(raw_payload.keys()) - RESULT_KEYS if isinstance(raw_payload, dict) else ())}"
        )

    try:
        validate_result(raw_payload)
    except AutopilotError as exc:
        raise AcceptedLineageFileproofError(
            f"raw result failed autopilot validation: {exc}"
        ) from exc

    projected_expected = {k: expected_transition_result[k] for k in RESULT_KEYS}
    for key in sorted(RESULT_KEYS):
        raw_val = raw_payload[key]
        exp_val = projected_expected[key]
        if not _type_sensitive_equal(raw_val, exp_val):
            raise AcceptedLineageFileproofError(
                f"raw result field {key!r} ({raw_val!r}, type={type(raw_val).__name__}) "
                f"does not match expected transition result ({exp_val!r}, type={type(exp_val).__name__})"
            )

    if not _type_sensitive_equal(raw_payload, projected_expected):
        raise AcceptedLineageFileproofError(
            "raw result payload does not match projected expected transition result"
        )

    try:
        record = build_accepted_lineage_record(
            expected_transition_result,
            acceptance_context,
            upstream,
            computed_sha,
        )
    except AcceptedLineageRecordError as exc:
        raise AcceptedLineageFileproofError(
            f"failed to build accepted lineage record: {exc}"
        ) from exc

    return record
