"""Pure schema codec for quota resume request records (version 1.0)."""

from __future__ import annotations

import copy
import math
import re
from typing import Any

try:
    from scripts.two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
except ImportError:
    from two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from two_site_quota_wait_schedule import _parse_utc_iso

__all__ = [
    "ResumeRequestRecordError",
    "build_quota_resume_request",
    "validate_quota_resume_request",
]


class ResumeRequestRecordError(ValueError):
    """Raised when a quota resume request record fails validation."""


_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUEST_KEYS = frozenset({
    "schema_version",
    "request_type",
    "request_id",
    "controller_run_id",
    "original_controller_run_id",
    "original_request_id",
    "claim_sha256",
    "original_wait_sha256",
    "semantic_task_fingerprint",
    "task_id",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
    "run_prefix",
    "role",
    "timeout",
    "created_at",
})

_MIRRORED_LINEAGE_FIELDS = (
    ("request_id", "resume_request_id"),
    ("controller_run_id", "continuation_controller_run_id"),
    ("original_controller_run_id", "original_controller_run_id"),
    ("original_request_id", "original_request_id"),
    ("original_wait_sha256", "original_wait_sha256"),
    ("semantic_task_fingerprint", "semantic_task_fingerprint"),
    ("task_id", "task_id"),
    ("task_path", "task_path"),
    ("task_sha256", "task_sha256"),
    ("source_head", "source_head"),
    ("assignment_id", "assignment_id"),
    ("assignment_sha256", "assignment_sha256"),
    ("run_prefix", "resume_run_prefix"),
    ("role", "role"),
)


def validate_quota_resume_request(
    request: object,
    claim: object,
    expected_claim: object,
    expected_claim_sha256: object,
    now_epoch: object,
) -> dict[str, Any]:
    """Validate a version 1.0 quota resume request against validated claim and expected context."""
    if type(request) is not dict:
        raise ResumeRequestRecordError(f"request must be dict, got {type(request).__name__}")

    req_keys = set(request.keys())
    if req_keys != _REQUEST_KEYS:
        missing = sorted(_REQUEST_KEYS - req_keys)
        extra = sorted(req_keys - _REQUEST_KEYS)
        raise ResumeRequestRecordError(f"request keys mismatch: missing={missing}, extra={extra}")

    if type(request["schema_version"]) is not str or request["schema_version"] != "1.0":
        raise ResumeRequestRecordError(f"schema_version must be '1.0', got {request['schema_version']!r}")

    if type(request["request_type"]) is not str or request["request_type"] != "QUOTA_CONTINUATION":
        raise ResumeRequestRecordError(f"request_type must be 'QUOTA_CONTINUATION', got {request['request_type']!r}")

    if type(expected_claim_sha256) is not str or not _HEX_64_RE.fullmatch(expected_claim_sha256):
        raise ResumeRequestRecordError(
            f"expected_claim_sha256 must be 64 lowercase hex: {expected_claim_sha256!r}"
        )

    if type(request["claim_sha256"]) is not str or not _HEX_64_RE.fullmatch(request["claim_sha256"]):
        raise ResumeRequestRecordError(f"claim_sha256 must be 64 lowercase hex: {request['claim_sha256']!r}")

    if request["claim_sha256"] != expected_claim_sha256:
        raise ResumeRequestRecordError(
            f"claim_sha256 mismatch: request={request['claim_sha256']!r} != expected={expected_claim_sha256!r}"
        )

    try:
        validated_claim = validate_known_reset_resume_claim(claim, expected_claim)
    except ResumeClaimRecordError as exc:
        raise ResumeRequestRecordError(f"claim validation failed: {exc}") from exc

    for req_key, claim_key in _MIRRORED_LINEAGE_FIELDS:
        req_val = request[req_key]
        claim_val = validated_claim[claim_key]
        if type(req_val) is not type(claim_val) or req_val != claim_val:
            raise ResumeRequestRecordError(
                f"Field {req_key!r} mismatch: request={req_val!r} ({type(req_val).__name__}) != "
                f"claim[{claim_key!r}]={claim_val!r} ({type(claim_val).__name__})"
            )

    timeout = request["timeout"]
    if type(timeout) is not int or type(timeout) is bool or not (10 <= timeout <= 900):
        raise ResumeRequestRecordError(
            f"timeout must be exact int 10..900, got {timeout!r} ({type(timeout).__name__})"
        )

    if type(now_epoch) is bool or not isinstance(now_epoch, (int, float)) or not math.isfinite(now_epoch):
        raise ResumeRequestRecordError(f"now_epoch must be finite numeric (not bool), got {now_epoch!r}")

    created_at = request["created_at"]
    if type(created_at) is bool or not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
        raise ResumeRequestRecordError(f"created_at must be finite numeric (not bool), got {created_at!r}")

    if created_at < now_epoch - 300 or created_at > now_epoch + 30:
        raise ResumeRequestRecordError(
            f"created_at outside freshness window [now-300, now+30]: created_at={created_at}, now={now_epoch}"
        )

    try:
        claim_dt = _parse_utc_iso(validated_claim["claimed_at_utc"])
    except Exception as exc:
        raise ResumeRequestRecordError(f"claimed_at_utc invalid: {exc}") from exc
    claim_epoch = claim_dt.timestamp()

    if now_epoch < claim_epoch:
        raise ResumeRequestRecordError(
            f"Clock regression: now_epoch ({now_epoch}) cannot be earlier than claim epoch ({claim_epoch})"
        )

    if created_at < claim_epoch:
        raise ResumeRequestRecordError(
            f"Chronology violation: created_at ({created_at}) cannot be earlier than claim epoch ({claim_epoch})"
        )

    return copy.deepcopy(request)


