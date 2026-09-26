"""Unit tests for Worker-2 file-backed result reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_two_file_reader import (
    _EXPECTED_OUTPUT_KEYS,
    _EXPECTED_RESULT_STATE_KEYS,
    _EXPECTED_WORKER_INFO_KEYS,
    read_and_bind_worker_two_result,
)


def _make_valid_test_context(root: Path) -> tuple[dict, dict]:
    prefix = "testpfx-20260926"
    w2_run_id = f"{prefix}-worker-2"
    w1_run_id = f"{prefix}-worker-1"
    prior_sha = "11" * 32
    w2_sha = "22" * 32
    head = "33" * 20
    t_id = "TASK-AE03-W2"
    fingerprint = "fp-ae03-999"

    run_dir = root / ".local/two-site-refactor/proposals" / w2_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result_file = run_dir / "result.json"
    result_file.write_text('{"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"}')

    artifacts = {
        "worker_index": 2,
        "original_run_prefix": prefix,
        "final_worker_run_id": w1_run_id,
        "proposal_sha256": prior_sha,
        "semantic_task_fingerprint": fingerprint,
    }
    prior_worker_info = {
        "result": {"changes": [{"path": "scripts/old.py"}], "status": "PROPOSAL_READY"},
        "status": {"run_id": w1_run_id, "result_sha256": prior_sha, "conversation_id": "conv-w1"},
        "result_sha": prior_sha,
        "result_text": '{"changes": [{"path": "scripts/old.py"}]}',
        "conversation_id": "conv-w1",
    }
    evidence = {
        "feedback_sha": "44" * 32,
        "worker_info": prior_worker_info,
    }
    ctx = {
        "continuation_run_prefix": prefix,
        "source_head": head,
        "task": {"task_id": t_id},
        "sources": {"scripts/new.py": "abc"},
        "admitted_task_path": "task.json",
        "artifacts": artifacts,
        "evidence": evidence,
    }
    returned_state = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": t_id,
        "source_head": head,
        "final_worker_run_id": w2_run_id,
        "proposal_sha256": w2_sha,
        "changed_paths": ["scripts/new.py"],
        "proposal_path": f".local/two-site-refactor/proposals/{w2_run_id}/result.json",
        "correction_count": 2,
        "semantic_task_fingerprint": fingerprint,
    }
    return ctx, returned_state


def test_read_and_bind_worker_two_result_reviewer_backed_success(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ctx, returned_state = _make_valid_test_context(root)
    w2_run_id = returned_state["final_worker_run_id"]
    w2_sha = returned_state["proposal_sha256"]

    mock_history = {
        "complete": True,
        "ordered_conversation_ids": ["conv-w0", "conv-r0", "conv-w1", "conv-r1"],
    }
    mock_validated = (
        {"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"},
        {"run_id": w2_run_id, "conversation_id": "conv-w2", "state": "PROPOSAL_READY"},
        w2_sha,
        '{"changes": [{"path": "scripts/new.py"}]}',
    )

    def _mock_validate_run(*args: Any, **kwargs: Any) -> tuple[dict, dict, str, str]:
        if "seen_convs" in kwargs and isinstance(kwargs["seen_convs"], set):
            kwargs["seen_convs"].add("conv-w2")
        return mock_validated

    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value=mock_history,
    ) as mock_hist, mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.validate_run",
        side_effect=_mock_validate_run,
    ) as mock_val:
        out = read_and_bind_worker_two_result(root, ctx, returned_state)

    assert set(out.keys()) == _EXPECTED_OUTPUT_KEYS
    worker_info = out["worker_info"]
    assert set(worker_info.keys()) == _EXPECTED_WORKER_INFO_KEYS
    assert worker_info["result"] == {"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"}
    assert worker_info["conversation_id"] == "conv-w2"
    assert worker_info["result_sha"] == w2_sha

    assert out["current_worker"] == (w2_run_id, w2_sha, '{"changes": [{"path": "scripts/new.py"}]}', "conv-w2")
    assert out["seen_convs"] == {"conv-w0", "conv-r0", "conv-w1", "conv-r1", "conv-w2"}
    assert out["complete_history"] == mock_history
    assert set(out["returned_state"].keys()) == _EXPECTED_RESULT_STATE_KEYS
    assert out["returned_state"]["correction_count"] == 2
    assert out["returned_state"]["state"] == "WORKER_PROPOSAL_READY"

    mock_hist.assert_called_once_with(root, ctx["admitted_task_path"], ctx["artifacts"], ctx["evidence"])
    assert mock_val.call_count == 1


def test_read_and_bind_worker_two_result_state_and_lineage_drift(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ctx, returned_state = _make_valid_test_context(root)

    st_wrong_count = dict(returned_state, correction_count=1)
    with pytest.raises(ProposalError, match="correction_count must be exact int 2"):
        read_and_bind_worker_two_result(root, ctx, st_wrong_count)

    st_wrong_state = dict(returned_state, state="READY_FOR_CODEX")
    with pytest.raises(ProposalError, match="state must be 'WORKER_PROPOSAL_READY'"):
        read_and_bind_worker_two_result(root, ctx, st_wrong_state)

    st_wrong_run_id = dict(returned_state, final_worker_run_id="other-worker-2")
    with pytest.raises(ProposalError, match="final_worker_run_id mismatch"):
        read_and_bind_worker_two_result(root, ctx, st_wrong_run_id)

    st_wrong_task = dict(returned_state, task_id="OTHER-TASK")
    with pytest.raises(ProposalError, match="task_id mismatch"):
        read_and_bind_worker_two_result(root, ctx, st_wrong_task)

    st_wrong_fp = dict(returned_state, semantic_task_fingerprint="wrong-fp")
    with pytest.raises(ProposalError, match="semantic_task_fingerprint mismatch"):
        read_and_bind_worker_two_result(root, ctx, st_wrong_fp)

    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value={"complete": False},
    ):
        with pytest.raises(ProposalError, match="worker-2 history must be complete"):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value={"complete": True, "ordered_conversation_ids": []},
    ):
        with pytest.raises(ProposalError, match="missing or empty ordered_conversation_ids"):
            read_and_bind_worker_two_result(root, ctx, returned_state)


def test_read_and_bind_worker_two_result_duplicate_conversation(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ctx, returned_state = _make_valid_test_context(root)

    # 1. Duplicate conversation IDs in complete history
    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value={"complete": True, "ordered_conversation_ids": ["conv-w0", "conv-w0"]},
    ):
        with pytest.raises(ProposalError, match="duplicate conversation IDs in history"):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    # 2. Duplicate W2 conversation reusing prior history conversation ID
    mock_history = {"complete": True, "ordered_conversation_ids": ["conv-w0", "conv-w1"]}
    dup_conv_val = (
        {"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"},
        {"run_id": returned_state["final_worker_run_id"], "conversation_id": "conv-w1"},
        "66" * 32,
        '{"changes": [{"path": "scripts/new.py"}]}',
    )
    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value=mock_history,
    ), mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.validate_run",
        return_value=dup_conv_val,
    ):
        with pytest.raises(ProposalError, match="reused conversation_id"):
            read_and_bind_worker_two_result(root, ctx, returned_state)


def test_read_and_bind_worker_two_result_stale_sha_and_unchanged_correction(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ctx, returned_state = _make_valid_test_context(root)
    prior_sha = ctx["artifacts"]["proposal_sha256"]
    mock_history = {"complete": True, "ordered_conversation_ids": ["conv-w0", "conv-w1"]}

    stale_val = (
        {"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"},
        {"run_id": returned_state["final_worker_run_id"], "conversation_id": "conv-w2"},
        prior_sha,
        '{"changes": [{"path": "scripts/new.py"}]}',
    )
    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value=mock_history,
    ), mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.validate_run",
        return_value=stale_val,
    ):
        with pytest.raises(ProposalError, match="correction proposal SHA must differ from prior"):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    unchanged_changes_val = (
        {"changes": [{"path": "scripts/old.py"}], "status": "PROPOSAL_READY"},
        {"run_id": returned_state["final_worker_run_id"], "conversation_id": "conv-w2"},
        "55" * 32,
        '{"changes": [{"path": "scripts/old.py"}]}',
    )
    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value=mock_history,
    ), mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.validate_run",
        return_value=unchanged_changes_val,
    ):
        with pytest.raises(ProposalError, match="correction proposal changes must differ from prior"):
            read_and_bind_worker_two_result(root, ctx, returned_state)

    st_mismatched_sha = dict(returned_state, proposal_sha256="99" * 32)
    diff_sha_val = (
        {"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"},
        {"run_id": returned_state["final_worker_run_id"], "conversation_id": "conv-w2"},
        "77" * 32,
        '{"changes": [{"path": "scripts/new.py"}]}',
    )
    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value=mock_history,
    ), mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.validate_run",
        return_value=diff_sha_val,
    ):
        with pytest.raises(ProposalError, match="returned_state proposal_sha256 mismatch"):
            read_and_bind_worker_two_result(root, ctx, st_mismatched_sha)


def test_read_and_bind_worker_two_result_symlink_and_noncanonical(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    ctx, returned_state = _make_valid_test_context(root)
    run_dir = root / ".local/two-site-refactor/proposals" / returned_state["final_worker_run_id"]
    real_dir = tmp_path / "other_run_dir"
    real_dir.mkdir(parents=True, exist_ok=True)

    # Symlinked result file
    res_file = run_dir / "result.json"
    res_file.unlink()
    other_res = tmp_path / "other_result.json"
    other_res.write_text('{"changes": [], "status": "PROPOSAL_READY"}')
    res_file.symlink_to(other_res)
    with pytest.raises(ProposalError, match="result file cannot be a symlink"):
        read_and_bind_worker_two_result(root, ctx, returned_state)

    res_file.unlink()
    res_file.write_text('{"changes": [], "status": "PROPOSAL_READY"}')

    # Symlinked run_dir
    for p in run_dir.glob("*"):
        p.unlink()
    run_dir.rmdir()
    run_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ProposalError, match="run_dir cannot be a symlink"):
        read_and_bind_worker_two_result(root, ctx, returned_state)


def test_read_and_bind_worker_two_result_changed_paths_mismatch_and_pending_proof(tmp_path: Path) -> None:
    """Verify returned_state changed_paths mismatch fails closed before any final route,

    and note that full materialized history proof remains pending without live replay.
    """
    root = tmp_path.resolve()
    ctx, returned_state = _make_valid_test_context(root)
    mock_history = {
        "complete": True,
        "ordered_conversation_ids": ["conv-w0", "conv-w1"],
    }
    validated_worker = (
        {"changes": [{"path": "scripts/new.py"}], "status": "PROPOSAL_READY"},
        {"run_id": returned_state["final_worker_run_id"], "conversation_id": "conv-w2"},
        returned_state["proposal_sha256"],
        '{"changes": [{"path": "scripts/new.py"}]}',
    )
    st_drifted = dict(returned_state, changed_paths=["scripts/drifted.py"])
    with mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.complete_worker_two_history",
        return_value=mock_history,
    ), mock.patch(
        "scripts.two_site_quota_resume_worker_two_file_reader.validate_run",
        return_value=validated_worker,
    ):
        with pytest.raises(ProposalError, match="returned_state changed_paths mismatch"):
            read_and_bind_worker_two_result(root, ctx, st_drifted)

    # Unhooked contract: state remains intermediate WORKER_PROPOSAL_READY with correction_count=2;
    # full materialized history proof remains pending and no live route or replay authority is claimed.
    assert returned_state["state"] == "WORKER_PROPOSAL_READY"
    assert returned_state["correction_count"] == 2
