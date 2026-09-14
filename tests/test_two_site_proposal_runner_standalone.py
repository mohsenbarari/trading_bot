from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_proposal_runner as runner


def test_run_standalone_success_and_retry_exhaustion(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    out_dir = tmp_path / "out"

    # 1. First-attempt success calls run once
    with mock.patch.object(runner, "run", return_value=out_dir) as mock_run:
        res = runner.run_standalone(tmp_path, task_path, "worker", "base-w", 30)
        assert res == out_dir
        mock_run.assert_called_once_with(
            tmp_path, task_path, "worker", "base-w", 30,
            feedback=None, proposal=None, cli=None, test_evidence=None,
        )

    # 2. Retryable failure recovers on attempt 2
    err1 = runner.RetryableProviderSessionError("fail-1")
    with mock.patch.object(runner, "run", side_effect=[err1, out_dir]) as mock_run:
        res = runner.run_standalone(tmp_path, task_path, "worker", "base-w", 30)
        assert res == out_dir
        assert [c.args[3] for c in mock_run.call_args_list] == ["base-w", "base-w-retry-1"]

    # 3. Retryable failure recovers on attempt 3
    err2 = runner.RetryableProviderSessionError("fail-2")
    with mock.patch.object(runner, "run", side_effect=[err1, err2, out_dir]) as mock_run:
        res = runner.run_standalone(tmp_path, task_path, "verifier", "base-v", 30)
        assert res == out_dir
        assert [c.args[3] for c in mock_run.call_args_list] == ["base-v", "base-v-retry-1", "base-v-retry-2"]

    # 4. Three failures raise plain ProposalError chained from final RetryableProviderSessionError
    err3 = runner.RetryableProviderSessionError("fail-3")
    with mock.patch.object(runner, "run", side_effect=[err1, err2, err3]) as mock_run:
        with pytest.raises(runner.ProposalError) as exc_info:
            runner.run_standalone(tmp_path, task_path, "worker", "base-w", 30)
        assert type(exc_info.value) is runner.ProposalError
        assert exc_info.value.__cause__ is err3
        assert "provider session retry exhausted for base-w after 3 attempts" in str(exc_info.value)
        assert [c.args[3] for c in mock_run.call_args_list] == ["base-w", "base-w-retry-1", "base-w-retry-2"]

    # 5. Ordinary ProposalError is not retried
    plain_err = runner.ProposalError("ordinary failure")
    with mock.patch.object(runner, "run", side_effect=plain_err) as mock_run:
        with pytest.raises(runner.ProposalError) as exc_info:
            runner.run_standalone(tmp_path, task_path, "worker", "base-w", 30)
        assert exc_info.value is plain_err
        mock_run.assert_called_once()

    # 6. Retryable provider output error recovers on attempt 2 (two attempts)
    out_err1 = runner.RetryableProviderOutputError("malformed output 1")
    with mock.patch.object(runner, "run", side_effect=[out_err1, out_dir]) as mock_run:
        res = runner.run_standalone(tmp_path, task_path, "worker", "base-w-out", 30)
        assert res == out_dir
        assert mock_run.call_count == 2
        assert [c.args[3] for c in mock_run.call_args_list] == ["base-w-out", "base-w-out-retry-1"]

    # 7. Two provider output errors stop immediately after two attempts without retry-2
    out_err2 = runner.RetryableProviderOutputError("malformed output 2")
    with mock.patch.object(runner, "run", side_effect=[out_err1, out_err2]) as mock_run:
        with pytest.raises(runner.ProposalError) as exc_info:
            runner.run_standalone(tmp_path, task_path, "worker", "base-w-out-stop", 30)
        assert type(exc_info.value) is runner.ProposalError
        assert not isinstance(exc_info.value, runner.RetryableProviderSessionError)
        assert exc_info.value.__cause__ is out_err2
        assert "repeated provider output failure for base-w-out-stop after 2 output attempts" in str(exc_info.value)
        assert mock_run.call_count == 2
        assert [c.args[3] for c in mock_run.call_args_list] == ["base-w-out-stop", "base-w-out-stop-retry-1"]


def test_existing_directory_fails_closed_without_overwrite(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    proposals_dir = tmp_path / ".local/two-site-refactor/proposals"

    # Base evidence directory exists before attempt 1
    base_dir = proposals_dir / "run-id"
    base_dir.mkdir(parents=True, exist_ok=True)
    sentinel_base = base_dir / "sentinel.txt"
    sentinel_base.write_text("keep_base", encoding="utf-8")

    with mock.patch.object(runner, "run") as mock_run:
        with pytest.raises(runner.ProposalError, match="worker run ID already exists: run-id"):
            runner.run_standalone(tmp_path, task_path, "worker", "run-id", 30)
        mock_run.assert_not_called()
        assert sentinel_base.read_text(encoding="utf-8") == "keep_base"

    # Retry directory exists before attempt 2
    retry_dir = proposals_dir / "run-v-retry-1"
    retry_dir.mkdir(parents=True, exist_ok=True)
    sentinel_retry = retry_dir / "sentinel.txt"
    sentinel_retry.write_text("keep_retry", encoding="utf-8")

    with mock.patch.object(runner, "run", side_effect=runner.RetryableProviderSessionError("fail-1")) as mock_run:
        with pytest.raises(runner.ProposalError, match="reviewer run ID already exists: run-v-retry-1"):
            runner.run_standalone(tmp_path, task_path, "verifier", "run-v", 30)
        assert mock_run.call_count == 1
        assert sentinel_retry.read_text(encoding="utf-8") == "keep_retry"


def test_main_routing_check_arguments_and_pipeline_delegation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")
    fake_out = tmp_path / "out"

    # Prove main routes worker/verifier through run_standalone
    for role, state in (("worker", "PROPOSAL_READY"), ("verifier", "VERIFIED")):
        monkeypatch.setattr(sys, "argv", ["runner.py", role, "--task", str(task_file), "--run-id", f"r-{role}", "--root", str(tmp_path)])
        with mock.patch.object(runner, "run_standalone", return_value=fake_out) as mock_standalone, \
             mock.patch.object(runner, "bounded_file_text", return_value="{}"), \
             mock.patch.object(runner, "unique_json", return_value={"state": state, "duration_seconds": 5}):
            assert runner.main() == 0
            assert mock_standalone.call_args[0][2:4] == (role, f"r-{role}")

    # Check routes only through check
    monkeypatch.setattr(sys, "argv", ["runner.py", "check", "--task", str(task_file), "--root", str(tmp_path)])
    with mock.patch.object(runner, "check", return_value={"ok": True}) as mock_check, \
         mock.patch.object(runner, "run_standalone") as mock_standalone:
        assert runner.main() == 0
        mock_check.assert_called_once_with(tmp_path, task_file)
        mock_standalone.assert_not_called()

    # Rejection of incompatible check arguments
    for bad_flag in ("--run-id", "--feedback", "--proposal", "--cli", "--test-evidence"):
        monkeypatch.setattr(sys, "argv", ["runner.py", "check", "--task", str(task_file), "--root", str(tmp_path), bad_flag, "val"])
        buf = io.StringIO()
        with mock.patch.object(runner, "check") as mock_check, \
             mock.patch.object(runner, "run_standalone") as mock_standalone, \
             mock.patch("sys.stdout", buf):
            assert runner.main() == 1
            mock_check.assert_not_called()
            mock_standalone.assert_not_called()
            assert "check mode does not accept run-id, feedback, proposal, cli, or test-evidence arguments" in buf.getvalue()

    # Prove pipeline.runner_run is runner.run and run_provider_session retains exactly three attempts
    assert pipeline.runner_run is runner.run

    pipe_err = runner.RetryableProviderSessionError("pipe-fail")
    with mock.patch.object(pipeline, "runner_run", side_effect=pipe_err) as mock_pipe_run:
        with pytest.raises(runner.ProposalError) as exc_info:
            pipeline.run_provider_session(tmp_path, task_file, "worker", "pipe-id", 30, state_check=mock.Mock())
        assert "provider session retry exhausted for pipe-id after 3 attempts" in str(exc_info.value)
        assert [c.args[3] for c in mock_pipe_run.call_args_list] == ["pipe-id", "pipe-id-retry-1", "pipe-id-retry-2"]

    # Recovery on attempt 2 in run_provider_session
    with mock.patch.object(pipeline, "runner_run", side_effect=[pipe_err, fake_out]) as mock_pipe_run:
        attempt_id, run_dir = pipeline.run_provider_session(
            tmp_path, task_file, "verifier", "pipe-id", 30, state_check=mock.Mock()
        )
        assert (attempt_id, run_dir) == ("pipe-id-retry-1", fake_out)
        assert [c.args[3] for c in mock_pipe_run.call_args_list] == ["pipe-id", "pipe-id-retry-1"]

    # Pipeline delegation stops after two output errors without retry-2
    pipe_out_err1 = runner.RetryableProviderOutputError("pipe-out-1")
    pipe_out_err2 = runner.RetryableProviderOutputError("pipe-out-2")
    with mock.patch.object(pipeline, "runner_run", side_effect=[pipe_out_err1, pipe_out_err2]) as mock_pipe_run:
        with pytest.raises(runner.ProposalError) as exc_info:
            pipeline.run_provider_session(tmp_path, task_file, "worker", "pipe-out", 30, state_check=mock.Mock())
        assert type(exc_info.value) is runner.ProposalError
        assert not isinstance(exc_info.value, runner.RetryableProviderSessionError)
        assert "repeated provider output failure for pipe-out after 2 output attempts" in str(exc_info.value)
        assert [c.args[3] for c in mock_pipe_run.call_args_list] == ["pipe-out", "pipe-out-retry-1"]
        assert mock_pipe_run.call_count == 2