def build_quota_resume_request(
    claim: object,
    expected_claim: object,
    expected_claim_sha256: object,
    timeout: object,
    now_epoch: object,
) -> dict[str, Any]:
    """Pure builder for version 1.0 quota resume request records."""
    if type(expected_claim_sha256) is not str or not _HEX_64_RE.fullmatch(expected_claim_sha256):
        raise ResumeRequestRecordError(
            f"expected_claim_sha256 must be 64 lowercase hex: {expected_claim_sha256!r}"
        )

    if type(timeout) is not int or type(timeout) is bool or not (10 <= timeout <= 900):
        raise ResumeRequestRecordError(
            f"timeout must be exact int 10..900, got {timeout!r} ({type(timeout).__name__})"
        )

    if type(now_epoch) is bool or not isinstance(now_epoch, (int, float)) or not math.isfinite(now_epoch):
        raise ResumeRequestRecordError(f"now_epoch must be finite numeric (not bool), got {now_epoch!r}")

    try:
        validated_claim = validate_known_reset_resume_claim(claim, expected_claim)
    except ResumeClaimRecordError as exc:
        raise ResumeRequestRecordError(f"claim validation failed: {exc}") from exc

    request: dict[str, Any] = {
        "schema_version": "1.0",
        "request_type": "QUOTA_CONTINUATION",
        "request_id": validated_claim["resume_request_id"],
        "controller_run_id": validated_claim["continuation_controller_run_id"],
        "original_controller_run_id": validated_claim["original_controller_run_id"],
        "original_request_id": validated_claim["original_request_id"],
        "claim_sha256": expected_claim_sha256,
        "original_wait_sha256": validated_claim["original_wait_sha256"],
        "semantic_task_fingerprint": validated_claim["semantic_task_fingerprint"],
        "task_id": validated_claim["task_id"],
        "task_path": validated_claim["task_path"],
        "task_sha256": validated_claim["task_sha256"],
        "source_head": validated_claim["source_head"],
        "assignment_id": validated_claim["assignment_id"],
        "assignment_sha256": validated_claim["assignment_sha256"],
        "run_prefix": validated_claim["resume_run_prefix"],
        "role": validated_claim["role"],
        "timeout": timeout,
        "created_at": now_epoch,
    }

    return validate_quota_resume_request(
        request,
        claim,
        expected_claim,
        expected_claim_sha256,
        now_epoch,
    )
