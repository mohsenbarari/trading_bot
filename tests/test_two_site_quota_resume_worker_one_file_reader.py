"""Tests for Worker-1 file-backed run reader and result binder."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_one_file_reader import (
    read_and_bind_worker_one_result,
)
import tests.test_two_site_quota_resume_worker_one_result_contract as contract_module


def _make_valid_context_and_state(
    continuation_run_prefix: str = "cont-run-42",
) -> tuple[dict[str, Any], dict[str, Any], tuple[dict[str, Any], dict[str, Any], str, str]]:
    expected_run_id = f"{continuation_run_prefix}-worker-1"
    prior_run_id = f"{continuation_run_prefix}-worker-0"

    prior_result_dict = {
        "status": "PROPOSAL_READY",
        "summary": "prior proposal",
        "covered_requirements": [1],
        "findings": [],
        "changes": [{"path": "file_a.py", "original_sha256": None, "content": "a", "replacements": []}],
    }
    prior_text = json.dumps(prior_result_dict, separators=(",", ":"))
    prior_sha = hashlib.sha256(prior_text.encode("utf-8")).hexdigest()

    curr_result_dict = {
        "status": "PROPOSAL_READY",
        "summary": "current proposal",
        "covered_requirements": [1, 2],
        "findings": [],
        "changes": [{"path": "file_b.py", "original_sha256": None, "content": "b", "replacements": []}],
    }
    curr_text = json.dumps(curr_result_dict, separators=(",", ":"))
    curr_sha = hashlib.sha256(curr_text.encode("utf-8")).hexdigest()

    feedback_sha = "f" * 64
    fingerprint = "e" * 64
    head = "b" * 40

    ctx: dict[str, Any] = {
        "continuation_run_prefix": continuation_run_prefix,
        "source_head": head,
        "task": {"task_id": "TASK-123", "goal": "test goal"},
        "sources": {"file_a.py": {"sha256": "1" * 64, "content": "x"}},
        "artifacts": {
            "worker_index": 1,
            "final_worker_run_id": prior_run_id,
            "proposal_sha256": prior_sha,
            "semantic_task_fingerprint": fingerprint,
        },
        "evidence": {
            "feedback_sha": feedback_sha,
            "worker_info": {
                "result": prior_result_dict,
                "status": {"conversation_id": "conv-worker-0", "result_sha256": prior_sha},
                "result_sha": prior_sha,
                "result_text": prior_text,
                "conversation_id": "conv-worker-0",
            },
            "reviewer_status": {
                "conversation_id": "conv-reviewer-0",
            },
            "feedback": {
                "reviewer": "gemini",
                "findings": ["finding 1"],
            },
            "reviewer_result": {
                "findings": ["finding 1"],
            },
        },
    }

    proposal_path = f".local/two-site-refactor/proposals/{expected_run_id}/result.json"
    returned_state: dict[str, Any] = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": "TASK-123",
        "source_head": head,
        "final_worker_run_id": expected_run_id,
        "proposal_sha256": curr_sha,
        "changed_paths": ["file_b.py"],
        "proposal_path": proposal_path,
        "correction_count": 1,
        "semantic_task_fingerprint": fingerprint,
    }

    curr_status = {
        "run_id": expected_run_id,
        "role": "worker",
        "conversation_id": "conv-worker-1",
        "result_sha256": curr_sha,
    }
    validated = (curr_result_dict, curr_status, curr_sha, curr_text)
    return ctx, returned_state, validated


def test_read_and_bind_worker_one_result_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    run_dir.mkdir(parents=True)

    ctx, returned_state, validated = _make_valid_context_and_state()
    ctx_before = copy.deepcopy(ctx)
    state_before = copy.deepcopy(returned_state)

    calls: list[dict[str, Any]] = []

    def fake_validate_run(
        root_arg: Path,
        run_dir_arg: Path,
        role_arg: str,
        expected_run_id_arg: str,
        source_head_arg: str,
        task_arg: dict,
        sources_arg: dict,
        seen_convs: set[str],
        prior_worker: tuple[str, str, str, str],
        expected_feedback_sha: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        calls.append({
            "root": root_arg,
            "run_dir": run_dir_arg,
            "role": role_arg,
            "expected_run_id": expected_run_id_arg,
            "source_head": source_head_arg,
            "task": task_arg,
            "sources": sources_arg,
            "seen_convs": set(seen_convs),
            "prior_worker": prior_worker,
            "expected_feedback_sha": expected_feedback_sha,
        })
        return validated

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        fake_validate_run,
    )

    res = read_and_bind_worker_one_result(root, ctx, returned_state)

    assert len(calls) == 1
    call = calls[0]
    assert call["root"] == root
    assert call["run_dir"] == run_dir
    assert call["role"] == "worker"
    assert call["expected_run_id"] == "cont-run-42-worker-1"
    assert call["source_head"] == ctx["source_head"]
    assert call["task"] == ctx["task"]
    assert call["sources"] == ctx["sources"]
    assert call["seen_convs"] == {"conv-worker-0", "conv-reviewer-0"}
    assert call["prior_worker"] == (
        "cont-run-42-worker-0",
        ctx["artifacts"]["proposal_sha256"],
        ctx["evidence"]["worker_info"]["result_text"],
        "conv-worker-0",
    )
    assert call["expected_feedback_sha"] == "f" * 64

    expected_keys = {
        "worker_info",
        "returned_state",
        "current_worker",
        "seen_convs",
        "seen_norm_findings",
        "seen_test_signatures",
        "seen_bootstrap_signatures",
    }
    assert set(res.keys()) == expected_keys
    assert res["current_worker"] == (
        "cont-run-42-worker-1",
        validated[2],
        validated[3],
        "conv-worker-1",
    )
    assert res["seen_convs"] == {"conv-worker-0", "conv-reviewer-0", "conv-worker-1"}
    assert ctx == ctx_before
    assert returned_state == state_before


def test_mismatched_current_sha_and_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    run_dir.mkdir(parents=True)
    ctx, returned_state, validated = _make_valid_context_and_state()
    prior_sha = ctx["artifacts"]["proposal_sha256"]
    prior_changes = ctx["evidence"]["worker_info"]["result"]["changes"]

    curr_res, curr_st, _, curr_txt = validated
    same_sha_validated = (curr_res, curr_st, prior_sha, curr_txt)
    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        lambda *args, **kwargs: same_sha_validated,
    )
    with pytest.raises(ProposalError, match="correction proposal SHA must differ"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    same_changes_res = copy.deepcopy(curr_res)
    same_changes_res["changes"] = copy.deepcopy(prior_changes)
    same_changes_validated = (same_changes_res, curr_st, validated[2], curr_txt)
    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        lambda *args, **kwargs: same_changes_validated,
    )
    with pytest.raises(ProposalError, match="correction proposal changes must differ"):
        read_and_bind_worker_one_result(root, ctx, returned_state)


def test_malformed_prior_worker_info(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    run_dir.mkdir(parents=True)

    ctx, returned_state, _ = _make_valid_context_and_state()
    ctx["evidence"]["worker_info"]["extra_key"] = 123
    with pytest.raises(ProposalError, match="prior worker_info keys mismatch"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    ctx, returned_state, _ = _make_valid_context_and_state()
    del ctx["evidence"]["worker_info"]["status"]
    with pytest.raises(ProposalError, match="prior worker_info keys mismatch"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    ctx, returned_state, _ = _make_valid_context_and_state()
    ctx["evidence"]["worker_info"]["result_sha"] = "0" * 64
    with pytest.raises(ProposalError, match="prior result_sha mismatch"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    ctx, returned_state, _ = _make_valid_context_and_state()
    ctx["evidence"]["worker_info"]["status"]["result_sha256"] = "1" * 64
    with pytest.raises(ProposalError, match="prior status result_sha256 does not match"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    ctx, returned_state, _ = _make_valid_context_and_state()
    ctx["evidence"]["worker_info"]["result"]["changes"] = "not-a-list"
    with pytest.raises(ProposalError, match="changes.*must be a list"):
        read_and_bind_worker_one_result(root, ctx, returned_state)


def test_symlink_and_missing_run_dir(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ctx, returned_state, _ = _make_valid_context_and_state()

    with pytest.raises(ProposalError, match="run_dir does not exist"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    real_dir = root / "external_dir"
    real_dir.mkdir()
    symlink_run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    symlink_run_dir.parent.mkdir(parents=True, exist_ok=True)
    symlink_run_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ProposalError, match="run_dir cannot be a symlink"):
        read_and_bind_worker_one_result(root, ctx, returned_state)


def test_returned_state_mismatches_before_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    run_dir.mkdir(parents=True)

    call_count = 0

    def fail_if_called(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise AssertionError("validate_run should not have been called")

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        fail_if_called,
    )

    ctx, returned_state, _ = _make_valid_context_and_state()
    returned_state["final_worker_run_id"] = "wrong-run-id"
    with pytest.raises(ProposalError, match="final_worker_run_id mismatch"):
        read_and_bind_worker_one_result(root, ctx, returned_state)
    assert call_count == 0

    ctx, returned_state, _ = _make_valid_context_and_state()
    returned_state["proposal_path"] = "wrong/path/result.json"
    with pytest.raises(ProposalError, match="proposal_path mismatch"):
        read_and_bind_worker_one_result(root, ctx, returned_state)
    assert call_count == 0


def test_validate_run_error_propagation_and_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    run_dir.mkdir(parents=True)
    ctx, returned_state, validated = _make_valid_context_and_state()

    def raise_err(*args, **kwargs):
        raise ProposalError("pipeline inner validation failed")

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        raise_err,
    )
    with pytest.raises(ProposalError, match="pipeline inner validation failed"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        lambda *args, **kwargs: validated[:3],  # 3-tuple instead of 4-tuple
    )
    with pytest.raises(ProposalError, match="must return a 4-tuple"):
        read_and_bind_worker_one_result(root, ctx, returned_state)


def test_reviewer_conversation_validation(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    run_dir.mkdir(parents=True)

    ctx, returned_state, _ = _make_valid_context_and_state()
    del ctx["evidence"]["reviewer_status"]
    with pytest.raises(ProposalError, match="missing or invalid reviewer_status"):
        read_and_bind_worker_one_result(root, ctx, returned_state)

    ctx, returned_state, _ = _make_valid_context_and_state()
    ctx["evidence"]["reviewer_status"]["conversation_id"] = "conv-worker-0"
    with pytest.raises(ProposalError, match="reviewer conversation_id must differ"):
        read_and_bind_worker_one_result(root, ctx, returned_state)


def test_canonical_root_validation(tmp_path: Path) -> None:
    ctx, returned_state, _ = _make_valid_context_and_state()

    with pytest.raises(ProposalError, match="root must be a Path"):
        read_and_bind_worker_one_result("/not/a/path", ctx, returned_state)  # type: ignore[arg-type]

    with pytest.raises(ProposalError, match="root directory does not exist"):
        read_and_bind_worker_one_result(tmp_path / "nonexistent", ctx, returned_state)

    dummy_file = tmp_path / "file.txt"
    dummy_file.write_text("hi")
    with pytest.raises(ProposalError, match="root must be a resolved canonical directory"):
        read_and_bind_worker_one_result(dummy_file, ctx, returned_state)


def test_no_provider_or_lock_authority_in_file_reader() -> None:
    import inspect
    import scripts.two_site_quota_resume_worker_one_file_reader as module

    source = inspect.getsource(module)
    forbidden_tokens = (
        "pipeline_lock",
        "runner_run",
        "run_provider_session",
        "import socket",
        "from socket",
    )
    for token in forbidden_tokens:
        assert token not in source, f"Found unauthorized token {token!r} in reader implementation"


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
def test_read_and_bind_test_gate_origins_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    if origin == "BOOTSTRAP":
        ctx, returned_state, validated, findings = (
            contract_module._make_bootstrap_bundle()
        )
        expected_sig = hashlib.sha256(
            json.dumps(findings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    else:
        ctx, returned_state, validated, receipt = (
            contract_module._make_test_failed_bundle()
        )
        val_receipt = contract_module.validate_test_receipt(
            receipt, ctx["artifacts"]["proposal_sha256"]
        )
        expected_sig = contract_module.producer_test_failure_signature(val_receipt)

    root = tmp_path.resolve()
    expected_run_id = f"{ctx['continuation_run_prefix']}-worker-1"
    run_dir = root / f".local/two-site-refactor/proposals/{expected_run_id}"
    run_dir.mkdir(parents=True)

    ctx["sources"] = {"file_a.py": {"sha256": "1" * 64, "content": "x"}}
    feedback_sha = "f" * 64
    ctx["evidence"]["feedback_sha"] = feedback_sha

    calls: list[dict[str, Any]] = []

    def fake_validate_run(
        root_arg: Path,
        run_dir_arg: Path,
        role_arg: str,
        expected_run_id_arg: str,
        source_head_arg: str,
        task_arg: dict,
        sources_arg: dict,
        seen_convs: set[str],
        prior_worker: tuple[str, str, str, str],
        expected_feedback_sha: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        calls.append({
            "seen_convs": set(seen_convs),
            "prior_worker": prior_worker,
            "expected_feedback_sha": expected_feedback_sha,
        })
        return validated

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        fake_validate_run,
    )

    res = read_and_bind_worker_one_result(root, ctx, returned_state)

    assert len(calls) == 1
    call = calls[0]
    prior_conv = ctx["evidence"]["worker_info"]["conversation_id"]
    assert call["seen_convs"] == {prior_conv}
    assert "conv-reviewer-0" not in call["seen_convs"]
    assert call["prior_worker"] == (
        ctx["artifacts"]["final_worker_run_id"],
        ctx["artifacts"]["proposal_sha256"],
        ctx["evidence"]["worker_info"]["result_text"],
        prior_conv,
    )
    assert call["expected_feedback_sha"] == feedback_sha

    if origin == "BOOTSTRAP":
        assert res["seen_bootstrap_signatures"] == {expected_sig}
        assert res["seen_test_signatures"] == set()
    else:
        assert res["seen_test_signatures"] == {expected_sig}
        assert res["seen_bootstrap_signatures"] == set()
    assert res["seen_convs"] == {prior_conv, "conv-worker-1"}


def test_test_gate_fail_before_validate_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    run_dir = root / ".local/two-site-refactor/proposals/run-cont-456-worker-1"
    run_dir.mkdir(parents=True)

    def fail_if_validate_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("validate_run should not have been called")

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_one_file_reader.validate_run",
        fail_if_validate_called,
    )

    ctx, ret, _, _ = contract_module._make_bootstrap_bundle()
    ctx["sources"] = {"file_a.py": {"sha256": "1" * 64, "content": "x"}}
    ctx["evidence"]["feedback_sha"] = "f" * 64

    cases_reviewer_fields = [
        ({"artifacts": {"reviewer_path": ".local/r.json"}}, "artifacts reviewer_path must be None"),
        ({"artifacts": {"final_reviewer_run_id": "rev-0"}}, "artifacts final_reviewer_run_id must be None"),
        ({"evidence": {"reviewer_status": {"conversation_id": "conv-r"}}}, "evidence reviewer_status must be None"),
        ({"evidence": {"reviewer_result": {"findings": []}}}, "evidence reviewer_result must be None"),
        ({"evidence": {"reviewer_sha": "a" * 64}}, "evidence reviewer sha must be None"),
        ({"evidence": {"reviewer_sha256": "a" * 64}}, "evidence reviewer sha must be None"),
        ({"evidence": {"reviewer_result_sha": "a" * 64}}, "evidence reviewer sha must be None"),
    ]
    for patch_dict, err_match in cases_reviewer_fields:
        bad_ctx = copy.deepcopy(ctx)
        for section, fields in patch_dict.items():
            bad_ctx[section].update(fields)
        with pytest.raises(ProposalError, match=err_match):
            read_and_bind_worker_one_result(root, bad_ctx, ret)

    bad_ctx = copy.deepcopy(ctx)
    bad_ctx["evidence"]["feedback"]["reviewer"] = "gemini"
    with pytest.raises(ProposalError, match="feedback reviewer must be 'test-gate'"):
        read_and_bind_worker_one_result(root, bad_ctx, ret)

    bad_ctx = copy.deepcopy(ctx)
    bad_ctx["evidence"]["feedback"] = "not-a-dict"
    with pytest.raises(ProposalError, match="evidence missing or invalid feedback"):
        read_and_bind_worker_one_result(root, bad_ctx, ret)

    bad_ctx = copy.deepcopy(ctx)
    bad_ctx["artifacts"]["correction_origin"] = "UNKNOWN_ORIGIN"
    with pytest.raises(ProposalError, match="unknown correction_origin"):
        read_and_bind_worker_one_result(root, bad_ctx, ret)

    bad_ctx = copy.deepcopy(ctx)
    bad_ctx["evidence"]["correction_origin"] = "TEST_FAILED"
    with pytest.raises(ProposalError, match="mixed correction_origin between artifacts and evidence"):
        read_and_bind_worker_one_result(root, bad_ctx, ret)

    bad_ctx = copy.deepcopy(ctx)
    bad_ctx["correction_origin"] = "TEST_FAILED"
    with pytest.raises(ProposalError, match="mixed correction_origin between artifacts and ctx"):
        read_and_bind_worker_one_result(root, bad_ctx, ret)

    rev_ctx, rev_ret, _ = _make_valid_context_and_state()
    rev_run_dir = root / ".local/two-site-refactor/proposals/cont-run-42-worker-1"
    rev_run_dir.mkdir(parents=True, exist_ok=True)
    rev_ctx["evidence"]["reviewer_status"]["conversation_id"] = "conv-worker-0"
    with pytest.raises(ProposalError, match="reviewer conversation_id must differ"):
        read_and_bind_worker_one_result(root, rev_ctx, rev_ret)

    rev_ctx["artifacts"]["correction_origin"] = "REVIEWER"
    with pytest.raises(ProposalError, match="reviewer conversation_id must differ"):
        read_and_bind_worker_one_result(root, rev_ctx, rev_ret)
