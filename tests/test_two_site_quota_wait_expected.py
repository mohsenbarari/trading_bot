"""Tests for derive_initial_wait_expected projection and validation."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

import pytest

from scripts.two_site_quota_wait_expected import (
    WaitExpectedError,
    derive_initial_wait_expected,
)

_CONTROLLER = "cycle-2026-20260925T010203Z-123"
_REQUEST_ID = "0123456789abcdef0123456789abcdef"
_SOURCE_HEAD = "1" * 40
_TASK_PATH = ".local/two-site-refactor/tasks/test.json"
_TASK_SHA = "2" * 64
_ASSIGNMENT_SHA = "3" * 64
_RUN_PREFIX = "ae03-test"
_ASSIGNMENT_ID = "assignment-ae03-c5b-001"
_CREATED_AT = 123.5
_FINISHED_AT = 124.5
_EPOCH = "rescope-" + "e" * 64
_ATTEMPT = 1
_FINGERPRINT = "4" * 64
_STATUS_SHA = "5" * 64


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_valid_fixtures() -> tuple[
    dict[str, Any], dict[str, Any], bytes, dict[str, Any], str, int, dict[str, str]
]:
    reservation = {
        "controller_run_id": _CONTROLLER,
        "run_prefix": _RUN_PREFIX,
        "task_path": _TASK_PATH,
        "task_sha256": _TASK_SHA,
        "source_head": _SOURCE_HEAD,
        "assignment_id": _ASSIGNMENT_ID,
        "assignment_sha256": _ASSIGNMENT_SHA,
    }

    request = {
        "schema_version": "1.0",
        "request_id": _REQUEST_ID,
        "controller_run_id": _CONTROLLER,
        "run_prefix": _RUN_PREFIX,
        "task_path": _TASK_PATH,
        "task_sha256": _TASK_SHA,
        "source_head": _SOURCE_HEAD,
        "assignment_id": _ASSIGNMENT_ID,
        "assignment_sha256": _ASSIGNMENT_SHA,
        "timeout": 900,
        "created_at": _CREATED_AT,
    }

    quota_evidence = {
        "kind": "QUOTA_RESET_UNKNOWN",
        "provider": "gemini",
        "model": "gemini-test",
        "conversation_id": "conv-test",
        "reset_at_utc": None,
    }

    quota_attempt = {
        "role": "worker",
        "run_id": "ae03-test-worker-0",
        "status_sha256": _STATUS_SHA,
    }

    pipeline_payload = {
        "state": "TASK_BLOCKED",
        "task_id": "test-id",
        "source_head": _SOURCE_HEAD,
        "final_worker_run_id": None,
        "final_reviewer_run_id": None,
        "proposal_sha256": None,
        "changed_paths": [],
        "proposal_path": None,
        "reviewer_path": None,
        "correction_count": 0,
        "blockers": ["observational quota, no replay authority"],
        "pre_review_test_path": None,
        "pre_review_test_sha256": None,
        "pre_review_test_state": None,
        "semantic_task_fingerprint": _FINGERPRINT,
        "quota_evidence": quota_evidence,
        "quota_attempt": quota_attempt,
    }

    stdout_str = _canonical_bytes(pipeline_payload).decode("utf-8")

    response = {
        "schema_version": "1.0",
        "request_id": _REQUEST_ID,
        "run_prefix": _RUN_PREFIX,
        "source_head": _SOURCE_HEAD,
        "state": "COMPLETED",
        "exit_code": 1,
        "stdout": stdout_str,
        "stderr": "",
        "finished_at": _FINISHED_AT,
    }

    req_bytes = _canonical_bytes(request)
    req_sha = _sha256(req_bytes)
    resp_bytes = _canonical_bytes(response)
    resp_sha = _sha256(resp_bytes)

    receipt = {
        "schema_version": "1.0",
        "request": request,
        "request_sha256": req_sha,
        "response": response,
        "response_sha256": resp_sha,
        "quota_evidence": quota_evidence,
        "finished_at": _FINISHED_AT,
    }
    receipt_bytes = _canonical_bytes(receipt)
    receipt_sha = _sha256(receipt_bytes)

    res_record = {
        "schema_version": "1.0",
        "controller_run_id": _CONTROLLER,
        "request_id": _REQUEST_ID,
        "reservation": reservation,
        "request_sha256": req_sha,
        "created_at": _CREATED_AT,
    }

    repository = {
        "head": _SOURCE_HEAD,
        "status": "",
        "protected_refs": {"refs/heads/main": _SOURCE_HEAD},
        "control_blobs": {"config/rules.json": "a" * 40},
        "assignment_scope": {
            "assignment_id": _ASSIGNMENT_ID,
            "stage_id": "P1-00b",
            "sha256": _ASSIGNMENT_SHA,
            "expires_at": "2026-09-25T05:00:00Z",
            "allowed_paths": ["tests/test_two_site_quota_wait_expected.py"],
            "forbidden_paths": ["scripts/two_site_quota_wait_expected.py"],
        },
    }

    digests = {
        "request_sha": req_sha,
        "response_sha": resp_sha,
        "receipt_sha": receipt_sha,
    }
    return res_record, receipt, receipt_bytes, repository, _EPOCH, _ATTEMPT, digests


def _recompute_all(
    res_record: dict[str, Any],
    receipt: dict[str, Any],
    recompute_req: bool = False,
    recompute_resp: bool = False,
) -> bytes:
    if recompute_req:
        req_sha = _sha256(_canonical_bytes(receipt["request"]))
        receipt["request_sha256"] = req_sha
        res_record["request_sha256"] = req_sha
    if recompute_resp:
        resp_sha = _sha256(_canonical_bytes(receipt["response"]))
        receipt["response_sha256"] = resp_sha
    return _canonical_bytes(receipt)


def test_derive_initial_wait_expected_positive() -> None:
    res_record, receipt, receipt_bytes, repo, epoch, attempt, digests = _build_valid_fixtures()
    orig_res = copy.deepcopy(res_record)
    orig_receipt = copy.deepcopy(receipt)
    orig_repo = copy.deepcopy(repo)

    result = derive_initial_wait_expected(res_record, receipt, receipt_bytes, repo, epoch, attempt)

    assert set(result.keys()) == {
        "controller_run_id", "request_id", "reservation", "request_sha256",
        "terminal_receipt_sha256", "response_sha256", "semantic_task_fingerprint",
        "rescope_epoch", "rescope_attempt", "quota_attempt", "quota_evidence", "repository",
    }
    assert result["controller_run_id"] == _CONTROLLER
    assert result["request_id"] == _REQUEST_ID
    assert result["reservation"] == res_record["reservation"]
    assert result["request_sha256"] == digests["request_sha"]
    assert result["terminal_receipt_sha256"] == digests["receipt_sha"]
    assert result["response_sha256"] == digests["response_sha"]
    assert result["semantic_task_fingerprint"] == _FINGERPRINT
    assert result["rescope_epoch"] == epoch
    assert result["rescope_attempt"] == attempt
    assert result["quota_attempt"] == {"role": "worker", "run_id": "ae03-test-worker-0", "status_sha256": _STATUS_SHA}
    assert result["quota_evidence"] == receipt["quota_evidence"]
    assert result["repository"] == repo

    result["reservation"]["task_path"] = "mutated"
    result["quota_attempt"]["role"] = "verifier"
    result["quota_evidence"]["provider"] = "mutated"
    result["repository"]["status"] = "dirty"
    result["repository"]["assignment_scope"]["allowed_paths"].append("mutated")

    assert res_record == orig_res
    assert receipt == orig_receipt
    assert repo == orig_repo


def _setup_negative_case(case: str) -> tuple[
    dict[str, Any], dict[str, Any], bytes, dict[str, Any], str, int, str
]:
    res_record, receipt, receipt_bytes, repo, epoch, attempt, _ = _build_valid_fixtures()

    if case == "created_at_drift":
        receipt["request"]["created_at"] = 123
        res_record["created_at"] = 123.0
        receipt_bytes = _recompute_all(res_record, receipt, recompute_req=True)
        err = "reservation_record created_at does not match request created_at"
    elif case == "response_request_id_drift":
        receipt["response"]["request_id"] = "f" * 32
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "response request_id does not match request_id"
    elif case == "response_run_prefix_drift":
        receipt["response"]["run_prefix"] = "other-prefix"
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "response run_prefix does not match request"
    elif case == "response_source_head_drift":
        receipt["response"]["source_head"] = "f" * 40
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "response source_head does not match request"
    elif case == "request_reservation_drift":
        receipt["request"]["task_path"] = ".local/two-site-refactor/tasks/other.json"
        receipt_bytes = _recompute_all(res_record, receipt, recompute_req=True)
        err = "reservation field 'task_path' does not match request field"
    elif case == "quota_evidence_drift":
        receipt["quota_evidence"]["conversation_id"] = "conv-other"
        receipt_bytes = _recompute_all(res_record, receipt)
        err = "pipeline quota_evidence does not match terminal_receipt"
    elif case == "pipeline_state_drift":
        payload = json.loads(receipt["response"]["stdout"])
        payload["state"] = "TASK_RUNNING"
        receipt["response"]["stdout"] = _canonical_bytes(payload).decode("utf-8")
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "pipeline state must be 'TASK_BLOCKED'"
    elif case == "pipeline_blocker_drift":
        payload = json.loads(receipt["response"]["stdout"])
        payload["blockers"] = ["other"]
        receipt["response"]["stdout"] = _canonical_bytes(payload).decode("utf-8")
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "pipeline blockers mismatch"
    elif case == "pipeline_fingerprint_drift":
        payload = json.loads(receipt["response"]["stdout"])
        payload["semantic_task_fingerprint"] = "bad"
        receipt["response"]["stdout"] = _canonical_bytes(payload).decode("utf-8")
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "semantic_task_fingerprint must be 64 lowercase hex"
    elif case == "pipeline_attempt_drift":
        payload = json.loads(receipt["response"]["stdout"])
        payload["quota_attempt"]["role"] = "reviewer"
        receipt["response"]["stdout"] = _canonical_bytes(payload).decode("utf-8")
        receipt_bytes = _recompute_all(res_record, receipt, recompute_resp=True)
        err = "quota_attempt role must be 'worker' or 'verifier'"
    elif case == "repo_head_drift":
        repo["head"] = "f" * 40
        err = "repository head does not match request source_head"
    elif case == "assignment_id_drift":
        repo["assignment_scope"]["assignment_id"] = "other-assignment"
        err = "assignment_scope assignment_id does not match reservation"
    elif case == "assignment_sha_drift":
        repo["assignment_scope"]["sha256"] = "f" * 64
        err = "assignment_scope sha256 does not match reservation"
    elif case == "bad_rescope_attempt":
        attempt = 4
        err = "rescope_attempt must be int in range 1..3: 4"
    elif case == "bad_rescope_epoch":
        epoch = "bad-epoch"
        err = "invalid rescope_epoch: 'bad-epoch'"
    elif case == "noncanonical_receipt_bytes":
        receipt_bytes = receipt_bytes + b" "
        err = "terminal_receipt_bytes is not canonical compact UTF-8 serialization of receipt"
    elif case == "wrong_digest":
        receipt["request_sha256"] = "f" * 64
        receipt_bytes = _canonical_bytes(receipt)
        err = "terminal_receipt request_sha256 does not match canonical request SHA"
    else:
        raise ValueError(f"unknown case {case}")

    return res_record, receipt, receipt_bytes, repo, epoch, attempt, err


@pytest.mark.parametrize(
    "case",
    [
        "created_at_drift",
        "response_request_id_drift",
        "response_run_prefix_drift",
        "response_source_head_drift",
        "request_reservation_drift",
        "quota_evidence_drift",
        "pipeline_state_drift",
        "pipeline_blocker_drift",
        "pipeline_fingerprint_drift",
        "pipeline_attempt_drift",
        "repo_head_drift",
        "assignment_id_drift",
        "assignment_sha_drift",
        "bad_rescope_attempt",
        "bad_rescope_epoch",
        "noncanonical_receipt_bytes",
        "wrong_digest",
    ],
)
def test_derive_initial_wait_expected_negatives(case: str) -> None:
    res_record, receipt, receipt_bytes, repo, epoch, attempt, expected_err = _setup_negative_case(case)
    with pytest.raises(WaitExpectedError, match=re.escape(expected_err)):
        derive_initial_wait_expected(res_record, receipt, receipt_bytes, repo, epoch, attempt)
