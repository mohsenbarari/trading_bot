"""Dormant pure final-acknowledgement record codec for two-site final acceptance.

Provides pure functional building and type-sensitive validation of version 1.0
final acknowledgement candidate records bound to a structurally validated accepted-lineage
record and its canonical SHA-256 digest.

Boundary and authority constraints:
1. Dormant codec only: contains NO filesystem, time, network, lock, Supervisor, Broker,
   Provider, pruning, wake or replay side effect or authority.
2. Pure caller obligations: a future caller must read the accepted-lineage record from
   strict file custody, hold exclusive Supervisor lock, independently revalidate repository
   and source lineage, and publish durable one-shot acknowledgement before any wake.
3. Proof limitation: recomputing canonical SHA-256 proves internal byte consistency only,
   NOT that the record originated from trusted on-disk custody.
4. Retention independence: does not claim this codec alone closes C5e or authorizes cleanup;
   C4c terminal continuation receipt retention requires separate reference-free >=10d proof.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
from typing import Any

from scripts.two_site_provider_broker import CYCLE_RUN_RE
from scripts.two_site_quota_accepted_lineage_record import (
    AcceptedLineageRecordError,
    MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
    _validate_record_structure,
)

MAX_FINAL_ACK_RECORD_BYTES: int = 2048
FINAL_ACK_SCHEMA_VERSION: str = "1.0"
FINAL_ACK_KIND: str = "CODEX_FINAL_ACCEPTANCE"

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TIMESTAMP_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_FINAL_ACK_KEYS = frozenset({
    "schema_version",
    "ack_kind",
    "original_controller_run_id",
    "continuation_controller_run_id",
    "resume_request_id",
    "terminal_receipt_sha256",
    "accepted_lineage_sha256",
    "result_sha256",
    "accepted_state",
    "head_after",
    "assignment_sha256",
    "acknowledged_at_utc",
})


class FinalAckRecordError(ValueError):
    """Raised when final acknowledgement candidate building, cross-binding, or validation fails."""


__all__ = [
    "FINAL_ACK_KIND",
    "FINAL_ACK_SCHEMA_VERSION",
    "FinalAckRecordError",
    "MAX_FINAL_ACK_RECORD_BYTES",
    "build_final_ack_candidate",
    "validate_final_ack_candidate",
]


def _bounded_canonical_bytes(obj: Any, max_bytes: int) -> bytes:
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        chunks: list[bytes] = []
        total_bytes = 0
        for chunk in encoder.iterencode(obj):
            chunk_bytes = chunk.encode("utf-8")
            total_bytes += len(chunk_bytes)
            if total_bytes > max_bytes:
                raise FinalAckRecordError(
                    f"canonical JSON size exceeds limit ({max_bytes} bytes)"
                )
            chunks.append(chunk_bytes)
        out = b"".join(chunks)
        if len(out) > max_bytes:
            raise FinalAckRecordError(
                f"canonical JSON size exceeds limit ({max_bytes} bytes)"
            )
        return out
    except FinalAckRecordError:
        raise
    except (TypeError, ValueError) as exc:
        raise FinalAckRecordError(
            "record cannot be serialized to canonical JSON"
        ) from exc


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


def _validate_timestamp_utc(val: Any) -> str:
    if type(val) is not str:
        raise FinalAckRecordError("acknowledged_at_utc must be a string")
    if not _TIMESTAMP_UTC_RE.fullmatch(val):
        raise FinalAckRecordError(
            "acknowledged_at_utc must match canonical whole-second UTC format YYYY-MM-DDTHH:MM:SSZ"
        )
    try:
        dt = datetime.datetime.strptime(val, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise FinalAckRecordError(
            "acknowledged_at_utc has invalid calendar date/time"
        ) from exc
    if dt.strftime("%Y-%m-%dT%H:%M:%SZ") != val:
        raise FinalAckRecordError(
            "acknowledged_at_utc canonical representation mismatch"
        )
    return val


def _validate_accepted_lineage_input(
    accepted_lineage_record: Any,
    accepted_lineage_sha256: Any,
) -> bytes:
    if type(accepted_lineage_record) is not dict:
        raise FinalAckRecordError("accepted_lineage_record must be a dict")
    if type(accepted_lineage_sha256) is not str or not _HEX_64_RE.fullmatch(
        accepted_lineage_sha256
    ):
        raise FinalAckRecordError(
            "accepted_lineage_sha256 must be 64 lower hex characters"
        )

    # Cheap exact-str length prebounds on structurally free-text fields before structural
    # validation or serialization to ensure bounded chunk allocation in iterencode.
    rec_asgn = accepted_lineage_record.get("assignment_id")
    if type(rec_asgn) is str and len(rec_asgn) > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
        raise FinalAckRecordError(
            f"record assignment_id length exceeds limit ({MAX_ACCEPTED_LINEAGE_RECORD_BYTES} bytes)"
        )
    upstream = accepted_lineage_record.get("upstream")
    if type(upstream) is dict:
        up_task = upstream.get("task_id")
        if type(up_task) is str and len(up_task) > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
            raise FinalAckRecordError(
                f"upstream task_id length exceeds limit ({MAX_ACCEPTED_LINEAGE_RECORD_BYTES} bytes)"
            )
        up_asgn = upstream.get("assignment_id")
        if type(up_asgn) is str and len(up_asgn) > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
            raise FinalAckRecordError(
                f"upstream assignment_id length exceeds limit ({MAX_ACCEPTED_LINEAGE_RECORD_BYTES} bytes)"
            )

    try:
        _validate_record_structure(accepted_lineage_record)
    except AcceptedLineageRecordError as exc:
        raise FinalAckRecordError(
            "accepted_lineage_record structural validation failed"
        ) from exc

    raw_bytes = _bounded_canonical_bytes(
        accepted_lineage_record, MAX_ACCEPTED_LINEAGE_RECORD_BYTES
    )

    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_sha != accepted_lineage_sha256:
        raise FinalAckRecordError(
            "accepted_lineage_sha256 mismatch with computed canonical SHA-256"
        )
    return raw_bytes


def build_final_ack_candidate(
    accepted_lineage_record: dict[str, Any],
    accepted_lineage_sha256: str,
    acknowledged_at_utc: str,
) -> dict[str, Any]:
    """Build and self-validate a version 1.0 final acknowledgement candidate record."""
    _validate_accepted_lineage_input(accepted_lineage_record, accepted_lineage_sha256)
    _validate_timestamp_utc(acknowledged_at_utc)

    upstream = accepted_lineage_record["upstream"]
    candidate = {
        "schema_version": FINAL_ACK_SCHEMA_VERSION,
        "ack_kind": FINAL_ACK_KIND,
        "original_controller_run_id": upstream["original_controller_run_id"],
        "continuation_controller_run_id": accepted_lineage_record["controller_run_id"],
        "resume_request_id": upstream["resume_request_id"],
        "terminal_receipt_sha256": upstream["terminal_receipt_sha256"],
        "accepted_lineage_sha256": accepted_lineage_sha256,
        "result_sha256": accepted_lineage_record["result_sha256"],
        "accepted_state": accepted_lineage_record["accepted_state"],
        "head_after": accepted_lineage_record["head_after"],
        "assignment_sha256": accepted_lineage_record["assignment_sha256"],
        "acknowledged_at_utc": acknowledged_at_utc,
    }

    return validate_final_ack_candidate(
        candidate=candidate,
        accepted_lineage_record=accepted_lineage_record,
        accepted_lineage_sha256=accepted_lineage_sha256,
    )


def validate_final_ack_candidate(
    candidate: dict[str, Any],
    accepted_lineage_record: dict[str, Any],
    accepted_lineage_sha256: str,
) -> dict[str, Any]:
    """Validate a final acknowledgement candidate against accepted-lineage record and return detached copy."""
    if type(candidate) is not dict:
        raise FinalAckRecordError("candidate must be a dict")
    cand_keys = set(candidate.keys())
    if cand_keys != _FINAL_ACK_KEYS:
        raise FinalAckRecordError("candidate keys mismatch")

    if (
        type(candidate["schema_version"]) is not str
        or candidate["schema_version"] != FINAL_ACK_SCHEMA_VERSION
    ):
        raise FinalAckRecordError(
            f"candidate schema_version must be {FINAL_ACK_SCHEMA_VERSION!r}"
        )
    if type(candidate["ack_kind"]) is not str or candidate["ack_kind"] != FINAL_ACK_KIND:
        raise FinalAckRecordError(
            f"candidate ack_kind must be {FINAL_ACK_KIND!r}"
        )
    if type(candidate["original_controller_run_id"]) is not str or not CYCLE_RUN_RE.fullmatch(
        candidate["original_controller_run_id"]
    ):
        raise FinalAckRecordError(
            "candidate original_controller_run_id must match cycle run pattern"
        )
    if type(
        candidate["continuation_controller_run_id"]
    ) is not str or not CYCLE_RUN_RE.fullmatch(candidate["continuation_controller_run_id"]):
        raise FinalAckRecordError(
            "candidate continuation_controller_run_id must match cycle run pattern"
        )
    if type(candidate["resume_request_id"]) is not str or not _REQUEST_ID_RE.fullmatch(
        candidate["resume_request_id"]
    ):
        raise FinalAckRecordError(
            "candidate resume_request_id must be 32 lower hex characters"
        )
    if type(candidate["terminal_receipt_sha256"]) is not str or not _HEX_64_RE.fullmatch(
        candidate["terminal_receipt_sha256"]
    ):
        raise FinalAckRecordError(
            "candidate terminal_receipt_sha256 must be 64 lower hex characters"
        )
    if type(candidate["accepted_lineage_sha256"]) is not str or not _HEX_64_RE.fullmatch(
        candidate["accepted_lineage_sha256"]
    ):
        raise FinalAckRecordError(
            "candidate accepted_lineage_sha256 must be 64 lower hex characters"
        )
    if type(candidate["result_sha256"]) is not str or not _HEX_64_RE.fullmatch(
        candidate["result_sha256"]
    ):
        raise FinalAckRecordError(
            "candidate result_sha256 must be 64 lower hex characters"
        )
    state = candidate["accepted_state"]
    if type(state) is not str or state not in ("CONTINUE", "COMPLETE"):
        raise FinalAckRecordError(
            "candidate accepted_state must be 'CONTINUE' or 'COMPLETE'"
        )
    if type(candidate["head_after"]) is not str or not _HEX_40_RE.fullmatch(
        candidate["head_after"]
    ):
        raise FinalAckRecordError(
            "candidate head_after must be 40 lower hex characters"
        )
    if type(candidate["assignment_sha256"]) is not str or not _HEX_64_RE.fullmatch(
        candidate["assignment_sha256"]
    ):
        raise FinalAckRecordError(
            "candidate assignment_sha256 must be 64 lower hex characters"
        )

    _validate_timestamp_utc(candidate["acknowledged_at_utc"])

    # For candidate, all 12 fields are fixed constants or regex-validated above,
    # guaranteeing strictly bounded length per field before serialization.
    _bounded_canonical_bytes(candidate, MAX_FINAL_ACK_RECORD_BYTES)

    _validate_accepted_lineage_input(accepted_lineage_record, accepted_lineage_sha256)

    upstream = accepted_lineage_record["upstream"]
    rederived: dict[str, Any] = {
        "schema_version": FINAL_ACK_SCHEMA_VERSION,
        "ack_kind": FINAL_ACK_KIND,
        "original_controller_run_id": upstream["original_controller_run_id"],
        "continuation_controller_run_id": accepted_lineage_record["controller_run_id"],
        "resume_request_id": upstream["resume_request_id"],
        "terminal_receipt_sha256": upstream["terminal_receipt_sha256"],
        "accepted_lineage_sha256": accepted_lineage_sha256,
        "result_sha256": accepted_lineage_record["result_sha256"],
        "accepted_state": accepted_lineage_record["accepted_state"],
        "head_after": accepted_lineage_record["head_after"],
        "assignment_sha256": accepted_lineage_record["assignment_sha256"],
    }

    for key, expected_val in rederived.items():
        actual_val = candidate[key]
        if not _type_sensitive_equal(actual_val, expected_val):
            raise FinalAckRecordError(
                f"candidate field {key!r} does not match rederived accepted-lineage value"
            )

    return copy.deepcopy(candidate)
