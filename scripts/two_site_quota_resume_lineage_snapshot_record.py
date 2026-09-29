"""Pure schema codec for known-reset quota resume lineage snapshot records (version 1.0).

This module provides pure detached snapshot construction and validation for
expected wait/claim/request lineage before continuation dispatch. It performs pure
validation and canonical encoding only.

Operational boundary and authority constraints:
1. Pure codec only: This module contains NO filesystem I/O, NO live hooks, NO wall clock reads,
   NO network access, and grants NO execution, wake, replay, or cleanup authority.
2. Provenance and publication: This pure codec CANNOT prove independent provenance or
   temporal pre-dispatch publication on its own. A future caller / storage unit must supply
   `expected_wait` and `expected_claim` from the already validated in-memory admission context
   (e.g., collect_known_reset_resume_context_locked / prepare_known_reset_resume_claim) and
   publish the snapshot immutably to no-overwrite storage before queueing/dispatch.
3. Acknowledgement gate: This source alone does NOT close the final acknowledgement gate,
   nor does it prove storage, temporal publication, Codex acceptance, ack, wake, replay,
   Provider execution, or cleanup.
4. Bounded-input contract:
   Streaming JSON encoding rejects incrementally as soon as accumulated UTF-8 bytes exceed
   the specified byte limit (MAX_WAIT_RECORD_BYTES, MAX_CLAIM_RECORD_BYTES, MAX_REQUEST_BYTES,
   or MAX_SNAPSHOT_RECORD_BYTES). Python may allocate memory for individual string chunks
   emitted by iterencode, but aggregate serialized output is never accumulated beyond the cap.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

try:
    from scripts.two_site_provider_broker import CYCLE_RUN_RE, MAX_REQUEST_BYTES, REQUEST_ID_RE
    from scripts.two_site_quota_resume_claim_record import validate_known_reset_resume_claim
    from scripts.two_site_quota_resume_claim_storage import MAX_CLAIM_RECORD_BYTES
    from scripts.two_site_quota_resume_request_record import validate_quota_resume_request
    from scripts.two_site_quota_wait_record import validate_initial_wait_record
    from scripts.two_site_quota_wait_storage import MAX_WAIT_RECORD_BYTES
except ImportError:
    from two_site_provider_broker import CYCLE_RUN_RE, MAX_REQUEST_BYTES, REQUEST_ID_RE
    from two_site_quota_resume_claim_record import validate_known_reset_resume_claim
    from two_site_quota_resume_claim_storage import MAX_CLAIM_RECORD_BYTES
    from two_site_quota_resume_request_record import validate_quota_resume_request
    from two_site_quota_wait_record import validate_initial_wait_record
    from two_site_quota_wait_storage import MAX_WAIT_RECORD_BYTES

__all__ = [
    "LineageSnapshotRecordError",
    "SnapshotRecordError",
    "SNAPSHOT_ENVELOPE_OVERHEAD_BYTES",
    "MAX_SNAPSHOT_RECORD_BYTES",
    "canonical_json_bytes",
    "compute_canonical_sha256",
    "build_expected_lineage_snapshot",
    "validate_expected_lineage_snapshot",
]


class LineageSnapshotRecordError(ValueError):
    """Raised when a lineage snapshot record fails schema, hash, or lineage validation."""


SnapshotRecordError = LineageSnapshotRecordError

_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")

_SNAPSHOT_KEYS = frozenset({
    "schema_version",
    "original_controller_run_id",
    "resume_request_id",
    "expected_wait",
    "expected_claim",
    "expected_request",
    "original_wait_sha256",
    "claim_sha256",
    "request_sha256",
})

SNAPSHOT_ENVELOPE_OVERHEAD_BYTES: int = 4096
MAX_SNAPSHOT_RECORD_BYTES: int = (
    MAX_WAIT_RECORD_BYTES
    + MAX_CLAIM_RECORD_BYTES
    + MAX_REQUEST_BYTES
    + SNAPSHOT_ENVELOPE_OVERHEAD_BYTES
)


def _validate_max_bytes(max_bytes: object) -> int:
    if type(max_bytes) is not int:
        raise LineageSnapshotRecordError(
            f"max_bytes must be exact int, got {max_bytes!r} ({type(max_bytes).__name__})"
        )
    if max_bytes <= 0:
        raise LineageSnapshotRecordError(
            f"max_bytes must be positive int, got {max_bytes!r}"
        )
    if max_bytes > MAX_SNAPSHOT_RECORD_BYTES:
        raise LineageSnapshotRecordError(
            f"max_bytes cannot exceed MAX_SNAPSHOT_RECORD_BYTES ({MAX_SNAPSHOT_RECORD_BYTES}), got {max_bytes!r}"
        )
    return max_bytes


def _type_sensitive_equal(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(v, b[k]) for k, v in a.items())
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return a == b


def canonical_json_bytes(
    data: object,
    max_bytes: int = MAX_SNAPSHOT_RECORD_BYTES,
) -> bytes:
    """Encode an object into canonical sorted compact UTF-8 JSON bytes under an exact positive byte cap."""
    limit = _validate_max_bytes(max_bytes)
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    chunks: list[bytes] = []
    total_bytes = 0
    try:
        for chunk in encoder.iterencode(data):
            encoded = chunk.encode("utf-8")
            total_bytes += len(encoded)
            if total_bytes > limit:
                raise LineageSnapshotRecordError(
                    f"canonical JSON exceeds byte limit ({total_bytes} > {limit})"
                )
            chunks.append(encoded)
        return b"".join(chunks)
    except (KeyboardInterrupt, SystemExit):
        raise
    except LineageSnapshotRecordError:
        raise
    except (ValueError, TypeError) as exc:
        raise LineageSnapshotRecordError(
            f"record cannot be serialized to canonical JSON: {exc}"
        ) from exc


def compute_canonical_sha256(
    data: object,
    max_bytes: int = MAX_SNAPSHOT_RECORD_BYTES,
) -> str:
    """Compute SHA-256 hex digest of canonical JSON encoding under an exact positive byte cap."""
    return hashlib.sha256(canonical_json_bytes(data, max_bytes=max_bytes)).hexdigest()


def _check_binding(name: str, a: object, b: object) -> None:
    if not _type_sensitive_equal(a, b):
        raise LineageSnapshotRecordError(
            f"Cross-binding mismatch for {name}: {a!r} ({type(a).__name__}) != {b!r} ({type(b).__name__})"
        )


def _validate_cross_bindings(
    *,
    snapshot: dict[str, Any],
    wait_record: dict[str, Any],
    claim_record: dict[str, Any],
    request: dict[str, Any],
    wait_sha: str,
    claim_sha: str,
    request_sha: str,
) -> None:
    orig_cid = wait_record["controller_run_id"]
    orig_rid = wait_record["request_id"]
    resume_rid = claim_record["resume_request_id"]
    fp = wait_record["semantic_task_fingerprint"]
    role = wait_record["quota_attempt"]["role"]
    res = wait_record["reservation"]
    sch = wait_record["schedule"]
    expected_resume_prefix = f"qresume-{orig_rid}"

    for prefix, target in (("snapshot", snapshot), ("claim", claim_record), ("request", request)):
        _check_binding(f"{prefix}.original_wait_sha256", target["original_wait_sha256"], wait_sha)
        _check_binding(f"{prefix}.original_controller_run_id", target["original_controller_run_id"], orig_cid)

    _check_binding("snapshot.claim_sha256", snapshot["claim_sha256"], claim_sha)
    _check_binding("request.claim_sha256", request["claim_sha256"], claim_sha)
    _check_binding("snapshot.request_sha256", snapshot["request_sha256"], request_sha)

    _check_binding("continuation_controller_run_id", claim_record["continuation_controller_run_id"], request["controller_run_id"])
    _check_binding("snapshot.resume_request_id", snapshot["resume_request_id"], resume_rid)
    _check_binding("request.request_id", request["request_id"], resume_rid)
    _check_binding("claim.original_request_id", claim_record["original_request_id"], orig_rid)
    _check_binding("request.original_request_id", request["original_request_id"], orig_rid)

    for target_name, target in (("claim", claim_record), ("request", request)):
        _check_binding(f"{target_name}.semantic_task_fingerprint", target["semantic_task_fingerprint"], fp)
        _check_binding(f"{target_name}.role", target["role"], role)

    for field in ("task_path", "task_sha256", "source_head", "assignment_id", "assignment_sha256"):
        _check_binding(field, claim_record[field], res[field])
        _check_binding(f"request.{field}", request[field], res[field])

    _check_binding("claim.task_id", claim_record["task_id"], request["task_id"])
    _check_binding("original_run_prefix", claim_record["original_run_prefix"], res["run_prefix"])
    _check_binding("claim.resume_run_prefix", claim_record["resume_run_prefix"], expected_resume_prefix)
    _check_binding("request.run_prefix", request["run_prefix"], expected_resume_prefix)
    _check_binding("reset_kind", claim_record["reset_kind"], sch["reset_kind"])
    _check_binding("next_check_at_utc", claim_record["next_check_at_utc"], sch["next_check_at_utc"])
    _check_binding("claimed_at_utc", request["claimed_at_utc"], claim_record["claimed_at_utc"])


def _validate_lineage_material(
    wait_record: object,
    expected_wait: object,
    claim_record: object,
    expected_claim: object,
    request: object,
    request_label: str = "request",
) -> tuple[dict[str, Any], str, dict[str, Any], str, dict[str, Any], str]:
    for name, val in (
        ("wait_record", wait_record),
        ("expected_wait", expected_wait),
        ("claim_record", claim_record),
        ("expected_claim", expected_claim),
        (request_label, request),
    ):
        if type(val) is not dict:
            raise LineageSnapshotRecordError(f"{name} must be dict, got {type(val).__name__}")

    try:
        validated_wait = validate_initial_wait_record(wait_record, expected_wait)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise LineageSnapshotRecordError(f"wait_record validation failed: {exc}") from exc

    try:
        wait_bytes = canonical_json_bytes(validated_wait, max_bytes=MAX_WAIT_RECORD_BYTES)
    except LineageSnapshotRecordError as exc:
        raise LineageSnapshotRecordError(
            f"validated wait_record exceeds MAX_WAIT_RECORD_BYTES ({MAX_WAIT_RECORD_BYTES}): {exc}"
        ) from exc
    wait_sha = hashlib.sha256(wait_bytes).hexdigest()

    try:
        validated_claim = validate_known_reset_resume_claim(claim_record, expected_claim)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise LineageSnapshotRecordError(f"claim_record validation failed: {exc}") from exc

    try:
        claim_bytes = canonical_json_bytes(validated_claim, max_bytes=MAX_CLAIM_RECORD_BYTES)
    except LineageSnapshotRecordError as exc:
        raise LineageSnapshotRecordError(
            f"validated claim_record exceeds MAX_CLAIM_RECORD_BYTES ({MAX_CLAIM_RECORD_BYTES}): {exc}"
        ) from exc
    claim_sha = hashlib.sha256(claim_bytes).hexdigest()

    created_at = request.get("created_at") if isinstance(request, dict) else None
    if type(created_at) is bool or not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
        raise LineageSnapshotRecordError(
            f"{request_label} created_at must be finite numeric (not bool), got {created_at!r}"
        )

    try:
        validated_request = validate_quota_resume_request(
            request,
            validated_claim,
            expected_claim,
            claim_sha,
            now_epoch=created_at,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise LineageSnapshotRecordError(f"{request_label} validation failed: {exc}") from exc

    try:
        request_bytes = canonical_json_bytes(validated_request, max_bytes=MAX_REQUEST_BYTES)
    except LineageSnapshotRecordError as exc:
        raise LineageSnapshotRecordError(
            f"validated request exceeds MAX_REQUEST_BYTES ({MAX_REQUEST_BYTES}): {exc}"
        ) from exc
    request_sha = hashlib.sha256(request_bytes).hexdigest()

    return validated_wait, wait_sha, validated_claim, claim_sha, validated_request, request_sha


def validate_expected_lineage_snapshot(
    snapshot: object,
    wait_record: object,
    claim_record: object,
) -> dict[str, Any]:
    """Validate a version 1.0 expected lineage snapshot against wait and claim records."""
    if type(snapshot) is not dict:
        raise LineageSnapshotRecordError(f"snapshot must be dict, got {type(snapshot).__name__}")
    if type(wait_record) is not dict:
        raise LineageSnapshotRecordError(f"wait_record must be dict, got {type(wait_record).__name__}")
    if type(claim_record) is not dict:
        raise LineageSnapshotRecordError(f"claim_record must be dict, got {type(claim_record).__name__}")

    snap_keys = set(snapshot.keys())
    if snap_keys != _SNAPSHOT_KEYS:
        missing = sorted(_SNAPSHOT_KEYS - snap_keys)
        extra = sorted(snap_keys - _SNAPSHOT_KEYS)
        raise LineageSnapshotRecordError(f"snapshot keys mismatch: missing={missing}, extra={extra}")

    if snapshot["schema_version"] != "1.0" or type(snapshot["schema_version"]) is not str:
        raise LineageSnapshotRecordError(f"schema_version must be '1.0', got {snapshot['schema_version']!r}")

    orig_cid = snapshot["original_controller_run_id"]
    if type(orig_cid) is not str or not CYCLE_RUN_RE.fullmatch(orig_cid):
        raise LineageSnapshotRecordError(f"original_controller_run_id invalid: {orig_cid!r}")

    resume_rid = snapshot["resume_request_id"]
    if type(resume_rid) is not str or not REQUEST_ID_RE.fullmatch(resume_rid):
        raise LineageSnapshotRecordError(f"resume_request_id invalid: {resume_rid!r}")

    for digest_field in ("original_wait_sha256", "claim_sha256", "request_sha256"):
        val = snapshot[digest_field]
        if type(val) is not str or not _HEX_64_RE.fullmatch(val):
            raise LineageSnapshotRecordError(f"{digest_field} must be 64 lowercase hex: {val!r}")

    try:
        canonical_json_bytes(snapshot, max_bytes=MAX_SNAPSHOT_RECORD_BYTES)
    except LineageSnapshotRecordError as exc:
        raise LineageSnapshotRecordError(
            f"canonical snapshot exceeds MAX_SNAPSHOT_RECORD_BYTES ({MAX_SNAPSHOT_RECORD_BYTES}): {exc}"
        ) from exc

    expected_wait = snapshot["expected_wait"]
    expected_claim = snapshot["expected_claim"]
    expected_request = snapshot["expected_request"]
    if type(expected_wait) is not dict or type(expected_claim) is not dict or type(expected_request) is not dict:
        raise LineageSnapshotRecordError("expected_wait, expected_claim, and expected_request must be dicts")

    (
        validated_wait,
        wait_sha,
        validated_claim,
        claim_sha,
        validated_request,
        request_sha,
    ) = _validate_lineage_material(
        wait_record,
        expected_wait,
        claim_record,
        expected_claim,
        expected_request,
        request_label="expected_request",
    )

    if not _type_sensitive_equal(expected_request, validated_request):
        raise LineageSnapshotRecordError("expected_request does not match validated request")

    _validate_cross_bindings(
        snapshot=snapshot,
        wait_record=validated_wait,
        claim_record=validated_claim,
        request=validated_request,
        wait_sha=wait_sha,
        claim_sha=claim_sha,
        request_sha=request_sha,
    )

    return copy.deepcopy(snapshot)


def build_expected_lineage_snapshot(
    wait_record: object,
    expected_wait: object,
    claim_record: object,
    expected_claim: object,
    request: object,
) -> dict[str, Any]:
    """Pure builder for version 1.0 quota resume lineage snapshot records."""
    (
        validated_wait,
        wait_sha,
        validated_claim,
        claim_sha,
        validated_request,
        request_sha,
    ) = _validate_lineage_material(
        wait_record,
        expected_wait,
        claim_record,
        expected_claim,
        request,
        request_label="request",
    )

    snapshot: dict[str, Any] = {
        "schema_version": "1.0",
        "original_controller_run_id": validated_wait["controller_run_id"],
        "resume_request_id": validated_claim["resume_request_id"],
        "expected_wait": copy.deepcopy(expected_wait),
        "expected_claim": copy.deepcopy(expected_claim),
        "expected_request": copy.deepcopy(validated_request),
        "original_wait_sha256": wait_sha,
        "claim_sha256": claim_sha,
        "request_sha256": request_sha,
    }

    return validate_expected_lineage_snapshot(snapshot, wait_record, claim_record)
