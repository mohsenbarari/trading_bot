"""Tests for Worker-1 post-validation result contract binding."""

from __future__ import annotations

import copy
import hashlib
import json
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_one_result_contract import (
    bind_worker_one_result,
)


def _make_valid_bundle():
    prior_text = json.dumps({"status": "PROPOSAL_READY", "changes": [{"path": "a.py"}]})
    prior_sha = hashlib.sha256(prior_text.encode("utf-8")).hexdigest()
    prior_info = {
        "result": {"status": "PROPOSAL_READY", "changes": [{"path": "a.py"}]},
        "status": {"conversation_id": "conv-worker-0", "exit_code": 0},
        "result_sha": prior_sha,
        "result_text": prior_text,
        "conversation_id": "conv-worker-0",
    }
    ctx = {
        "task": {"task_id": "task-test-123"},
        "source_head": "0123456789abcdef0123456789abcdef01234567",
        "continuation_run_prefix": "run-cont-456",
        "artifacts": {
            "worker_index": 1,
            "final_worker_run_id": "run-orig-worker-0",
            "semantic_task_fingerprint": "fingerprint-abc",
        },
        "evidence": {
            "worker_info": prior_info,
            "reviewer_status": {"conversation_id": "conv-reviewer-0"},
            "reviewer_result": {"findings": ["issue at line 10 in a.py"]},
            "feedback": {
                "reviewer": "gemini",
                "findings": ["issue at line 10 in a.py"],
            },
        },
    }
    curr_result = {"status": "PROPOSAL_READY", "changes": [{"path": "b.py"}]}
    curr_text = json.dumps(curr_result)
    curr_sha = hashlib.sha256(curr_text.encode("utf-8")).hexdigest()
    curr_status = {"conversation_id": "conv-worker-1", "exit_code": 0}
    validated = (curr_result, curr_status, curr_sha, curr_text)
    returned_state = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": "task-test-123",
        "source_head": "0123456789abcdef0123456789abcdef01234567",
        "final_worker_run_id": "run-cont-456-worker-1",
        "proposal_sha256": curr_sha,
        "changed_paths": ["b.py"],
        "proposal_path": ".local/two-site-refactor/proposals/run-cont-456-worker-1/result.json",
        "correction_count": 1,
        "semantic_task_fingerprint": "fingerprint-abc",
    }
    return ctx, returned_state, validated


def test_bind_worker_one_result_success():
    ctx, returned_state, validated = _make_valid_bundle()
    res = bind_worker_one_result(ctx, returned_state, validated)
    expected_top_keys = {
        "worker_info",
        "returned_state",
        "current_worker",
        "seen_convs",
        "seen_norm_findings",
        "seen_test_signatures",
        "seen_bootstrap_signatures",
    }
    assert set(res.keys()) == expected_top_keys
    expected_worker_info_keys = {
        "result",
        "status",
        "result_sha",
        "result_text",
        "conversation_id",
    }
    assert set(res["worker_info"].keys()) == expected_worker_info_keys
    assert "run_id" not in res["worker_info"]
    curr_sha = validated[2]
    curr_text = validated[3]
    assert res["current_worker"] == ("run-cont-456-worker-1", curr_sha, curr_text, "conv-worker-1")
    assert res["seen_convs"] == {"conv-worker-0", "conv-reviewer-0", "conv-worker-1"}
    assert res["seen_test_signatures"] == set()
    assert res["seen_bootstrap_signatures"] == set()
    assert res["seen_norm_findings"] == {"issue at line 10 in a.py"}


def test_reject_extra_run_id_or_missing_status_in_prior():
    ctx, returned_state, validated = _make_valid_bundle()
    bad_ctx1 = copy.deepcopy(ctx)
    bad_ctx1["evidence"]["worker_info"]["run_id"] = "invented-id"
    with pytest.raises(ProposalError, match="run_id|keys mismatch"):
        bind_worker_one_result(bad_ctx1, returned_state, validated)

    bad_ctx2 = copy.deepcopy(ctx)
    del bad_ctx2["evidence"]["worker_info"]["status"]
    with pytest.raises(ProposalError, match="status|keys mismatch"):
        bind_worker_one_result(bad_ctx2, returned_state, validated)


def test_reject_malformed_validated_fourth_value_or_sha():
    ctx, returned_state, validated = _make_valid_bundle()
    curr_result, curr_status, curr_sha, curr_text = validated

    bad_val1 = (curr_result, curr_status, curr_sha, "conv-worker-1")
    with pytest.raises(ProposalError, match="result_text must be valid JSON"):
        bind_worker_one_result(ctx, returned_state, bad_val1)

    bad_val2 = (curr_result, curr_status, hashlib.sha256(b"[]").hexdigest(), "[]")
    with pytest.raises(ProposalError, match="encode a JSON object"):
        bind_worker_one_result(ctx, returned_state, bad_val2)

    bad_val3 = (curr_result, curr_status, "0" * 64, curr_text)
    with pytest.raises(ProposalError, match="SHA256 does not match"):
        bind_worker_one_result(ctx, returned_state, bad_val3)

    bad_val4 = (curr_result, curr_status, "badsha", curr_text)
    with pytest.raises(ProposalError, match="invalid result_sha format"):
        bind_worker_one_result(ctx, returned_state, bad_val4)


def test_reject_duplicate_conversation_or_findings():
    ctx, returned_state, validated = _make_valid_bundle()
    curr_result, _, curr_sha, curr_text = validated
    bad_status = {"conversation_id": "conv-worker-0"}
    with pytest.raises(ProposalError, match="conversation IDs must be distinct"):
        bind_worker_one_result(ctx, returned_state, (curr_result, bad_status, curr_sha, curr_text))

    dup_ctx = copy.deepcopy(ctx)
    f1 = "Fix issue in a.py"
    f2 = "  fix   issue   in  a.py  "
    dup_ctx["evidence"]["feedback"]["findings"] = [f1, f2]
    dup_ctx["evidence"]["reviewer_result"]["findings"] = [f1, f2]
    with pytest.raises(ProposalError, match="duplicate normalized finding"):
        bind_worker_one_result(dup_ctx, returned_state, validated)


def test_reject_invalid_state_or_worker_index():
    ctx, returned_state, validated = _make_valid_bundle()
    bad_ret = copy.deepcopy(returned_state)
    bad_ret["correction_count"] = 0
    with pytest.raises(ProposalError, match="correction_count must be exact int 1"):
        bind_worker_one_result(ctx, bad_ret, validated)

    bad_ctx = copy.deepcopy(ctx)
    bad_ctx["artifacts"]["worker_index"] = 2
    with pytest.raises(ProposalError, match="worker_index must be exact int 1"):
        bind_worker_one_result(bad_ctx, returned_state, validated)
