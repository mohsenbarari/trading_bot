"""Pure strict observational validator for two-site READY_FOR_CODEX continuation records."""

from __future__ import annotations

import json
import re
from typing import Any

try:
    from scripts.two_site_provider_broker import (
        MAX_PIPELINE_OUTPUT_BYTES,
        RUN_PREFIX_RE,
        BrokerError,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        MAX_PIPELINE_OUTPUT_BYTES,
        RUN_PREFIX_RE,
        BrokerError,
    )

try:
    from scripts.two_site_quota_continuation_terminal_record import (
        validate_terminal_continuation_record,
    )
except ImportError:
    from two_site_quota_continuation_terminal_record import (  # type: ignore
        validate_terminal_continuation_record,
    )

if RUN_PREFIX_RE is None or not hasattr(RUN_PREFIX_RE, "fullmatch"):
    RUN_PREFIX_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,60}$")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")

_READY_PAYLOAD_KEYS = frozenset({
    "state",
    "task_id",
    "source_head",
    "final_worker_run_id",
    "final_reviewer_run_id",
    "proposal_sha256",
    "changed_paths",
    "proposal_path",
    "reviewer_path",
    "correction_count",
    "blockers",
    "pre_review_test_path",
    "pre_review_test_sha256",
    "pre_review_test_state",
    "semantic_task_fingerprint",
})


class QuotaReadyPayloadError(BrokerError):
    """Raised when READY_FOR_CODEX record or payload validation fails."""


ReadyPayloadError = QuotaReadyPayloadError


def _validate_safe_posix_path(p: Any, field_name: str) -> None:
    if type(p) is not str:
        raise QuotaReadyPayloadError(
            f"{field_name} entry must be str, got {type(p).__name__}"
        )
    if not p:
        raise QuotaReadyPayloadError(f"{field_name} entry cannot be empty")
    if "\0" in p:
        raise QuotaReadyPayloadError(f"{field_name} entry contains NUL byte: {p!r}")
    if "\\" in p:
        raise QuotaReadyPayloadError(f"{field_name} entry contains backslash: {p!r}")
    if p.startswith("/"):
        raise QuotaReadyPayloadError(f"{field_name} entry cannot be absolute: {p!r}")
    parts = p.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise QuotaReadyPayloadError(
                f"{field_name} entry contains invalid segment {part!r}: {p!r}"
            )


def _validate_allowed_output_paths(allowed_output_paths: Any) -> list[str]:
    if type(allowed_output_paths) not in (list, tuple):
        raise QuotaReadyPayloadError(
            f"allowed_output_paths must be list or tuple, got {type(allowed_output_paths).__name__}"
        )
    if not (1 <= len(allowed_output_paths) <= 32):
        raise QuotaReadyPayloadError(
            f"allowed_output_paths length must be 1..32, got {len(allowed_output_paths)}"
        )
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in allowed_output_paths:
        _validate_safe_posix_path(item, "allowed_output_paths")
        if item in seen:
            raise QuotaReadyPayloadError(
                f"allowed_output_paths contains duplicate: {item!r}"
            )
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _validate_changed_paths(
    changed_paths: Any, allowed_set: set[str]
) -> list[str]:
    if type(changed_paths) is not list:
        raise QuotaReadyPayloadError(
            f"changed_paths must be list, got {type(changed_paths).__name__}"
        )
    if not (1 <= len(changed_paths) <= 32):
        raise QuotaReadyPayloadError(
            f"changed_paths length must be 1..32, got {len(changed_paths)}"
        )
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in changed_paths:
        _validate_safe_posix_path(item, "changed_paths")
        if item in seen:
            raise QuotaReadyPayloadError(
                f"changed_paths contains duplicate: {item!r}"
            )
        if item not in allowed_set:
            raise QuotaReadyPayloadError(
                f"changed_paths entry {item!r} not in allowed_output_paths"
            )
        seen.add(item)
        cleaned.append(item)
    return cleaned


