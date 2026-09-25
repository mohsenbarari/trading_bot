"""Pure projection and cross-source equality verification for initial quota wait state."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from scripts.two_site_provider_broker import (
    MAX_QUOTA_STDOUT_BYTES,
    MAX_RECORD_BYTES,
    PIPELINE_QUOTA_KEYS,
    QUOTA_ATTEMPT_KEYS,
    RECEIPT_KEYS,
    REQUEST_KEYS,
    RESPONSE_KEYS,
    unique_json,
)
from scripts.two_site_quota_wait_record import (
    _EXPECTED_KEYS,
    _REPOSITORY_KEYS,
    _RESERVATION_KEYS,
)

__all__ = ["WaitExpectedError", "derive_initial_wait_expected"]

_RESERVATION_RECORD_KEYS = frozenset({
    "schema_version",
    "controller_run_id",
    "request_id",
    "reservation",
    "request_sha256",
    "created_at",
})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_RESCOPE_EPOCH_RE = re.compile(r"^rescope-[0-9a-f]{64}$")
_CANONICAL_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WaitExpectedError(ValueError):
    """Raised when projecting initial quota wait expected state fails."""


def _type_sensitive_equal(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        for k, v in a.items():
            if not _type_sensitive_equal(v, b[k]):
                return False
        return True
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if not _type_sensitive_equal(x, y):
                return False
        return True
    return a == b


def derive_initial_wait_expected(
    reservation_record: Any,
    terminal_receipt: Any,
    terminal_receipt_bytes: Any,
    repository: Any,
    rescope_epoch: Any,
    rescope_attempt: Any,
) -> dict[str, Any]:
    """Derive the 12-field expected dictionary from validated sources."""
    if type(rescope_epoch) is not str or not _RESCOPE_EPOCH_RE.fullmatch(rescope_epoch):
        raise WaitExpectedError(f"invalid rescope_epoch: {rescope_epoch!r}")
    if type(rescope_attempt) is not int or not (1 <= rescope_attempt <= 3):
        raise WaitExpectedError(f"rescope_attempt must be int in range 1..3: {rescope_attempt!r}")

    if type(reservation_record) is not dict or set(reservation_record.keys()) != _RESERVATION_RECORD_KEYS:
        raise WaitExpectedError("reservation_record does not match exact six-key contract")
    if reservation_record.get("schema_version") != "1.0" or type(reservation_record.get("schema_version")) is not str:
        raise WaitExpectedError("reservation_record schema_version must be '1.0'")

    res = reservation_record.get("reservation")
    if type(res) is not dict or set(res.keys()) != _RESERVATION_KEYS:
        raise WaitExpectedError("reservation must contain exact seven reservation keys")
    for k in _RESERVATION_KEYS:
        if type(res[k]) is not str:
            raise WaitExpectedError(f"reservation field {k} must be str")

    ctrl_run_id = reservation_record.get("controller_run_id")
    if res.get("controller_run_id") != ctrl_run_id:
        raise WaitExpectedError("reservation controller_run_id mismatch")

    req_id = reservation_record.get("request_id")
    if type(req_id) is not str or not _REQUEST_ID_RE.fullmatch(req_id):
        raise WaitExpectedError(f"invalid reservation_record request_id: {req_id!r}")

    if not isinstance(terminal_receipt_bytes, bytes):
        raise WaitExpectedError("terminal_receipt_bytes must be bytes")
    if len(terminal_receipt_bytes) > MAX_RECORD_BYTES:
        raise WaitExpectedError("terminal_receipt_bytes exceeds MAX_RECORD_BYTES")

    if type(terminal_receipt) is not dict or set(terminal_receipt.keys()) != RECEIPT_KEYS:
        raise WaitExpectedError("terminal_receipt does not match exact seven-key contract")
    if terminal_receipt.get("schema_version") != "1.0" or type(terminal_receipt.get("schema_version")) is not str:
        raise WaitExpectedError("terminal_receipt schema_version must be '1.0'")

    try:
        canonical_receipt_bytes = json.dumps(
            terminal_receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise WaitExpectedError(f"cannot canonically serialize terminal_receipt: {exc}") from exc
    if canonical_receipt_bytes != terminal_receipt_bytes:
        raise WaitExpectedError("terminal_receipt_bytes is not canonical compact UTF-8 serialization of receipt")

    receipt_sha256 = hashlib.sha256(terminal_receipt_bytes).hexdigest()

    request = terminal_receipt.get("request")
    if type(request) is not dict or set(request.keys()) != REQUEST_KEYS:
        raise WaitExpectedError("broker request does not match exact contract")
    if request.get("schema_version") != "1.0" or type(request.get("schema_version")) is not str:
        raise WaitExpectedError("broker request schema_version must be '1.0'")
    if request.get("request_id") != req_id:
        raise WaitExpectedError("request request_id does not match reservation")

    response = terminal_receipt.get("response")
    if type(response) is not dict or set(response.keys()) != RESPONSE_KEYS:
        raise WaitExpectedError("broker response does not match exact contract")
    if response.get("schema_version") != "1.0" or type(response.get("schema_version")) is not str:
        raise WaitExpectedError("broker response schema_version must be '1.0'")
    if response.get("request_id") != req_id:
        raise WaitExpectedError("response request_id does not match request_id")
    if response.get("run_prefix") != request.get("run_prefix"):
        raise WaitExpectedError("response run_prefix does not match request")
    if response.get("source_head") != request.get("source_head"):
        raise WaitExpectedError("response source_head does not match request")

    try:
        req_canonical = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        resp_canonical = json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise WaitExpectedError(f"cannot canonically serialize request or response: {exc}") from exc

    computed_req_sha = hashlib.sha256(req_canonical).hexdigest()
    computed_resp_sha = hashlib.sha256(resp_canonical).hexdigest()

    if terminal_receipt.get("request_sha256") != computed_req_sha:
        raise WaitExpectedError("terminal_receipt request_sha256 does not match canonical request SHA")
    if reservation_record.get("request_sha256") != computed_req_sha:
        raise WaitExpectedError("reservation_record request_sha256 does not match canonical request SHA")
    if terminal_receipt.get("response_sha256") != computed_resp_sha:
        raise WaitExpectedError("terminal_receipt response_sha256 does not match canonical response SHA")

    for k in _RESERVATION_KEYS:
        if not _type_sensitive_equal(res[k], request.get(k)):
            raise WaitExpectedError(f"reservation field {k!r} does not match request field")

    rec_created = reservation_record.get("created_at")
    req_created = request.get("created_at")
    if not _type_sensitive_equal(rec_created, req_created):
        raise WaitExpectedError("reservation_record created_at does not match request created_at")
    if isinstance(req_created, bool) or not isinstance(req_created, (int, float)) or not math.isfinite(req_created):
        raise WaitExpectedError("created_at must be finite float or int and not bool")

    if response.get("state") != "COMPLETED" or type(response.get("state")) is not str:
        raise WaitExpectedError("response state must be 'COMPLETED'")
    if type(response.get("exit_code")) is not int or response.get("exit_code") != 1:
        raise WaitExpectedError("response exit_code must be int 1")
    if response.get("stderr") != "" or type(response.get("stderr")) is not str:
        raise WaitExpectedError("response stderr must be empty string")

    raw_stdout = response.get("stdout")
    if not isinstance(raw_stdout, str):
        raise WaitExpectedError("response stdout must be string")
    stdout_bytes = raw_stdout.encode("utf-8")
    if len(stdout_bytes) > MAX_QUOTA_STDOUT_BYTES or not raw_stdout.strip():
        raise WaitExpectedError("response stdout is invalid or exceeds MAX_QUOTA_STDOUT_BYTES")

    try:
        payload = unique_json(raw_stdout)
    except Exception as exc:
        raise WaitExpectedError(f"response stdout is not valid unique json: {exc}") from exc
    if type(payload) is not dict or set(payload.keys()) != PIPELINE_QUOTA_KEYS:
        raise WaitExpectedError("stdout payload does not match PIPELINE_QUOTA_KEYS")

    if payload.get("state") != "TASK_BLOCKED":
        raise WaitExpectedError("pipeline state must be 'TASK_BLOCKED'")
    if payload.get("blockers") != ["observational quota, no replay authority"]:
        raise WaitExpectedError("pipeline blockers mismatch")
    if payload.get("source_head") != request.get("source_head"):
        raise WaitExpectedError("pipeline source_head does not match request")

    stf = payload.get("semantic_task_fingerprint")
    if type(stf) is not str or not _SHA256_RE.fullmatch(stf):
        raise WaitExpectedError("semantic_task_fingerprint must be 64 lowercase hex")

    qe = payload.get("quota_evidence")
    if not _type_sensitive_equal(qe, terminal_receipt.get("quota_evidence")):
        raise WaitExpectedError("pipeline quota_evidence does not match terminal_receipt")

    qa = payload.get("quota_attempt")
    if type(qa) is not dict or set(qa.keys()) != QUOTA_ATTEMPT_KEYS:
        raise WaitExpectedError("quota_attempt does not match QUOTA_ATTEMPT_KEYS")
    if qa.get("role") not in ("worker", "verifier") or type(qa.get("role")) is not str:
        raise WaitExpectedError("quota_attempt role must be 'worker' or 'verifier'")
    run_id = qa.get("run_id")
    if type(run_id) is not str or not _CANONICAL_RUN_ID_RE.fullmatch(run_id):
        raise WaitExpectedError(f"quota_attempt run_id is invalid: {run_id!r}")
    status_sha = qa.get("status_sha256")
    if type(status_sha) is not str or not _SHA256_RE.fullmatch(status_sha):
        raise WaitExpectedError("quota_attempt status_sha256 must be 64 lowercase hex")

    if type(repository) is not dict or set(repository.keys()) != _REPOSITORY_KEYS:
        raise WaitExpectedError("repository does not match exact five-key shape")
    if repository.get("head") != request.get("source_head"):
        raise WaitExpectedError("repository head does not match request source_head")
    if repository.get("status") != "" or type(repository.get("status")) is not str:
        raise WaitExpectedError("repository status must be empty string")

    scope = repository.get("assignment_scope")
    if type(scope) is not dict:
        raise WaitExpectedError("repository assignment_scope must be dict")
    if scope.get("assignment_id") != res.get("assignment_id"):
        raise WaitExpectedError("assignment_scope assignment_id does not match reservation")
    if scope.get("sha256") != res.get("assignment_sha256"):
        raise WaitExpectedError("assignment_scope sha256 does not match reservation")

    result = {
        "controller_run_id": copy.deepcopy(ctrl_run_id),
        "request_id": copy.deepcopy(req_id),
        "reservation": copy.deepcopy(res),
        "request_sha256": copy.deepcopy(computed_req_sha),
        "terminal_receipt_sha256": copy.deepcopy(receipt_sha256),
        "response_sha256": copy.deepcopy(computed_resp_sha),
        "semantic_task_fingerprint": copy.deepcopy(stf),
        "rescope_epoch": copy.deepcopy(rescope_epoch),
        "rescope_attempt": copy.deepcopy(rescope_attempt),
        "quota_attempt": copy.deepcopy(qa),
        "quota_evidence": copy.deepcopy(qe),
        "repository": copy.deepcopy(repository),
    }
    if set(result.keys()) != _EXPECTED_KEYS:
        raise WaitExpectedError("projected result keys do not match _EXPECTED_KEYS")

    return result
