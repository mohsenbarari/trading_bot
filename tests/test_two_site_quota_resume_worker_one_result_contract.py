"""Tests for Worker-1 post-validation result contract binding."""

from __future__ import annotations

import copy
import hashlib
import json
import pytest

from scripts.two_site_pre_review_receipt import (
    p1_00_characterization_bootstrap_findings,
    validate_test_receipt,
)
from scripts.two_site_proposal_pipeline import (
    ProposalError,
    test_failure_signature as producer_test_failure_signature,
    test_gate_feedback as producer_test_gate_feedback,
)
from scripts.two_site_proposal_runner import (
    validate_correction_feedback,
)
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


def _make_bootstrap_bundle():
    bad_code = "import models\n\ndef test_dummy():\n    pass\n"
    prior_result = {
        "status": "PROPOSAL_READY",
        "summary": "real bootstrap defect test",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": "tests/test_p1_00_characterization_sample.py",
                "original_sha256": None,
                "content": bad_code,
                "replacements": [],
            }
        ],
    }
    prior_text = json.dumps(prior_result)
    prior_sha = hashlib.sha256(prior_text.encode("utf-8")).hexdigest()
    prior_info = {
        "result": prior_result,
        "status": {"conversation_id": "conv-worker-0", "exit_code": 0},
        "result_sha": prior_sha,
        "result_text": prior_text,
        "conversation_id": "conv-worker-0",
    }
    bootstrap_findings = p1_00_characterization_bootstrap_findings(prior_result)
    assert bootstrap_findings

    fb_payload = {
        "reviewer": "test-gate",
        "reviewed_proposal_sha256": prior_sha,
        "findings": bootstrap_findings,
        "test_evidence": [
            "gate=P1-00-characterization-bootstrap",
            f"proposal_sha256={prior_sha}",
            f"finding_count={len(bootstrap_findings)}",
        ],
    }
    feedback = validate_correction_feedback(json.dumps(fb_payload), prior_sha)

    ctx = {
        "task": {"task_id": "task-test-123"},
        "source_head": "0123456789abcdef0123456789abcdef01234567",
        "continuation_run_prefix": "run-cont-456",
        "artifacts": {
            "correction_origin": "BOOTSTRAP",
            "worker_index": 1,
            "final_worker_run_id": "run-orig-worker-0",
            "semantic_task_fingerprint": "fingerprint-abc",
            "proposal_sha256": prior_sha,
            "reviewer_path": None,
            "final_reviewer_run_id": None,
            "pre_review_test_path": None,
            "pre_review_test_sha256": None,
            "pre_review_test_state": None,
        },
        "evidence": {
            "worker_info": prior_info,
            "feedback": feedback,
            "receipt": None,
            "reviewer_status": None,
            "reviewer_result": None,
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
    return ctx, returned_state, validated, bootstrap_findings


def _make_test_failed_bundle():
    prior_result = {"status": "PROPOSAL_READY", "changes": [{"path": "a.py"}]}
    prior_text = json.dumps(prior_result)
    prior_sha = hashlib.sha256(prior_text.encode("utf-8")).hexdigest()
    prior_info = {
        "result": prior_result,
        "status": {"conversation_id": "conv-worker-0", "exit_code": 0},
        "result_sha": prior_sha,
        "result_text": prior_text,
        "conversation_id": "conv-worker-0",
    }
    receipt = {
        "state": "FAILED",
        "proposal_sha256": prior_sha,
        "selectors": ["tests/test_sample.py"],
        "unsupported_specs": [],
        "returncode": 1,
        "stdout_tail": "FAILED tests/test_sample.py::test_case - AssertionError",
        "stderr_tail": "E   AssertionError: assert False",
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "hermetic",
    }
    validated_receipt = validate_test_receipt(receipt, prior_sha)
    feedback = producer_test_gate_feedback(validated_receipt, prior_sha)

    ctx = {
        "task": {"task_id": "task-test-123"},
        "source_head": "0123456789abcdef0123456789abcdef01234567",
        "continuation_run_prefix": "run-cont-456",
        "artifacts": {
            "correction_origin": "TEST_FAILED",
            "worker_index": 1,
            "final_worker_run_id": "run-orig-worker-0",
            "semantic_task_fingerprint": "fingerprint-abc",
            "proposal_sha256": prior_sha,
            "pre_review_test_state": "FAILED",
            "reviewer_path": None,
            "final_reviewer_run_id": None,
        },
        "evidence": {
            "worker_info": prior_info,
            "feedback": feedback,
            "receipt": receipt,
            "reviewer_status": None,
            "reviewer_result": None,
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
    return ctx, returned_state, validated, receipt


def test_bind_worker_one_result_bootstrap_success():
    ctx, returned_state, validated, bootstrap_findings = _make_bootstrap_bundle()
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
    assert res["seen_convs"] == {"conv-worker-0", "conv-worker-1"}
    assert res["seen_norm_findings"] == set()

    expected_sig = hashlib.sha256(
        json.dumps(bootstrap_findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert res["seen_bootstrap_signatures"] == {expected_sig}
    assert res["seen_test_signatures"] == set()

    assert res["worker_info"]["result"] is not validated[0]
    assert res["worker_info"]["status"] is not validated[1]
    assert res["returned_state"] is not returned_state
    res["worker_info"]["result"]["status"] = "MUTATED"
    assert validated[0]["status"] == "PROPOSAL_READY"


def test_bind_worker_one_result_test_failed_success():
    ctx, returned_state, validated, receipt = _make_test_failed_bundle()
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
    assert res["seen_convs"] == {"conv-worker-0", "conv-worker-1"}
    assert res["seen_norm_findings"] == set()

    val_receipt = validate_test_receipt(receipt, ctx["artifacts"]["proposal_sha256"])
    expected_sig = producer_test_failure_signature(val_receipt)
    assert res["seen_test_signatures"] == {expected_sig}
    assert res["seen_bootstrap_signatures"] == set()

    assert res["worker_info"]["result"] is not validated[0]
    assert res["worker_info"]["status"] is not validated[1]
    assert res["returned_state"] is not returned_state
    res["returned_state"]["state"] = "MUTATED"
    assert returned_state["state"] == "WORKER_PROPOSAL_READY"

    tf_ctx, tf_ret, tf_val, _ = _make_test_failed_bundle()
    tf_ctx["evidence"]["test_receipt"] = tf_ctx["evidence"].pop("receipt")
    res_alias = bind_worker_one_result(tf_ctx, tf_ret, tf_val)
    assert res_alias["seen_test_signatures"] == {expected_sig}


def test_reject_wrong_feedback_or_sha():
    ctx, returned_state, validated, _ = _make_bootstrap_bundle()

    bad_ctx1 = copy.deepcopy(ctx)
    bad_ctx1["evidence"]["feedback"]["reviewed_proposal_sha256"] = "0" * 64
    with pytest.raises(ProposalError, match="reviewed_proposal_sha256 does not match"):
        bind_worker_one_result(bad_ctx1, returned_state, validated)

    bad_ctx2 = copy.deepcopy(ctx)
    bad_ctx2["evidence"]["worker_info"]["result_sha"] = "0" * 64
    with pytest.raises(ProposalError, match="prior worker_info result_sha does not match"):
        bind_worker_one_result(bad_ctx2, returned_state, validated)

    bad_ctx3 = copy.deepcopy(ctx)
    bad_ctx3["artifacts"]["proposal_sha256"] = "invalid-sha"
    with pytest.raises(ProposalError, match="artifacts proposal_sha256 invalid"):
        bind_worker_one_result(bad_ctx3, returned_state, validated)

    bad_ctx4 = copy.deepcopy(ctx)
    bad_ctx4["evidence"]["feedback"]["reviewer"] = "gemini"
    with pytest.raises(ProposalError, match="feedback reviewer must be 'test-gate'"):
        bind_worker_one_result(bad_ctx4, returned_state, validated)


def test_reject_non_none_reviewer_fields_for_test_gate():
    for maker in (_make_bootstrap_bundle, _make_test_failed_bundle):
        ctx, returned_state, validated, _ = maker()

        bad_art1 = copy.deepcopy(ctx)
        bad_art1["artifacts"]["reviewer_path"] = ".local/two-site-refactor/proposals/r/result.json"
        with pytest.raises(ProposalError, match="artifacts reviewer_path must be None"):
            bind_worker_one_result(bad_art1, returned_state, validated)

        bad_art2 = copy.deepcopy(ctx)
        bad_art2["artifacts"]["final_reviewer_run_id"] = "run-reviewer-0"
        with pytest.raises(ProposalError, match="artifacts final_reviewer_run_id must be None"):
            bind_worker_one_result(bad_art2, returned_state, validated)

        bad_ev1 = copy.deepcopy(ctx)
        bad_ev1["evidence"]["reviewer_status"] = {"conversation_id": "conv-rev-0"}
        with pytest.raises(ProposalError, match="evidence reviewer_status must be None"):
            bind_worker_one_result(bad_ev1, returned_state, validated)

        bad_ev2 = copy.deepcopy(ctx)
        bad_ev2["evidence"]["reviewer_result"] = {"findings": []}
        with pytest.raises(ProposalError, match="evidence reviewer_result must be None"):
            bind_worker_one_result(bad_ev2, returned_state, validated)

        for sha_key in ("reviewer_sha", "reviewer_sha256", "reviewer_result_sha"):
            bad_ev3 = copy.deepcopy(ctx)
            bad_ev3["evidence"][sha_key] = "a" * 64
            with pytest.raises(ProposalError, match="evidence reviewer sha must be None"):
                bind_worker_one_result(bad_ev3, returned_state, validated)


def test_reject_receipt_faults_for_bootstrap_and_test_failed():
    boot_ctx, boot_ret, boot_val, _ = _make_bootstrap_bundle()
    bad_boot1 = copy.deepcopy(boot_ctx)
    bad_boot1["evidence"]["receipt"] = {"state": "FAILED"}
    with pytest.raises(ProposalError, match="BOOTSTRAP origin requires receipt to be None"):
        bind_worker_one_result(bad_boot1, boot_ret, boot_val)

    bad_boot2 = copy.deepcopy(boot_ctx)
    bad_boot2["evidence"]["test_receipt"] = {"state": "FAILED"}
    with pytest.raises(ProposalError, match="BOOTSTRAP origin requires receipt to be None"):
        bind_worker_one_result(bad_boot2, boot_ret, boot_val)

    for field in ("pre_review_test_path", "pre_review_test_sha256", "pre_review_test_state"):
        bad_boot_art = copy.deepcopy(boot_ctx)
        bad_boot_art["artifacts"][field] = "not-none"
        with pytest.raises(ProposalError, match="BOOTSTRAP artifacts pre_review_test fields must be None"):
            bind_worker_one_result(bad_boot_art, boot_ret, boot_val)

    tf_ctx, tf_ret, tf_val, _ = _make_test_failed_bundle()
    bad_tf1 = copy.deepcopy(tf_ctx)
    bad_tf1["evidence"]["receipt"] = None
    bad_tf1["evidence"].pop("test_receipt", None)
    with pytest.raises(ProposalError, match="evidence missing receipt for TEST_FAILED origin"):
        bind_worker_one_result(bad_tf1, tf_ret, tf_val)

    bad_tf2 = copy.deepcopy(tf_ctx)
    bad_tf2["evidence"]["test_receipt"] = {"state": "FAILED", "different": True}
    with pytest.raises(ProposalError, match="evidence contains conflicting receipt and test_receipt"):
        bind_worker_one_result(bad_tf2, tf_ret, tf_val)

    bad_tf3 = copy.deepcopy(tf_ctx)
    bad_tf3["evidence"]["receipt"] = dict(
        bad_tf3["evidence"]["receipt"], state="PASSED", returncode=0
    )
    with pytest.raises(ProposalError, match="validated test_receipt state must be 'FAILED'"):
        bind_worker_one_result(bad_tf3, tf_ret, tf_val)

    bad_tf4 = copy.deepcopy(tf_ctx)
    bad_tf4["artifacts"]["pre_review_test_state"] = "PASSED"
    with pytest.raises(ProposalError, match="artifacts pre_review_test_state must be 'FAILED'"):
        bind_worker_one_result(bad_tf4, tf_ret, tf_val)

    bad_tf5 = copy.deepcopy(tf_ctx)
    bad_tf5["evidence"]["receipt"] = "not-a-dict"
    with pytest.raises(ProposalError, match="receipt must be dict"):
        bind_worker_one_result(bad_tf5, tf_ret, tf_val)


def test_reject_unknown_or_mixed_origin_and_duplicate_conv():
    ctx, returned_state, validated, _ = _make_bootstrap_bundle()

    bad_orig = copy.deepcopy(ctx)
    bad_orig["artifacts"]["correction_origin"] = "UNKNOWN_ORIGIN"
    with pytest.raises(ProposalError, match="unknown correction_origin"):
        bind_worker_one_result(bad_orig, returned_state, validated)

    bad_mix1 = copy.deepcopy(ctx)
    bad_mix1["evidence"]["correction_origin"] = "TEST_FAILED"
    with pytest.raises(ProposalError, match="mixed correction_origin between artifacts and evidence"):
        bind_worker_one_result(bad_mix1, returned_state, validated)

    bad_mix2 = copy.deepcopy(ctx)
    bad_mix2["correction_origin"] = "TEST_FAILED"
    with pytest.raises(ProposalError, match="mixed correction_origin between artifacts and ctx"):
        bind_worker_one_result(bad_mix2, returned_state, validated)

    curr_result, curr_status, curr_sha, curr_text = validated
    dup_status = dict(curr_status, conversation_id="conv-worker-0")
    with pytest.raises(ProposalError, match="current worker conversation must differ from prior Worker"):
        bind_worker_one_result(ctx, returned_state, (curr_result, dup_status, curr_sha, curr_text))


def test_reject_tampered_findings():
    boot_ctx, boot_ret, boot_val, _ = _make_bootstrap_bundle()
    bad_boot1 = copy.deepcopy(boot_ctx)
    bad_boot1["evidence"]["feedback"]["findings"] = ["tampered finding"]
    with pytest.raises(ProposalError, match="test-gate feedback findings do not match expected findings"):
        bind_worker_one_result(bad_boot1, boot_ret, boot_val)

    bad_boot2 = copy.deepcopy(boot_ctx)
    bad_boot2["evidence"]["feedback"]["test_evidence"] = ["gate=tampered"]
    with pytest.raises(ProposalError, match="test-gate feedback test_evidence does not match expected evidence"):
        bind_worker_one_result(bad_boot2, boot_ret, boot_val)

    bad_boot3 = copy.deepcopy(boot_ctx)
    bad_boot3["evidence"]["feedback"]["unexpected"] = "field"
    with pytest.raises(ProposalError, match="test-gate feedback does not match accepted producer payload"):
        bind_worker_one_result(bad_boot3, boot_ret, boot_val)

    tf_ctx, tf_ret, tf_val, _ = _make_test_failed_bundle()
    bad_tf = copy.deepcopy(tf_ctx)
    bad_tf["evidence"]["feedback"]["findings"] = ["tampered finding"]
    with pytest.raises(ProposalError, match="feedback does not match expected test_gate_feedback payload"):
        bind_worker_one_result(bad_tf, tf_ret, tf_val)