def validate_quota_ready_payload(
    terminal_record: object,
    expected_intent: object,
    expected_request: object,
    expected_task_id: object,
    allowed_output_paths: object,
) -> dict[str, Any]:
    """Pure observational validator for two-site READY_FOR_CODEX continuation records."""
    if type(expected_task_id) is not str or not expected_task_id:
        raise QuotaReadyPayloadError("expected_task_id must be non-empty str")
    if type(expected_request) is not dict:
        raise QuotaReadyPayloadError("expected_request must be dict")

    allowed_paths_list = _validate_allowed_output_paths(allowed_output_paths)
    allowed_set = set(allowed_paths_list)

    try:
        validated_record = validate_terminal_continuation_record(
            terminal_record,
            expected_intent=expected_intent,
            expected_request=expected_request,
        )
    except BrokerError as exc:
        raise QuotaReadyPayloadError(
            f"terminal continuation record validation failed: {exc}"
        ) from exc
    except Exception as exc:
        raise QuotaReadyPayloadError(
            f"terminal continuation record validation failed: {exc}"
        ) from exc

    response = validated_record["response"]
    if response.get("state") != "COMPLETED":
        raise QuotaReadyPayloadError(
            f"response state must be 'COMPLETED', got {response.get('state')!r}"
        )
    exit_code = response.get("exit_code")
    if type(exit_code) is not int or type(exit_code) is bool or exit_code != 0:
        raise QuotaReadyPayloadError(
            f"exit_code must be exact int 0, got {exit_code!r}"
        )
    if response.get("stderr") != "":
        raise QuotaReadyPayloadError(
            f"response stderr must be empty, got {response.get('stderr')!r}"
        )

    stdout_text = response.get("stdout")
    if type(stdout_text) is not str:
        raise QuotaReadyPayloadError("response stdout must be str")
    stdout_bytes = stdout_text.encode("utf-8")
    if len(stdout_bytes) > MAX_PIPELINE_OUTPUT_BYTES:
        raise QuotaReadyPayloadError(
            f"stdout size ({len(stdout_bytes)} bytes) exceeds limit "
            f"MAX_PIPELINE_OUTPUT_BYTES ({MAX_PIPELINE_OUTPUT_BYTES})"
        )

    def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k, v in pairs:
            if k in res:
                raise QuotaReadyPayloadError(f"duplicate stdout key: {k!r}")
            res[k] = v
        return res

    try:
        payload = json.loads(stdout_text, object_pairs_hook=_pairs_hook)
    except QuotaReadyPayloadError:
        raise
    except Exception as exc:
        raise QuotaReadyPayloadError(f"stdout is not valid JSON: {exc}") from exc

    if type(payload) is not dict:
        raise QuotaReadyPayloadError("stdout payload must be a JSON dict")

    canonical_stdout = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical_stdout != stdout_text:
        raise QuotaReadyPayloadError(
            "stdout payload must be canonical unique JSON dict"
        )

    if set(payload.keys()) != _READY_PAYLOAD_KEYS:
        raise QuotaReadyPayloadError(
            "payload keys mismatch with exact 15-key READY_FOR_CODEX schema"
        )

    payload_state = payload.get("state")
    if payload_state != "READY_FOR_CODEX":
        raise QuotaReadyPayloadError(
            f"payload state must be 'READY_FOR_CODEX', got {payload_state!r}"
        )

    blockers = payload.get("blockers")
    if type(blockers) is not list or blockers != []:
        raise QuotaReadyPayloadError(
            f"payload blockers must be empty list [], got {blockers!r}"
        )

    pre_review_test_state = payload.get("pre_review_test_state")
    if pre_review_test_state != "PASSED":
        raise QuotaReadyPayloadError(
            f"payload pre_review_test_state must be 'PASSED', got {pre_review_test_state!r}"
        )

    request = validated_record["request"]
    if type(request) is not dict:
        raise QuotaReadyPayloadError("record request must be dict")

    run_prefix = request.get("run_prefix")
    if type(run_prefix) is not str or not RUN_PREFIX_RE.fullmatch(run_prefix):
        raise QuotaReadyPayloadError(
            f"request run_prefix invalid: {run_prefix!r}"
        )

    payload_task_id = payload.get("task_id")
    req_task_id = request.get("task_id")
    if (
        type(payload_task_id) is not str
        or payload_task_id != expected_task_id
        or payload_task_id != req_task_id
    ):
        raise QuotaReadyPayloadError(
            f"task_id mismatch: payload={payload_task_id!r}, "
            f"expected={expected_task_id!r}, request={req_task_id!r}"
        )

    source_head = payload.get("source_head")
    req_source_head = request.get("source_head")
    if (
        type(source_head) is not str
        or not _SOURCE_HEAD_RE.fullmatch(source_head)
        or source_head != req_source_head
    ):
        raise QuotaReadyPayloadError(
            f"source_head invalid or mismatch: payload={source_head!r}, request={req_source_head!r}"
        )

    sem_fp = payload.get("semantic_task_fingerprint")
    req_sem_fp = request.get("semantic_task_fingerprint")
    if (
        type(sem_fp) is not str
        or not _SHA256_RE.fullmatch(sem_fp)
        or sem_fp != req_sem_fp
    ):
        raise QuotaReadyPayloadError(
            f"semantic_task_fingerprint invalid or mismatch: payload={sem_fp!r}, request={req_sem_fp!r}"
        )

    correction_count = payload.get("correction_count")
    if (
        type(correction_count) is not int
        or type(correction_count) is bool
        or not (0 <= correction_count <= 2)
    ):
        raise QuotaReadyPayloadError(
            f"correction_count must be exact int 0..2, got {correction_count!r}"
        )

    final_worker_run_id = payload.get("final_worker_run_id")
    if type(final_worker_run_id) is not str:
        raise QuotaReadyPayloadError(
            f"final_worker_run_id must be str, got {type(final_worker_run_id).__name__}"
        )
    worker_match = re.fullmatch(
        rf"^{re.escape(run_prefix)}-worker-{correction_count}(?:-retry-[12])?$",
        final_worker_run_id,
    )
    if not worker_match:
        raise QuotaReadyPayloadError(
            f"final_worker_run_id {final_worker_run_id!r} does not match "
            f"expected prefix {run_prefix!r} with count {correction_count}"
        )

    final_reviewer_run_id = payload.get("final_reviewer_run_id")
    if type(final_reviewer_run_id) is not str:
        raise QuotaReadyPayloadError(
            f"final_reviewer_run_id must be str, got {type(final_reviewer_run_id).__name__}"
        )
    reviewer_match = re.fullmatch(
        rf"^{re.escape(run_prefix)}(?:-reviewer-{correction_count}|-v{correction_count}r[12])(?:-retry-[12])?$",
        final_reviewer_run_id,
    )
    if not reviewer_match:
        raise QuotaReadyPayloadError(
            f"final_reviewer_run_id {final_reviewer_run_id!r} does not match "
            f"expected prefix {run_prefix!r} with count {correction_count}"
        )

    if final_worker_run_id == final_reviewer_run_id:
        raise QuotaReadyPayloadError(
            "final_worker_run_id and final_reviewer_run_id must be distinct"
        )

    expected_proposal_path = (
        f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json"
    )
    if payload.get("proposal_path") != expected_proposal_path:
        raise QuotaReadyPayloadError(
            f"proposal_path mismatch: {payload.get('proposal_path')!r} != {expected_proposal_path!r}"
        )

    expected_reviewer_path = (
        f".local/two-site-refactor/proposals/{final_reviewer_run_id}/result.json"
    )
    if payload.get("reviewer_path") != expected_reviewer_path:
        raise QuotaReadyPayloadError(
            f"reviewer_path mismatch: {payload.get('reviewer_path')!r} != {expected_reviewer_path!r}"
        )

    expected_test_path = (
        f".local/two-site-refactor/pipeline/{run_prefix}/pre-review-tests-{correction_count}.json"
    )
    if payload.get("pre_review_test_path") != expected_test_path:
        raise QuotaReadyPayloadError(
            f"pre_review_test_path mismatch: {payload.get('pre_review_test_path')!r} != {expected_test_path!r}"
        )

    proposal_sha = payload.get("proposal_sha256")
    if type(proposal_sha) is not str or not _SHA256_RE.fullmatch(proposal_sha):
        raise QuotaReadyPayloadError(
            f"proposal_sha256 must be 64 lowercase hex, got {proposal_sha!r}"
        )

    test_sha = payload.get("pre_review_test_sha256")
    if type(test_sha) is not str or not _SHA256_RE.fullmatch(test_sha):
        raise QuotaReadyPayloadError(
            f"pre_review_test_sha256 must be 64 lowercase hex, got {test_sha!r}"
        )

    validated_changed_paths = _validate_changed_paths(
        payload.get("changed_paths"), allowed_set
    )

    return {
        "status": "REVIEW_BINDING_NOT_YET_VERIFIED",
        "request_id": validated_record["request_id"],
        "run_prefix": run_prefix,
        "task_id": payload_task_id,
        "source_head": source_head,
        "semantic_task_fingerprint": sem_fp,
        "correction_count": correction_count,
        "final_worker_run_id": final_worker_run_id,
        "final_reviewer_run_id": final_reviewer_run_id,
        "proposal_path": payload["proposal_path"],
        "proposal_sha256": proposal_sha,
        "reviewer_path": payload["reviewer_path"],
        "pre_review_test_path": payload["pre_review_test_path"],
        "pre_review_test_sha256": test_sha,
        "changed_paths": validated_changed_paths,
        "response_sha256": validated_record["response_sha256"],
    }


validate_ready_for_codex_record = validate_quota_ready_payload
validate_ready_payload = validate_quota_ready_payload

__all__ = [
    "QuotaReadyPayloadError",
    "ReadyPayloadError",
    "_validate_allowed_output_paths",
    "_validate_changed_paths",
    "validate_quota_ready_payload",
    "validate_ready_for_codex_record",
    "validate_ready_payload",
]
