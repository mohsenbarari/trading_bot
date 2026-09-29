"""Read-only upstream lineage collector for two-site final acceptance."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from scripts.two_site_provider_broker import (
    CYCLE_RUN_RE,
    read_cycle_reservation,
)
from scripts.two_site_quota_continuation_terminal_storage import (
    read_terminal_continuation_record,
)
from scripts.two_site_quota_resume_claim_storage import (
    read_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_request_record import (
    validate_quota_resume_request,
)
from scripts.two_site_quota_resume_submission_intent import (
    compute_canonical_request_sha256,
    read_quota_resume_submission_intent,
)
from scripts.two_site_quota_wait_storage import (
    read_initial_wait_record,
    read_initial_wait_record_bytes,
)

__all__ = [
    "FinalAcceptanceLineageError",
    "collect_final_acceptance_upstream_lineage_locked",
]

_RESERVATION_FIELDS = (
    "controller_run_id",
    "run_prefix",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
)


class FinalAcceptanceLineageError(ValueError):
    """Raised when upstream lineage collection, cross-binding, or validation fails."""


def _canonical_json_bytes(payload: Any) -> bytes:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"record cannot be serialized to canonical JSON: {exc}"
        ) from exc


def collect_final_acceptance_upstream_lineage_locked(
    root: Path,
    original_controller_run_id: str,
    expected_wait: dict,
    expected_claim: dict,
    expected_request: dict,
) -> dict[str, Any]:
    """Collect and cross-bind read-only upstream lineage for final acceptance.

    The caller MUST hold the Supervisor lock and independently obtain expected_*
    identities before calling this function. This module never infers expected
    identities from the records it validates.
    """
    if not isinstance(root, Path):
        raise FinalAcceptanceLineageError("root must be a Path")
    if type(original_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        original_controller_run_id
    ):
        raise FinalAcceptanceLineageError(
            f"original_controller_run_id must match cycle run pattern: {original_controller_run_id!r}"
        )
    if type(expected_wait) is not dict:
        raise FinalAcceptanceLineageError("expected_wait must be dict")
    if type(expected_claim) is not dict:
        raise FinalAcceptanceLineageError("expected_claim must be dict")
    if type(expected_request) is not dict:
        raise FinalAcceptanceLineageError("expected_request must be dict")

    # 1. Read existing Broker reservation
    try:
        reservation_record = read_cycle_reservation(root, original_controller_run_id)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to read cycle reservation for {original_controller_run_id}: {exc}"
        ) from exc

    persisted_reservation = reservation_record.get("reservation")
    if type(persisted_reservation) is not dict:
        raise FinalAcceptanceLineageError("reservation record missing valid reservation dictionary")

    # 2. Read initial wait raw bytes and validated record
    try:
        raw_wait_bytes = read_initial_wait_record_bytes(root, original_controller_run_id)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to read initial wait bytes for {original_controller_run_id}: {exc}"
        ) from exc

    wait_sha256 = hashlib.sha256(raw_wait_bytes).hexdigest()

    try:
        wait_record = read_initial_wait_record(
            root, original_controller_run_id, expected_wait
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to read initial wait record for {original_controller_run_id}: {exc}"
        ) from exc

    canonical_wait_bytes = _canonical_json_bytes(wait_record)
    if raw_wait_bytes != canonical_wait_bytes:
        raise FinalAcceptanceLineageError(
            "raw initial wait bytes do not match canonical encoding of validated wait record"
        )

    # 3. Compare persisted reservation request ID and seven fields with independently validated wait
    if wait_record.get("request_id") != reservation_record.get("request_id"):
        raise FinalAcceptanceLineageError(
            f"request_id mismatch between wait ({wait_record.get('request_id')!r}) "
            f"and reservation ({reservation_record.get('request_id')!r})"
        )

    wait_reservation = wait_record.get("reservation")
    if type(wait_reservation) is not dict:
        raise FinalAcceptanceLineageError("wait record missing valid reservation dictionary")

    for field in _RESERVATION_FIELDS:
        if wait_reservation.get(field) != persisted_reservation.get(field):
            raise FinalAcceptanceLineageError(
                f"reservation field {field!r} mismatch between wait "
                f"({wait_reservation.get(field)!r}) and broker ({persisted_reservation.get(field)!r})"
            )

    if wait_record.get("request_sha256") != reservation_record.get("request_sha256"):
        raise FinalAcceptanceLineageError(
            f"request_sha256 mismatch between wait and broker reservation: "
            f"{wait_record.get('request_sha256')!r} != {reservation_record.get('request_sha256')!r}"
        )

    # 4. Bind wait SHA to expected_claim/expected_request
    if expected_claim.get("original_wait_sha256") != wait_sha256:
        raise FinalAcceptanceLineageError(
            f"expected_claim original_wait_sha256 mismatch: "
            f"{expected_claim.get('original_wait_sha256')!r} != {wait_sha256!r}"
        )
    if expected_request.get("original_wait_sha256") != wait_sha256:
        raise FinalAcceptanceLineageError(
            f"expected_request original_wait_sha256 mismatch: "
            f"{expected_request.get('original_wait_sha256')!r} != {wait_sha256!r}"
        )

    # 5. Read known-reset resume claim
    try:
        claim_record = read_known_reset_resume_claim(
            root, original_controller_run_id, expected_claim
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to read known-reset resume claim for {original_controller_run_id}: {exc}"
        ) from exc

    # Bind all claim fields derived from wait/reservation/schedule/role/task/assignment to independent expected and persisted claim
    derived_claim_bindings: dict[str, Any] = {
        "original_controller_run_id": original_controller_run_id,
        "original_request_id": wait_record["request_id"],
        "original_wait_sha256": wait_sha256,
        "semantic_task_fingerprint": wait_record["semantic_task_fingerprint"],
        "original_run_prefix": persisted_reservation["run_prefix"],
        "task_path": persisted_reservation["task_path"],
        "task_sha256": persisted_reservation["task_sha256"],
        "source_head": persisted_reservation["source_head"],
        "assignment_id": persisted_reservation["assignment_id"],
        "assignment_sha256": persisted_reservation["assignment_sha256"],
        "role": wait_record["quota_attempt"]["role"],
        "reset_kind": wait_record["schedule"]["reset_kind"],
        "next_check_at_utc": wait_record["schedule"]["next_check_at_utc"],
        "resume_run_prefix": f"qresume-{wait_record['request_id']}",
    }

    for key, expected_val in derived_claim_bindings.items():
        if expected_claim.get(key) != expected_val:
            raise FinalAcceptanceLineageError(
                f"expected_claim field {key!r} ({expected_claim.get(key)!r}) "
                f"does not match lineage derived value ({expected_val!r})"
            )
        if claim_record.get(key) != expected_val:
            raise FinalAcceptanceLineageError(
                f"claim_record field {key!r} ({claim_record.get(key)!r}) "
                f"does not match lineage derived value ({expected_val!r})"
            )

    # 6. Bind claim SHA to request
    claim_canonical_bytes = _canonical_json_bytes(claim_record)
    claim_sha256 = hashlib.sha256(claim_canonical_bytes).hexdigest()

    if expected_request.get("claim_sha256") != claim_sha256:
        raise FinalAcceptanceLineageError(
            f"expected_request claim_sha256 mismatch: "
            f"{expected_request.get('claim_sha256')!r} != {claim_sha256!r}"
        )

    # 7. Validate full historical request vs claim with validate_quota_resume_request using request.created_at as historical now_epoch, not wall time
    req_created_at = expected_request.get("created_at")
    if (
        req_created_at is None
        or type(req_created_at) is bool
        or not isinstance(req_created_at, (int, float))
        or not math.isfinite(req_created_at)
    ):
        raise FinalAcceptanceLineageError(
            f"expected_request created_at must be finite numeric, got {req_created_at!r}"
        )

    try:
        validate_quota_resume_request(
            request=expected_request,
            claim=claim_record,
            expected_claim=expected_claim,
            expected_claim_sha256=claim_sha256,
            now_epoch=req_created_at,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"historical request validation against claim failed: {exc}"
        ) from exc

    # 8. Read submission intent and receipt against exact expected_request, verify canonical SHA and response linkage
    try:
        expected_request_sha256 = compute_canonical_request_sha256(expected_request)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to compute canonical request SHA for expected_request: {exc}"
        ) from exc

    try:
        submission_intent = read_quota_resume_submission_intent(
            root, original_controller_run_id, expected_request
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to read submission intent for {original_controller_run_id}: {exc}"
        ) from exc

    if submission_intent.get("request_sha256") != expected_request_sha256:
        raise FinalAcceptanceLineageError(
            f"submission intent request_sha256 mismatch: "
            f"{submission_intent.get('request_sha256')!r} != {expected_request_sha256!r}"
        )
    if submission_intent.get("request_id") != expected_request.get("request_id"):
        raise FinalAcceptanceLineageError(
            f"submission intent request_id mismatch: "
            f"{submission_intent.get('request_id')!r} != {expected_request.get('request_id')!r}"
        )

    intent_canonical_bytes = _canonical_json_bytes(submission_intent)
    intent_sha256 = hashlib.sha256(intent_canonical_bytes).hexdigest()

    resume_request_id = expected_request.get("request_id")
    if not isinstance(resume_request_id, str):
        raise FinalAcceptanceLineageError("expected_request missing valid request_id")

    try:
        terminal_receipt = read_terminal_continuation_record(
            root, resume_request_id, expected_request=expected_request
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceLineageError(
            f"failed to read terminal continuation record for request {resume_request_id}: {exc}"
        ) from exc

    if terminal_receipt.get("request_sha256") != expected_request_sha256:
        raise FinalAcceptanceLineageError(
            f"terminal receipt request_sha256 mismatch: "
            f"{terminal_receipt.get('request_sha256')!r} != {expected_request_sha256!r}"
        )
    if terminal_receipt.get("submission_intent_sha256") != intent_sha256:
        raise FinalAcceptanceLineageError(
            f"terminal receipt submission_intent_sha256 mismatch: "
            f"{terminal_receipt.get('submission_intent_sha256')!r} != {intent_sha256!r}"
        )

    receipt_canonical_bytes = _canonical_json_bytes(terminal_receipt)
    receipt_sha256 = hashlib.sha256(receipt_canonical_bytes).hexdigest()

    response = terminal_receipt.get("response")
    if type(response) is not dict:
        raise FinalAcceptanceLineageError("terminal receipt missing valid response dict")

    resp_canonical_bytes = _canonical_json_bytes(response)
    resp_sha256 = hashlib.sha256(resp_canonical_bytes).hexdigest()

    if terminal_receipt.get("response_sha256") != resp_sha256:
        raise FinalAcceptanceLineageError(
            f"terminal receipt response_sha256 mismatch: "
            f"{terminal_receipt.get('response_sha256')!r} != {resp_sha256!r}"
        )

    resp_state = response.get("state")
    if resp_state != "COMPLETED":
        raise FinalAcceptanceLineageError(
            f"response state must be 'COMPLETED', got {resp_state!r}"
        )

    exit_code = response.get("exit_code")
    if type(exit_code) is not int or type(exit_code) is bool or exit_code not in (0, 1):
        raise FinalAcceptanceLineageError(
            f"response exit_code must be int 0 or 1, got {exit_code!r}"
        )

    if response.get("request_id") != resume_request_id:
        raise FinalAcceptanceLineageError(
            f"response request_id mismatch: {response.get('request_id')!r} != {resume_request_id!r}"
        )

    # 9. Return bounded exact IDs/digests and response state/exit code only
    return {
        "original_controller_run_id": original_controller_run_id,
        "continuation_controller_run_id": claim_record["continuation_controller_run_id"],
        "original_request_id": wait_record["request_id"],
        "resume_request_id": resume_request_id,
        "task_id": claim_record["task_id"],
        "assignment_id": claim_record["assignment_id"],
        "source_head": claim_record["source_head"],
        "task_sha256": claim_record["task_sha256"],
        "assignment_sha256": claim_record["assignment_sha256"],
        "semantic_task_fingerprint": claim_record["semantic_task_fingerprint"],
        "original_wait_sha256": wait_sha256,
        "claim_sha256": claim_sha256,
        "request_sha256": expected_request_sha256,
        "submission_intent_sha256": intent_sha256,
        "terminal_receipt_sha256": receipt_sha256,
        "response_sha256": resp_sha256,
        "response_state": resp_state,
        "exit_code": exit_code,
    }
