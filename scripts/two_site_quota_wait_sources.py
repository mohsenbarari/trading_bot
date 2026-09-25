"""Strict read-only composition of initial quota wait expected state from authoritative sources.

Caller MUST hold existing Supervisor exclusive lock. This function does NOT prove intent,
claim resume, or grant wake/replay authority. An independent strict intent gate is mandatory
before publication or resume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    rescope_epoch_basename,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_provider_broker import (
    BrokerError,
    canonical_root,
    find_terminal_quota_for_reservation,
    read_cycle_reservation,
    read_terminal_quota_bytes,
    validate_terminal_quota_payload,
)
from scripts.two_site_quota_wait_expected import (
    WaitExpectedError,
    derive_initial_wait_expected,
)

__all__ = ["WaitSourceError", "collect_initial_wait_source_expected"]


class WaitSourceError(ValueError):
    """Raised when reading or verifying initial quota wait sources fails."""


def _canonical_json_bytes(data: Any) -> bytes:
    try:
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise WaitSourceError(f"data cannot be canonically serialized: {exc}") from exc


def collect_initial_wait_source_expected(
    root: Path,
    controller_run_id: str,
    rescope_epoch: str,
    rescope_attempt: int,
) -> dict[str, Any]:
    """Collect and verify initial quota wait expected state from independent sources.

    Caller MUST hold existing Supervisor exclusive lock. This function does NOT prove
    intent, claim resume, or grant wake/replay authority. An independent strict intent gate
    is mandatory before publication or resume.

    This function performs strictly read-only composition using existing readers/validators:
    1. Resolves canonical root via Broker canonical_root.
    2. Validates clean working tree and accepted repository facts via Autopilot.
    3. Derives deterministic rescope epoch and verifies equality with expected epoch.
    4. Reads strict Broker cycle reservation record.
    5. Resolves unique terminal quota record for reservation and verifies claimed response.
    6. Reads raw terminal quota bytes for reservation request_id and validates payload.
    7. Compares find-returned response with validated receipt response canonical bytes and structure.
    8. Revalidates repository facts to guarantee invariance across Broker reads.
    9. Derives initial wait expected dictionary via derive_initial_wait_expected.
    10. Rechecks repository facts after derivation.
    """
    try:
        c_root = canonical_root(root)

        # 1. Validate current repository before reads and verify epoch identity
        repo_facts_before = validate_accepted_repository(
            validate_repository(c_root, require_clean=True)
        )
        computed_epoch = rescope_epoch_basename(repo_facts_before)
        if computed_epoch != rescope_epoch:
            raise WaitSourceError(
                f"rescope_epoch mismatch: expected {rescope_epoch!r}, computed {computed_epoch!r}"
            )

        # 2. Read cycle reservation under Supervisor run directory
        reservation_record = read_cycle_reservation(c_root, controller_run_id)
        if not isinstance(reservation_record, dict) or "reservation" not in reservation_record:
            raise WaitSourceError("broker reservation record is malformed")
        reservation = reservation_record["reservation"]
        request_id = reservation_record.get("request_id")
        if not isinstance(request_id, str):
            raise WaitSourceError("broker reservation request_id is not a string")

        # 3. Find unique terminal quota record matching reservation
        find_response = find_terminal_quota_for_reservation(c_root, reservation)

        # 4. Read raw terminal quota bytes and validate payload
        raw_bytes = read_terminal_quota_bytes(c_root, request_id)
        receipt = validate_terminal_quota_payload(c_root, request_id, raw_bytes)

        # 5. Compare find-returned response with validated receipt response
        find_resp_bytes = _canonical_json_bytes(find_response)
        receipt_resp_bytes = _canonical_json_bytes(receipt.get("response"))
        if find_resp_bytes != receipt_resp_bytes or find_response != receipt.get("response"):
            raise WaitSourceError(
                "find-returned terminal quota response does not match validated receipt response"
            )

        # 6. Verify repository facts are unchanged after Broker reads
        repo_facts_mid = validate_accepted_repository(
            validate_repository(c_root, require_clean=True)
        )
        if (
            _canonical_json_bytes(repo_facts_mid) != _canonical_json_bytes(repo_facts_before)
            or repo_facts_mid != repo_facts_before
        ):
            raise WaitSourceError("repository facts changed during Broker reads")

        # 7. Derive initial wait expected mapping from validated sources
        expected = derive_initial_wait_expected(
            reservation_record=reservation_record,
            terminal_receipt=receipt,
            terminal_receipt_bytes=raw_bytes,
            repository=repo_facts_mid,
            rescope_epoch=rescope_epoch,
            rescope_attempt=rescope_attempt,
        )

        # 8. Recheck repository facts after derivation
        repo_facts_after = validate_accepted_repository(
            validate_repository(c_root, require_clean=True)
        )
        if (
            _canonical_json_bytes(repo_facts_after) != _canonical_json_bytes(repo_facts_before)
            or repo_facts_after != repo_facts_before
        ):
            raise WaitSourceError("repository facts changed after wait expected derivation")

        return expected

    except WaitSourceError:
        raise
    except (BrokerError, AutopilotError, WaitExpectedError) as exc:
        raise WaitSourceError(f"failed to collect initial wait source: {exc}") from exc
