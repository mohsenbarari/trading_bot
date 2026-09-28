"""Pure terminal continuation record codec for two-site quota continuation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

from scripts.two_site_provider_broker import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    BrokerError,
    validate_response,
)
from scripts.two_site_quota_resume_submission_intent import (
    compute_canonical_request_sha256,
    validate_quota_resume_submission_record,
)

MAX_TERMINAL_CONTINUATION_RECORD_BYTES: int = (
    MAX_RESPONSE_BYTES + MAX_REQUEST_BYTES + 16_384
)

_RECORD_KEYS = frozenset({
    "schema_version", "request_id", "request", "request_sha256",
    "response", "response_sha256", "submission_intent_sha256",
})


class TerminalContinuationRecordError(BrokerError):
    """Raised when continuation terminal record validation or encoding fails."""


__all__ = [
    "MAX_TERMINAL_CONTINUATION_RECORD_BYTES",
    "TerminalContinuationRecordError",
    "build_terminal_continuation_record",
    "validate_terminal_continuation_record",
]


def _canonical_bytes(obj: Any) -> bytes:
    try:
        return json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TerminalContinuationRecordError(f"cannot serialize to canonical JSON: {exc}") from exc


def _validate_stdout(stdout_text: str, exit_code: int) -> None:
    def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k, v in pairs:
            if k in res:
                raise TerminalContinuationRecordError(f"duplicate stdout key: {k!r}")
            res[k] = v
        return res

    try:
        payload = json.loads(stdout_text, object_pairs_hook=_pairs_hook)
    except TerminalContinuationRecordError:
        raise
    except Exception as exc:
        raise TerminalContinuationRecordError(f"stdout is not valid JSON: {exc}") from exc

    if type(payload) is not dict or _canonical_bytes(payload) != stdout_text.encode("utf-8"):
        raise TerminalContinuationRecordError("stdout payload must be canonical unique JSON dict")

    expected_state = "READY_FOR_CODEX" if exit_code == 0 else "TASK_BLOCKED"
    if payload.get("state") != expected_state:
        raise TerminalContinuationRecordError(
            f"stdout state {payload.get('state')!r} mismatch for exit_code {exit_code}"
        )


def _validate_request(request: Any) -> tuple[bytes, str]:
    try:
        req_sha = compute_canonical_request_sha256(request)
    except Exception as exc:
        raise TerminalContinuationRecordError(f"request validation failed: {exc}") from exc
    req_bytes = _canonical_bytes(request)
    if len(req_bytes) > MAX_REQUEST_BYTES:
        raise TerminalContinuationRecordError(f"request exceeds MAX_REQUEST_BYTES ({MAX_REQUEST_BYTES})")
    return req_bytes, req_sha


def _validate_response(request: dict[str, Any], response: Any) -> tuple[bytes, str]:
    try:
        validate_response(request, response)
    except Exception as exc:
        raise TerminalContinuationRecordError(f"broker response validation failed: {exc}") from exc

    if response.get("state") != "COMPLETED":
        raise TerminalContinuationRecordError(f"response state must be 'COMPLETED', got {response.get('state')!r}")
    exit_code = response.get("exit_code")
    if type(exit_code) is not int or exit_code not in (0, 1):
        raise TerminalContinuationRecordError(f"response exit_code must be 0 or 1, got {exit_code!r}")
    finished_at = response.get("finished_at")
    if type(finished_at) is bool or not isinstance(finished_at, (int, float)) or not math.isfinite(finished_at):
        raise TerminalContinuationRecordError(f"response finished_at must be finite nonbool numeric, got {finished_at!r}")
    if response.get("stderr") != "":
        raise TerminalContinuationRecordError(f"response stderr must be empty, got {response.get('stderr')!r}")

    _validate_stdout(response["stdout"], exit_code)
    resp_bytes = _canonical_bytes(response)
    if len(resp_bytes) > MAX_RESPONSE_BYTES:
        raise TerminalContinuationRecordError(f"response exceeds MAX_RESPONSE_BYTES ({MAX_RESPONSE_BYTES})")
    return resp_bytes, hashlib.sha256(resp_bytes).hexdigest()


def _validate_intent(intent: Any, request: dict[str, Any]) -> tuple[bytes, str]:
    try:
        validate_quota_resume_submission_record(intent, expected_request=request)
    except Exception as exc:
        raise TerminalContinuationRecordError(f"submission intent validation failed: {exc}") from exc
    intent_bytes = _canonical_bytes(intent)
    return intent_bytes, hashlib.sha256(intent_bytes).hexdigest()


def build_terminal_continuation_record(
    request: dict[str, Any],
    response: dict[str, Any],
    submission_intent: dict[str, Any],
) -> dict[str, Any]:
    """Build and self-validate a version 1.0 terminal continuation record."""
    _req_bytes, req_sha = _validate_request(request)
    _resp_bytes, resp_sha = _validate_response(request, response)
    _intent_bytes, intent_sha = _validate_intent(submission_intent, request)
    return validate_terminal_continuation_record(
        {
            "schema_version": "1.0",
            "request_id": request.get("request_id"),
            "request": request,
            "request_sha256": req_sha,
            "response": response,
            "response_sha256": resp_sha,
            "submission_intent_sha256": intent_sha,
        },
        expected_intent=submission_intent,
        expected_request=request,
    )


def validate_terminal_continuation_record(
    record: object,
    expected_intent: object,
    expected_request: object = None,
) -> dict[str, Any]:
    """Validate a terminal continuation record and return a detached deep copy."""
    if type(record) is not dict or set(record.keys()) != _RECORD_KEYS:
        raise TerminalContinuationRecordError("record must be dict with exact seven keys")
    if type(record["schema_version"]) is not str or record["schema_version"] != "1.0":
        raise TerminalContinuationRecordError(f"record schema_version must be '1.0', got {record['schema_version']!r}")

    request = record["request"]
    req_bytes, req_sha = _validate_request(request)

    request_id = record["request_id"]
    if type(request_id) is not str or request_id != request.get("request_id"):
        raise TerminalContinuationRecordError(f"record request_id mismatch: {request_id!r} != {request.get('request_id')!r}")
    if record["request_sha256"] != req_sha:
        raise TerminalContinuationRecordError(f"record request_sha256 mismatch: {record['request_sha256']!r} != {req_sha!r}")

    response = record["response"]
    _resp_bytes, resp_sha = _validate_response(request, response)
    if record["response_sha256"] != resp_sha:
        raise TerminalContinuationRecordError(f"record response_sha256 mismatch: {record['response_sha256']!r} != {resp_sha!r}")

    _intent_bytes, intent_sha = _validate_intent(expected_intent, request)
    if record["submission_intent_sha256"] != intent_sha:
        raise TerminalContinuationRecordError(
            f"record submission_intent_sha256 mismatch: {record['submission_intent_sha256']!r} != {intent_sha!r}"
        )

    if expected_request is not None:
        if _canonical_bytes(expected_request) != req_bytes:
            raise TerminalContinuationRecordError("record request does not match expected_request")

    rec_bytes = _canonical_bytes(record)
    if len(rec_bytes) > MAX_TERMINAL_CONTINUATION_RECORD_BYTES:
        raise TerminalContinuationRecordError(f"record size exceeds limit ({MAX_TERMINAL_CONTINUATION_RECORD_BYTES})")

    return copy.deepcopy(record)
