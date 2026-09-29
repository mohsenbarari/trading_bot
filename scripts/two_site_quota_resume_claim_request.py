"""Locked composer and publisher for known-reset quota resume requests."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from scripts.two_site_quota_resume_claim_admission import (
        ClaimAdmissionError,
        prepare_known_reset_resume_claim,
    )
    from scripts.two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from scripts.two_site_quota_resume_claim_storage import (
        ClaimStorageError,
        publish_known_reset_resume_claim,
    )
    from scripts.two_site_quota_resume_request_record import (
        ResumeRequestRecordError,
        build_quota_resume_request,
    )
    from scripts.two_site_quota_wait_record import (
        WaitRecordError,
        validate_initial_wait_record,
    )
    from scripts.two_site_quota_wait_storage import MAX_WAIT_RECORD_BYTES
except ImportError:
    from two_site_quota_resume_claim_admission import (  # type: ignore
        ClaimAdmissionError,
        prepare_known_reset_resume_claim,
    )
    from two_site_quota_resume_claim_record import (  # type: ignore
        ResumeClaimRecordError,
        validate_known_reset_resume_claim,
    )
    from two_site_quota_resume_claim_storage import (  # type: ignore
        ClaimStorageError,
        publish_known_reset_resume_claim,
    )
    from two_site_quota_resume_request_record import (  # type: ignore
        ResumeRequestRecordError,
        build_quota_resume_request,
    )
    from two_site_quota_wait_record import (  # type: ignore
        WaitRecordError,
        validate_initial_wait_record,
    )
    from two_site_quota_wait_storage import MAX_WAIT_RECORD_BYTES  # type: ignore

__all__ = [
    "ClaimRequestError",
    "MAX_WAIT_RECORD_BYTES",
    "compose_and_publish_known_reset_resume_request_locked",
]


class ClaimRequestError(ValueError):
    """Raised when composing or publishing a quota resume claim request fails."""


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


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def compose_and_publish_known_reset_resume_request_locked(
    root: Path,
    original_controller_run_id: str,
    wait_record: object,
    expected_wait: object,
    raw_wait_bytes: bytes,
    semantic_attempt: object,
    expected_task_id: object,
    continuation_controller_run_id: object,
    resume_request_id: object,
    claimed_at_utc: object,
    timeout: object,
    now_epoch: object,
) -> dict[str, Any]:
    """Compose and publish a known-reset quota resume claim request under caller-held lock."""
    if not isinstance(root, Path):
        raise ClaimRequestError("root must be a Path")
    if type(original_controller_run_id) is not str or not original_controller_run_id:
        raise ClaimRequestError("original_controller_run_id must be a non-empty string")

    if type(raw_wait_bytes) is not bytes:
        raise ClaimRequestError(
            f"raw_wait_bytes must be bytes, got {type(raw_wait_bytes).__name__}"
        )
    if not (1 <= len(raw_wait_bytes) <= MAX_WAIT_RECORD_BYTES):
        raise ClaimRequestError(
            f"raw_wait_bytes length ({len(raw_wait_bytes)}) outside 1..{MAX_WAIT_RECORD_BYTES}"
        )

    try:
        validated_wait = validate_initial_wait_record(wait_record, expected_wait)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimRequestError(f"Wait record validation failed: {exc}") from exc

    try:
        canonical_wait_bytes = _canonical_bytes(validated_wait)
    except (ValueError, TypeError) as exc:
        raise ClaimRequestError(
            f"Validated wait cannot be canonically serialized: {exc}"
        ) from exc

    if canonical_wait_bytes != raw_wait_bytes:
        raise ClaimRequestError(
            "raw_wait_bytes does not match canonical encoding of validated wait record"
        )

    if validated_wait.get("controller_run_id") != original_controller_run_id:
        raise ClaimRequestError(
            f"validated wait controller_run_id ({validated_wait.get('controller_run_id')!r}) "
            f"does not match original_controller_run_id ({original_controller_run_id!r})"
        )

    wait_sha256 = hashlib.sha256(raw_wait_bytes).hexdigest()

    try:
        admission_result = prepare_known_reset_resume_claim(
            wait_record=validated_wait,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=expected_task_id,
            original_wait_sha256=wait_sha256,
            continuation_controller_run_id=continuation_controller_run_id,
            resume_request_id=resume_request_id,
            claimed_at_utc=claimed_at_utc,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimRequestError(f"Claim admission failed: {exc}") from exc

    intended_claim = admission_result["claim"]
    expected_claim = admission_result["expected"]

    try:
        intended_claim_bytes = _canonical_bytes(intended_claim)
    except (ValueError, TypeError) as exc:
        raise ClaimRequestError(
            f"Intended claim cannot be canonically serialized: {exc}"
        ) from exc

    claim_sha256 = hashlib.sha256(intended_claim_bytes).hexdigest()

    try:
        validated_request = build_quota_resume_request(
            claim=intended_claim,
            expected_claim=expected_claim,
            expected_claim_sha256=claim_sha256,
            timeout=timeout,
            now_epoch=now_epoch,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimRequestError(f"Quota resume request build failed: {exc}") from exc

    try:
        published_claim = publish_known_reset_resume_claim(
            root,
            original_controller_run_id,
            intended_claim,
            expected_claim,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimRequestError(f"Claim publication failed: {exc}") from exc

    try:
        validated_published_claim = validate_known_reset_resume_claim(
            published_claim,
            expected_claim,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ClaimRequestError(f"Published claim validation failed: {exc}") from exc

    try:
        published_canonical_bytes = _canonical_bytes(validated_published_claim)
    except (ValueError, TypeError) as exc:
        raise ClaimRequestError(
            f"Published claim cannot be canonically serialized: {exc}"
        ) from exc

    if published_canonical_bytes != intended_claim_bytes:
        raise ClaimRequestError("Published claim canonical bytes mismatch intended claim")

    if not _type_sensitive_equal(validated_published_claim, intended_claim):
        raise ClaimRequestError("Published claim structure mismatch intended claim")

    return {
        "claim": copy.deepcopy(validated_published_claim),
        "expected_claim": copy.deepcopy(expected_claim),
        "claim_sha256": claim_sha256,
        "request": copy.deepcopy(validated_request),
        "original_wait_sha256": wait_sha256,
    }
