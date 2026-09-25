"""Hermetic unit tests for _derive_verifier_resume_artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
import pytest

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        _derive_verifier_resume_artifacts,
        derive_verifier_resume_artifacts,
    )
    from scripts.two_site_provider_broker import (
        BrokerError,
        PIPELINE_QUOTA_KEYS,
        RECEIPT_KEYS,
        REQUEST_KEYS,
        RESPONSE_KEYS,
        validate_response,
    )
    from scripts.two_site_quota_resume_claim_admission import prepare_known_reset_resume_claim
    from scripts.two_site_quota_resume_request_record import build_quota_resume_request
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
    from tests.test_two_site_quota_resume_claim_admission import (
        _CONT_CYCLE_ID,
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
        _step_iso_seconds,
    )
except ImportError:
    from two_site_proposal_pipeline import (
        ProposalError,
        _derive_verifier_resume_artifacts,
        derive_verifier_resume_artifacts,
    )
    from two_site_provider_broker import (
        BrokerError,
        PIPELINE_QUOTA_KEYS,
        RECEIPT_KEYS,
        REQUEST_KEYS,
        RESPONSE_KEYS,
        validate_response,
    )
    from two_site_quota_resume_claim_admission import prepare_known_reset_resume_claim
    from two_site_quota_resume_request_record import build_quota_resume_request
    from two_site_quota_wait_schedule import _parse_utc_iso
    from test_two_site_quota_resume_claim_admission import (
        _CONT_CYCLE_ID,
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
        _step_iso_seconds,
    )

_SHA_PROPOSAL = "4" * 64
_SHA_TEST = "5" * 64
_SHA_STATUS = "6" * 64
_SHA_CLAIM = "c" * 64
_SHA_WAIT = "1" * 64
_VALID_CONT_CYCLE_ID = _CONT_CYCLE_ID if "_CONT_CYCLE_ID" in globals() else "cycle-2026-20260925T020304Z-456"


def _canonical_sha256(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _build_valid_pair(
    *,
    correction_count: int = 0,
    worker_retry: str = "",
    reviewer_retry: str = "",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    wait_record, expected_wait = _build_valid_wait_fixtures(known_reset=True)
    wait_record["quota_attempt"]["role"] = "verifier"
    expected_wait["quota_attempt"]["role"] = "verifier"

    semantic_attempt = _build_valid_semantic_attempt()
    due_time = wait_record["schedule"]["next_check_at_utc"]
    claimed_at = _step_iso_seconds(due_time, 5)

    admission = prepare_known_reset_resume_claim(
        wait_record=wait_record,
        expected_wait=expected_wait,
        semantic_attempt=semantic_attempt,
        expected_task_id=semantic_attempt["task_id"],
        original_wait_sha256=_SHA_WAIT,
        continuation_controller_run_id=_VALID_CONT_CYCLE_ID,
        resume_request_id="fedcba9876543210fedcba9876543210",
        claimed_at_utc=claimed_at,
    )
    claim = admission["claim"]
    expected_claim = admission["expected"]

    claim_epoch = _parse_utc_iso(claimed_at).timestamp()
    admitted_request = build_quota_resume_request(
        claim=claim,
        expected_claim=expected_claim,
        expected_claim_sha256=_SHA_CLAIM,
        timeout=300,
        now_epoch=claim_epoch + 10.0,
    )

    orig_prefix = wait_record["reservation"]["run_prefix"]
    worker_run_id = f"{orig_prefix}-worker-{correction_count}{worker_retry}"
    verifier_run_id = f"{orig_prefix}-reviewer-{correction_count}{reviewer_retry}"

    broker_request = {
        "schema_version": "1.0",
        "request_id": admitted_request["original_request_id"],
        "task_path": admitted_request["task_path"],
        "task_sha256": admitted_request["task_sha256"],
        "run_prefix": orig_prefix,
        "timeout": 300,
        "source_head": admitted_request["source_head"],
        "assignment_id": admitted_request["assignment_id"],
        "assignment_sha256": admitted_request["assignment_sha256"],
        "controller_run_id": admitted_request["original_controller_run_id"],
        "created_at": 1758762123.0,
    }

    proposal_path = f".local/two-site-refactor/proposals/{worker_run_id}/result.json"
    test_path = f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-{correction_count}.json"

    pipeline_state = {
        "state": "TASK_BLOCKED",
        "task_id": admitted_request["task_id"],
        "source_head": admitted_request["source_head"],
        "final_worker_run_id": worker_run_id,
        "final_reviewer_run_id": None,
        "proposal_sha256": _SHA_PROPOSAL,
        "changed_paths": ["tests/test_demo.py"],
        "proposal_path": proposal_path,
        "reviewer_path": None,
        "correction_count": correction_count,
        "blockers": ["observational quota, no replay authority"],
        "pre_review_test_path": test_path,
        "pre_review_test_sha256": _SHA_TEST,
        "pre_review_test_state": "PASSED",
        "semantic_task_fingerprint": admitted_request["semantic_task_fingerprint"],
        "quota_evidence": copy.deepcopy(wait_record["quota_evidence"]),
        "quota_attempt": {
            "role": "verifier",
            "run_id": verifier_run_id,
            "status_sha256": _SHA_STATUS,
        },
    }

    finished_at = 1758762423.0
    broker_response = {
        "schema_version": "1.0",
        "request_id": broker_request["request_id"],
        "run_prefix": orig_prefix,
        "source_head": broker_request["source_head"],
        "state": "COMPLETED",
        "exit_code": 1,
        "stdout": json.dumps(pipeline_state),
        "stderr": "",
        "finished_at": finished_at,
    }

    receipt = {
        "schema_version": "1.0",
        "request": broker_request,
        "request_sha256": _canonical_sha256(broker_request),
        "response": broker_response,
        "response_sha256": _canonical_sha256(broker_response),
        "quota_evidence": copy.deepcopy(wait_record["quota_evidence"]),
        "finished_at": finished_at,
    }
    return admitted_request, receipt, pipeline_state


def test_derive_verifier_resume_artifacts_success_and_immutability() -> None:
    admitted_req, receipt, state = _build_valid_pair(correction_count=0)
    admitted_snap = copy.deepcopy(admitted_req)
    receipt_snap = copy.deepcopy(receipt)

    assert set(receipt["request"].keys()) == REQUEST_KEYS
    assert set(receipt["response"].keys()) == RESPONSE_KEYS
    assert validate_response(receipt["request"], receipt["response"]) == receipt["response"]
    assert set(receipt.keys()) == RECEIPT_KEYS
    assert receipt["request_sha256"] == _canonical_sha256(receipt["request"])
    assert receipt["response_sha256"] == _canonical_sha256(receipt["response"])
    assert receipt["finished_at"] == receipt["response"]["finished_at"]
    assert set(state.keys()) == PIPELINE_QUOTA_KEYS
    assert "task_id" not in receipt["request"]
    assert "payload" not in receipt

    artifacts = _derive_verifier_resume_artifacts(admitted_req, receipt)
    alias_artifacts = derive_verifier_resume_artifacts(admitted_req, receipt)
    assert artifacts == alias_artifacts

    assert artifacts["task_id"] == admitted_req["task_id"]
    assert artifacts["semantic_task_fingerprint"] == admitted_req["semantic_task_fingerprint"]
    assert artifacts["source_head"] == admitted_req["source_head"]
    assert artifacts["original_run_prefix"] == receipt["request"]["run_prefix"]
    assert artifacts["correction_count"] == 0
    assert artifacts["final_worker_run_id"] == state["final_worker_run_id"]
    assert artifacts["proposal_path"] == state["proposal_path"]
    assert artifacts["proposal_sha256"] == _SHA_PROPOSAL
    assert artifacts["pre_review_test_path"] == state["pre_review_test_path"]
    assert artifacts["pre_review_test_sha256"] == _SHA_TEST
    assert artifacts["pre_review_test_state"] == "PASSED"
    assert artifacts["verifier_run_id"] == state["quota_attempt"]["run_id"]
    assert artifacts["verifier_status_sha256"] == _SHA_STATUS
    assert artifacts["quota_evidence"] == state["quota_evidence"]

    artifacts["quota_evidence"]["kind"] = "MUTATED"
    assert receipt["quota_evidence"]["kind"] != "MUTATED"
    assert admitted_req == admitted_snap
    assert receipt == receipt_snap


@pytest.mark.parametrize(
    ("corr_idx", "worker_retry", "reviewer_retry"),
    [
        (1, "-retry-1", "-retry-2"),
        (2, "", "-retry-1"),
    ],
)
def test_derive_verifier_resume_artifacts_retry_and_index_success(
    corr_idx: int,
    worker_retry: str,
    reviewer_retry: str,
) -> None:
    admitted_req, receipt, _ = _build_valid_pair(
        correction_count=corr_idx,
        worker_retry=worker_retry,
        reviewer_retry=reviewer_retry,
    )
    artifacts = _derive_verifier_resume_artifacts(admitted_req, receipt)
    assert artifacts["correction_count"] == corr_idx
    assert artifacts["final_worker_run_id"].endswith(worker_retry)
    assert artifacts["verifier_run_id"].endswith(reviewer_retry)


@pytest.mark.parametrize(
    ("mutator", "match_msg"),
    [
        (lambda r, rec, st: rec["request"].__setitem__("request_id", "diff"), "request_id"),
        (lambda r, rec, st: rec["request"].__setitem__("controller_run_id", "diff"), "controller_run_id"),
        (lambda r, rec, st: rec["request"].__setitem__("task_path", "diff/path"), "task_path"),
        (lambda r, rec, st: rec["request"].__setitem__("task_sha256", "f" * 64), "task_sha256"),
        (lambda r, rec, st: rec["request"].__setitem__("source_head", "f" * 40), "source_head"),
        (lambda r, rec, st: rec["request"].__setitem__("assignment_id", "diff"), "assignment_id"),
        (lambda r, rec, st: rec["request"].__setitem__("assignment_sha256", "f" * 64), "assignment_sha256"),
        (lambda r, rec, st: r.__setitem__("schema_version", "2.0"), "schema_version"),
        (lambda r, rec, st: r.__setitem__("request_type", "OTHER"), "request_type"),
        (lambda r, rec, st: r.__setitem__("role", "worker"), "admitted_request role"),
        (lambda r, rec, st: st["quota_attempt"].__setitem__("role", "worker"), "quota_attempt.role"),
        (lambda r, rec, st: st.__setitem__("task_id", "diff"), "task_id"),
        (lambda r, rec, st: st.__setitem__("semantic_task_fingerprint", "f" * 64), "semantic_task_fingerprint"),
        (lambda r, rec, st: st.__setitem__("source_head", "f" * 40), "source_head"),
        (lambda r, rec, st: st.__setitem__("state", "READY_FOR_CODEX"), "TASK_BLOCKED"),
        (lambda r, rec, st: st.__setitem__("blockers", []), "blockers sentinel"),
        (lambda r, rec, st: st.__setitem__("correction_count", 3), "correction_count"),
        (lambda r, rec, st: st.__setitem__("correction_count", True), "correction_count"),
        (lambda r, rec, st: st.__setitem__("final_worker_run_id", "ae03.c5b-test-worker-bad"), "worker pattern"),
        (lambda r, rec, st: st.__setitem__("final_worker_run_id", "ae03.c5b-test-worker-1"), "final_worker_run_id index"),
        (lambda r, rec, st: st["quota_attempt"].__setitem__("run_id", "ae03.c5b-test-reviewer-bad"), "reviewer/verifier pattern"),
        (lambda r, rec, st: st["quota_attempt"].__setitem__("run_id", "ae03.c5b-test-reviewer-1"), "verifier index"),
        (lambda r, rec, st: st["quota_attempt"].__setitem__("status_sha256", "bad"), "status_sha256"),
        (lambda r, rec, st: st.__setitem__("proposal_sha256", "bad"), "proposal_sha256"),
        (lambda r, rec, st: st.__setitem__("proposal_path", ".local/other/result.json"), "proposal_path"),
        (lambda r, rec, st: st.__setitem__("pre_review_test_state", "FAILED"), "pre_review_test_state"),
        (lambda r, rec, st: st.__setitem__("pre_review_test_sha256", "bad"), "pre_review_test_sha256"),
        (lambda r, rec, st: st.__setitem__("pre_review_test_path", ".local/other/test.json"), "pre_review_test_path"),
        (lambda r, rec, st: st.__setitem__("final_reviewer_run_id", "rev-0"), "final_reviewer_run_id"),
        (lambda r, rec, st: st.__setitem__("reviewer_path", "path.json"), "reviewer_path"),
        (lambda r, rec, st: rec.__setitem__("payload", {}), "RECEIPT_KEYS"),
        (lambda r, rec, st: rec["request"].__setitem__("task_id", "task-1"), "REQUEST_KEYS"),
        (lambda r, rec, st: st.__setitem__("extra_pipeline_key", 123), "PIPELINE_QUOTA_KEYS"),
        (lambda r, rec, st: r.pop("claim_sha256"), "QUOTA_CONTINUATION schema"),
    ],
)
def test_derive_verifier_resume_artifacts_rejections(mutator: Any, match_msg: str) -> None:
    admitted_req, receipt, state = _build_valid_pair(correction_count=0)
    mutator(admitted_req, receipt, state)
    receipt["response"]["stdout"] = json.dumps(state)
    with pytest.raises(ProposalError, match=match_msg):
        _derive_verifier_resume_artifacts(admitted_req, receipt)


def test_derive_verifier_resume_artifacts_non_dict_and_invalid_stdout() -> None:
    admitted_req, receipt, _ = _build_valid_pair(correction_count=0)
    with pytest.raises(ProposalError, match="admitted_request must be dict"):
        _derive_verifier_resume_artifacts("not-a-dict", receipt)  # type: ignore[arg-type]
    with pytest.raises(ProposalError, match="validated_terminal_receipt must be dict"):
        _derive_verifier_resume_artifacts(admitted_req, ["not-a-dict"])  # type: ignore[arg-type]

    bad_response = copy.deepcopy(receipt)
    bad_response["response"] = "not-a-dict"
    with pytest.raises(ProposalError, match="receipt response must be dict"):
        _derive_verifier_resume_artifacts(admitted_req, bad_response)

    bad_stdout = copy.deepcopy(receipt)
    bad_stdout["response"]["stdout"] = 12345
    with pytest.raises(ProposalError, match="receipt response stdout must be str"):
        _derive_verifier_resume_artifacts(admitted_req, bad_stdout)

    invalid_json_stdout = copy.deepcopy(receipt)
    invalid_json_stdout["response"]["stdout"] = "{not valid json}"
    with pytest.raises(ProposalError, match="failed to parse pipeline state"):
        _derive_verifier_resume_artifacts(admitted_req, invalid_json_stdout)


def test_validate_response_rejects_malformed_envelope_and_lineage_mismatch() -> None:
    admitted_req, receipt, _ = _build_valid_pair(correction_count=0)
    req = receipt["request"]
    resp = receipt["response"]

    extra_resp = copy.deepcopy(resp)
    extra_resp["extra_field"] = "unexpected"
    with pytest.raises(BrokerError, match="broker response does not match the exact contract"):
        validate_response(req, extra_resp)

    missing_resp = copy.deepcopy(resp)
    missing_resp.pop("finished_at")
    with pytest.raises(BrokerError, match="broker response does not match the exact contract"):
        validate_response(req, missing_resp)

    bad_req_id = copy.deepcopy(resp)
    bad_req_id["request_id"] = "mismatched-id"
    with pytest.raises(BrokerError, match="broker response binding is invalid"):
        validate_response(req, bad_req_id)

    bad_prefix = copy.deepcopy(resp)
    bad_prefix["run_prefix"] = "mismatched-prefix"
    with pytest.raises(BrokerError, match="broker response binding is invalid"):
        validate_response(req, bad_prefix)

    bad_source_head = copy.deepcopy(resp)
    bad_source_head["source_head"] = "0" * 40
    with pytest.raises(BrokerError, match="broker response binding is invalid"):
        validate_response(req, bad_source_head)
