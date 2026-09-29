"""Hermetic unit tests for dormant pure final-acknowledgement record codec."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
import hashlib
import json
from typing import Any
from unittest import mock

import pytest

from scripts.two_site_quota_accepted_lineage_record import (
    MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
    PROTECTED_REF_NAMES,
    build_accepted_lineage_record,
)
from scripts.two_site_quota_final_ack_record import (
    FINAL_ACK_KIND,
    FINAL_ACK_SCHEMA_VERSION,
    FinalAckRecordError,
    MAX_FINAL_ACK_RECORD_BYTES,
    _bounded_canonical_bytes,
    build_final_ack_candidate,
    validate_final_ack_candidate,
)

_SAMPLE_PROTECTED_REFS = {
    "refs/heads/main": "1" * 40,
    "refs/remotes/origin/main": "2" * 40,
    "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
}

_SAMPLE_ACK_UTC = "2026-09-29T14:00:00Z"


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _make_lineage_fixture(
    state: str = "COMPLETE",
) -> tuple[dict[str, Any], str, str]:
    source_head = "1" * 40
    if state == "COMPLETE":
        head_after = source_head
        head_changed = False
        commit = None
        next_action = None
        worker_id = None
        reviewer_id = None
        prop_sha = None
    elif state == "CONTINUE":
        head_after = "2" * 40
        head_changed = True
        commit = head_after
        next_action = "Proceed with task verification AE03-C5F"
        worker_id = "worker-run-cycle-101"
        reviewer_id = "reviewer-run-cycle-101"
        prop_sha = "e" * 64
    else:
        raise ValueError(f"unsupported state: {state!r}")

    raw_result = {
        "schema_version": "1.0",
        "state": state,
        "disposition": "NONE",
        "category": "NONE",
        "summary": f"Summary for {state} verification",
        "commit": commit,
        "next_action": next_action,
        "blockers": [],
        "gemini_worker_run_id": worker_id,
        "gemini_reviewer_run_id": reviewer_id,
        "proposal_sha256": prop_sha,
    }
    result_sha256 = hashlib.sha256(_canonical_bytes(raw_result)).hexdigest()

    result = dict(raw_result)
    result["head_before"] = source_head
    result["head_after"] = head_after
    result["head_changed"] = head_changed

    upstream = {
        "original_controller_run_id": "cycle-0001-20260925T100000Z-001",
        "continuation_controller_run_id": "cycle-0002-20260925T110000Z-002",
        "original_request_id": "a" * 32,
        "resume_request_id": "b" * 32,
        "task_id": "AE03-TASK-001",
        "assignment_id": "asgn-test-001",
        "source_head": source_head,
        "task_sha256": "4" * 64,
        "assignment_sha256": "5" * 64,
        "semantic_task_fingerprint": "6" * 64,
        "original_wait_sha256": "7" * 64,
        "claim_sha256": "8" * 64,
        "request_sha256": "9" * 64,
        "submission_intent_sha256": "a" * 64,
        "terminal_receipt_sha256": "b" * 64,
        "response_sha256": "c" * 64,
        "response_state": "COMPLETED",
        "exit_code": 0,
        "expected_lineage_snapshot_sha256": "d" * 64,
    }

    context = {
        "controller_run_id": upstream["continuation_controller_run_id"],
        "protected_refs": copy.deepcopy(_SAMPLE_PROTECTED_REFS),
        "assignment_id": upstream["assignment_id"],
        "assignment_sha256": upstream["assignment_sha256"],
        "current_head": head_after,
    }
    assert set(context["protected_refs"].keys()) == PROTECTED_REF_NAMES

    lineage_record = build_accepted_lineage_record(
        result=result,
        acceptance_context=context,
        upstream=upstream,
        result_sha256=result_sha256,
    )
    lineage_sha = hashlib.sha256(_canonical_bytes(lineage_record)).hexdigest()
    return lineage_record, lineage_sha, _SAMPLE_ACK_UTC


def test_build_and_validate_final_ack_candidate_complete() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )

    assert len(candidate) == 12
    assert candidate["schema_version"] == FINAL_ACK_SCHEMA_VERSION
    assert candidate["ack_kind"] == FINAL_ACK_KIND
    assert candidate["original_controller_run_id"] == lineage["upstream"]["original_controller_run_id"]
    assert candidate["continuation_controller_run_id"] == lineage["controller_run_id"]
    assert candidate["resume_request_id"] == lineage["upstream"]["resume_request_id"]
    assert candidate["terminal_receipt_sha256"] == lineage["upstream"]["terminal_receipt_sha256"]
    assert candidate["accepted_lineage_sha256"] == lineage_sha
    assert candidate["result_sha256"] == lineage["result_sha256"]
    assert candidate["accepted_state"] == "COMPLETE"
    assert candidate["head_after"] == lineage["head_after"]
    assert candidate["assignment_sha256"] == lineage["assignment_sha256"]
    assert candidate["acknowledged_at_utc"] == ack_utc

    validated = validate_final_ack_candidate(
        candidate=candidate,
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
    )
    assert validated == candidate


def test_build_and_validate_final_ack_candidate_continue() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="CONTINUE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )

    assert len(candidate) == 12
    assert candidate["schema_version"] == "1.0"
    assert candidate["ack_kind"] == "CODEX_FINAL_ACCEPTANCE"
    assert candidate["accepted_state"] == "CONTINUE"
    assert candidate["head_after"] == "2" * 40
    assert candidate["head_after"] != lineage["head_before"]

    validated = validate_final_ack_candidate(
        candidate=candidate,
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
    )
    assert validated == candidate


def test_detachment_immutability() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )
    validated = validate_final_ack_candidate(
        candidate=candidate,
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
    )

    lineage["upstream"]["original_controller_run_id"] = "tampered-run-id"
    lineage["result_sha256"] = "0" * 64
    assert candidate["original_controller_run_id"] != "tampered-run-id"
    assert candidate["result_sha256"] != "0" * 64

    candidate["head_after"] = "f" * 40
    assert validated["head_after"] == "1" * 40


def test_accepted_lineage_sha256_mismatch_fails_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    wrong_sha = "0" * 64
    with pytest.raises(FinalAckRecordError, match="accepted_lineage_sha256 mismatch"):
        build_final_ack_candidate(
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=wrong_sha,
            acknowledged_at_utc=ack_utc,
        )


def test_mutated_accepted_lineage_record_fails_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    lineage["result_sha256"] = "f" * 64
    with pytest.raises(FinalAckRecordError, match="accepted_lineage_sha256 mismatch"):
        build_final_ack_candidate(
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
            acknowledged_at_utc=ack_utc,
        )


def test_accepted_lineage_structural_corruption_fails_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    lineage["upstream"]["exit_code"] = 1
    corrupt_sha = hashlib.sha256(_canonical_bytes(lineage)).hexdigest()
    with pytest.raises(FinalAckRecordError, match="structural validation failed"):
        build_final_ack_candidate(
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=corrupt_sha,
            acknowledged_at_utc=ack_utc,
        )


def test_upstream_linkage_tamper_fails_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    lineage["upstream"]["continuation_controller_run_id"] = "cycle-9999-20260925T110000Z-999"
    corrupt_sha = hashlib.sha256(_canonical_bytes(lineage)).hexdigest()
    with pytest.raises(FinalAckRecordError, match="structural validation failed"):
        build_final_ack_candidate(
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=corrupt_sha,
            acknowledged_at_utc=ack_utc,
        )


def test_candidate_missing_or_extra_keys_fail_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )

    missing_cand = dict(candidate)
    del missing_cand["terminal_receipt_sha256"]
    with pytest.raises(FinalAckRecordError, match="candidate keys mismatch"):
        validate_final_ack_candidate(
            candidate=missing_cand,
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )

    extra_cand = dict(candidate)
    extra_cand["extra_audit_field"] = "unauthorized"
    with pytest.raises(FinalAckRecordError, match="candidate keys mismatch"):
        validate_final_ack_candidate(
            candidate=extra_cand,
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )

    with pytest.raises(FinalAckRecordError, match="candidate must be a dict"):
        validate_final_ack_candidate(
            candidate="not-a-dict",  # type: ignore[arg-type]
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )


def test_candidate_wrong_types_fail_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )

    type_bad_cand = dict(candidate)
    type_bad_cand["schema_version"] = 1
    with pytest.raises(FinalAckRecordError, match="candidate schema_version must be '1.0'"):
        validate_final_ack_candidate(
            candidate=type_bad_cand,
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )


@pytest.mark.parametrize(
    ("field", "tampered_val", "match_pattern"),
    [
        (
            "original_controller_run_id",
            "cycle-0099-20260925T100000Z-001",
            "does not match rederived accepted-lineage value",
        ),
        (
            "continuation_controller_run_id",
            "cycle-0099-20260925T110000Z-002",
            "does not match rederived accepted-lineage value",
        ),
        (
            "resume_request_id",
            "f" * 32,
            "does not match rederived accepted-lineage value",
        ),
        (
            "terminal_receipt_sha256",
            "f" * 64,
            "does not match rederived accepted-lineage value",
        ),
        (
            "accepted_lineage_sha256",
            "f" * 64,
            "does not match rederived accepted-lineage value",
        ),
        (
            "result_sha256",
            "f" * 64,
            "does not match rederived accepted-lineage value",
        ),
        (
            "accepted_state",
            "CONTINUE",
            "does not match rederived accepted-lineage value",
        ),
        (
            "head_after",
            "f" * 40,
            "does not match rederived accepted-lineage value",
        ),
        (
            "assignment_sha256",
            "f" * 64,
            "does not match rederived accepted-lineage value",
        ),
        (
            "schema_version",
            "2.0",
            "candidate schema_version must be '1.0'",
        ),
        (
            "ack_kind",
            "OTHER_ACCEPTANCE",
            "candidate ack_kind must be 'CODEX_FINAL_ACCEPTANCE'",
        ),
    ],
)
def test_candidate_rederived_field_mismatches_fail_closed(
    field: str,
    tampered_val: str,
    match_pattern: str,
) -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )
    candidate[field] = tampered_val
    with pytest.raises(
        FinalAckRecordError,
        match=match_pattern,
    ):
        validate_final_ack_candidate(
            candidate=candidate,
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )


@pytest.mark.parametrize(
    "bad_timestamp",
    [
        "2026-09-29T14:00:00",
        "2026-09-29T14:00:00+00:00",
        "2026-09-29T14:00:00-05:00",
        "2026-09-29T14:00:00.123Z",
        "2026-09-29 14:00:00Z",
        "2026-02-30T14:00:00Z",
        "2026-04-31T14:00:00Z",
        "2025-02-29T14:00:00Z",
        "2026-13-01T14:00:00Z",
        "2026-00-10T14:00:00Z",
        "2026-09-29T24:00:00Z",
        "2026-09-29T14:60:00Z",
        "2026-09-29T14:00:60Z",
        "not-a-timestamp",
        1727618400,
    ],
)
def test_timestamp_validation_fails_closed(bad_timestamp: Any) -> None:
    lineage, lineage_sha, _ = _make_lineage_fixture(state="COMPLETE")
    with pytest.raises(FinalAckRecordError):
        build_final_ack_candidate(
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
            acknowledged_at_utc=bad_timestamp,
        )


def test_canonical_ack_json_size_limit_fails_closed() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )
    cand_bytes = _canonical_bytes(candidate)
    assert len(cand_bytes) <= MAX_FINAL_ACK_RECORD_BYTES

    oversize_cand = dict(candidate)
    oversize_cand["original_controller_run_id"] = "cycle-" + ("0" * 2500) + "-001"
    with pytest.raises(
        FinalAckRecordError,
        match="candidate original_controller_run_id must match cycle run pattern",
    ):
        validate_final_ack_candidate(
            candidate=oversize_cand,
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )


def test_accepted_lineage_canonical_size_limit_fails_closed() -> None:
    lineage, _, ack_utc = _make_lineage_fixture(state="COMPLETE")
    lineage["upstream"]["task_id"] = "X" * (MAX_ACCEPTED_LINEAGE_RECORD_BYTES + 100)
    dummy_sha = "0" * 64
    with pytest.raises(FinalAckRecordError, match="length exceeds limit"):
        build_final_ack_candidate(
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=dummy_sha,
            acknowledged_at_utc=ack_utc,
        )


@pytest.mark.parametrize(
    ("target_key", "in_upstream"),
    [
        ("task_id", True),
        ("assignment_id", True),
        ("assignment_id", False),
    ],
)
def test_oversized_free_text_prebound_rejects_before_encoder(
    target_key: str,
    in_upstream: bool,
) -> None:
    lineage, _, ack_utc = _make_lineage_fixture(state="COMPLETE")
    large_val = "x" * (1024 * 1024)  # 1 MiB structurally valid string
    if in_upstream:
        lineage["upstream"][target_key] = large_val
    else:
        lineage["assignment_id"] = large_val

    dummy_sha = "0" * 64

    orig_iterencode = json.JSONEncoder.iterencode

    def spy_iterencode(self: Any, o: Any) -> Any:
        if isinstance(o, dict) and (
            o.get("assignment_id") == large_val
            or (isinstance(o.get("upstream"), dict) and o["upstream"].get(target_key) == large_val)
        ):
            raise AssertionError("iterencode must not be invoked on oversized accepted lineage")
        return orig_iterencode(self, o)

    with mock.patch.object(json.JSONEncoder, "iterencode", spy_iterencode):
        with pytest.raises(FinalAckRecordError, match="length exceeds limit"):
            build_final_ack_candidate(
                accepted_lineage_record=lineage,
                accepted_lineage_sha256=dummy_sha,
                acknowledged_at_utc=ack_utc,
            )
        valid_candidate = {
            "schema_version": FINAL_ACK_SCHEMA_VERSION,
            "ack_kind": FINAL_ACK_KIND,
            "original_controller_run_id": lineage["upstream"]["original_controller_run_id"],
            "continuation_controller_run_id": lineage["controller_run_id"],
            "resume_request_id": lineage["upstream"]["resume_request_id"],
            "terminal_receipt_sha256": lineage["upstream"]["terminal_receipt_sha256"],
            "accepted_lineage_sha256": dummy_sha,
            "result_sha256": lineage["result_sha256"],
            "accepted_state": lineage["accepted_state"],
            "head_after": lineage["head_after"],
            "assignment_sha256": lineage["assignment_sha256"],
            "acknowledged_at_utc": ack_utc,
        }
        with pytest.raises(FinalAckRecordError, match="length exceeds limit"):
            validate_final_ack_candidate(
                candidate=valid_candidate,
                accepted_lineage_record=lineage,
                accepted_lineage_sha256=dummy_sha,
            )


def test_bounded_canonical_bytes_early_rejection_instrumented() -> None:
    lineage, _, _ = _make_lineage_fixture(state="COMPLETE")
    lineage["upstream"]["task_id"] = "X" * (MAX_ACCEPTED_LINEAGE_RECORD_BYTES + 500)

    yielded_chunks: list[str] = []
    orig_iterencode = json.JSONEncoder.iterencode

    def spy_iterencode(self: Any, o: Any) -> Any:
        for chunk in orig_iterencode(self, o):
            yielded_chunks.append(chunk)
            yield chunk

    with mock.patch.object(json.JSONEncoder, "iterencode", spy_iterencode):
        with pytest.raises(FinalAckRecordError, match="canonical JSON size exceeds limit"):
            _bounded_canonical_bytes(lineage, MAX_ACCEPTED_LINEAGE_RECORD_BYTES)

    total_bytes = sum(len(c.encode("utf-8")) for c in yielded_chunks)
    assert total_bytes > MAX_ACCEPTED_LINEAGE_RECORD_BYTES
    assert not any("task_sha256" in c for c in yielded_chunks)
    assert not any("terminal_receipt_sha256" in c for c in yielded_chunks)


def test_candidate_bounded_canonical_size_guard() -> None:
    lineage, lineage_sha, ack_utc = _make_lineage_fixture(state="COMPLETE")
    candidate = build_final_ack_candidate(
        accepted_lineage_record=lineage,
        accepted_lineage_sha256=lineage_sha,
        acknowledged_at_utc=ack_utc,
    )
    with pytest.raises(FinalAckRecordError, match="canonical JSON size exceeds limit"):
        _bounded_canonical_bytes(candidate, 100)


def test_docstring_and_purity_contracts() -> None:
    import scripts.two_site_quota_final_ack_record as mod

    doc = mod.__doc__ or ""
    assert "strict file custody" in doc
    assert "Supervisor lock" in doc
    assert "one-shot acknowledgement" in doc
    assert "C4c" in doc
    assert "reference-free >=10d" in doc
