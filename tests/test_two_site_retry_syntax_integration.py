from __future__ import annotations

from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import two_site_proposal_runner as runner


def _syntax_error() -> runner.RetryableProviderOutputError:
    cause = runner.ProposalError(
        "generated Python syntax is invalid: tests/test_a.py:10:5: invalid syntax"
    )
    err = runner.RetryableProviderOutputError("syntax invalid")
    err.__cause__ = cause
    return err


def test_worker_syntax_output_retries_with_sanitized_hint(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    out_dir = tmp_path / "out"
    err = _syntax_error()

    with (
        mock.patch.object(runner, "run", side_effect=[err, out_dir]) as mock_run,
        mock.patch.object(
            runner, "load_task", return_value=({"allowed_files": ["tests/test_a.py"]}, None)
        ) as mock_load_task,
    ):
        res = runner.run_standalone(tmp_path, task_path, "worker", "w-syn", 30)

        assert res == out_dir
        mock_load_task.assert_called_once_with(tmp_path, task_path)
        assert mock_run.call_count == 2
        assert [c.args[3] for c in mock_run.call_args_list] == ["w-syn", "w-syn-retry-1"]

        first_call = mock_run.call_args_list[0]
        assert first_call.args == (tmp_path, task_path, "worker", "w-syn", 30)
        assert "retry_hint" not in first_call.kwargs
        assert first_call.kwargs == {
            "feedback": None,
            "proposal": None,
            "cli": None,
            "test_evidence": None,
        }

        second_call = mock_run.call_args_list[1]
        assert second_call.args == (tmp_path, task_path, "worker", "w-syn-retry-1", 30)
        assert second_call.kwargs["retry_hint"] == {
            "code": "PYTHON_SYNTAX_ERROR",
            "path": "tests/test_a.py",
            "line": 10,
            "column": 5,
        }
        assert second_call.kwargs["feedback"] is None
        assert second_call.kwargs["proposal"] is None
        assert second_call.kwargs["cli"] is None
        assert second_call.kwargs["test_evidence"] is None


def test_no_hint_or_task_load_on_reviewer_or_correction_worker(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    out_dir = tmp_path / "out"
    err = _syntax_error()

    # 1. Reviewer (verifier) ignores syntax error cause
    with (
        mock.patch.object(runner, "run", side_effect=[err, out_dir]) as mock_run,
        mock.patch.object(runner, "load_task") as mock_load_task,
    ):
        res = runner.run_standalone(tmp_path, task_path, "verifier", "v-syn", 30)
        assert res == out_dir
        mock_load_task.assert_not_called()
        assert [c.args[3] for c in mock_run.call_args_list] == ["v-syn", "v-syn-retry-1"]
        assert "retry_hint" not in mock_run.call_args_list[0].kwargs
        assert "retry_hint" not in mock_run.call_args_list[1].kwargs

    # 2. Correction worker with feedback ignores syntax error cause
    fb = tmp_path / "fb.json"
    with (
        mock.patch.object(runner, "run", side_effect=[err, out_dir]) as mock_run,
        mock.patch.object(runner, "load_task") as mock_load_task,
    ):
        res = runner.run_standalone(tmp_path, task_path, "worker", "w-fb", 30, feedback=fb)
        assert res == out_dir
        mock_load_task.assert_not_called()
        assert [c.args[3] for c in mock_run.call_args_list] == ["w-fb", "w-fb-retry-1"]
        assert "retry_hint" not in mock_run.call_args_list[0].kwargs
        assert "retry_hint" not in mock_run.call_args_list[1].kwargs
        assert mock_run.call_args_list[1].kwargs["feedback"] == fb

    # 3. Correction worker with proposal ignores syntax error cause
    prop = tmp_path / "prop.json"
    with (
        mock.patch.object(runner, "run", side_effect=[err, out_dir]) as mock_run,
        mock.patch.object(runner, "load_task") as mock_load_task,
    ):
        res = runner.run_standalone(tmp_path, task_path, "worker", "w-prop", 30, proposal=prop)
        assert res == out_dir
        mock_load_task.assert_not_called()
        assert [c.args[3] for c in mock_run.call_args_list] == ["w-prop", "w-prop-retry-1"]
        assert "retry_hint" not in mock_run.call_args_list[0].kwargs
        assert "retry_hint" not in mock_run.call_args_list[1].kwargs
        assert mock_run.call_args_list[1].kwargs["proposal"] == prop


def test_no_hint_or_task_load_on_nonsyntax_output_or_session_error(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    out_dir = tmp_path / "out"

    # 1. Non-syntax output error
    nonsyntax_err = runner.RetryableProviderOutputError("malformed output")
    nonsyntax_err.__cause__ = runner.ProposalError("unparseable response")
    with (
        mock.patch.object(runner, "run", side_effect=[nonsyntax_err, out_dir]) as mock_run,
        mock.patch.object(runner, "load_task") as mock_load_task,
    ):
        res = runner.run_standalone(tmp_path, task_path, "worker", "w-nonsyn", 30)
        assert res == out_dir
        mock_load_task.assert_not_called()
        assert [c.args[3] for c in mock_run.call_args_list] == ["w-nonsyn", "w-nonsyn-retry-1"]
        assert "retry_hint" not in mock_run.call_args_list[0].kwargs
        assert "retry_hint" not in mock_run.call_args_list[1].kwargs

    # 2. Session error
    sess_err = runner.RetryableProviderSessionError("session drop")
    with (
        mock.patch.object(runner, "run", side_effect=[sess_err, out_dir]) as mock_run,
        mock.patch.object(runner, "load_task") as mock_load_task,
    ):
        res = runner.run_standalone(tmp_path, task_path, "worker", "w-sess", 30)
        assert res == out_dir
        mock_load_task.assert_not_called()
        assert [c.args[3] for c in mock_run.call_args_list] == ["w-sess", "w-sess-retry-1"]
        assert "retry_hint" not in mock_run.call_args_list[0].kwargs
        assert "retry_hint" not in mock_run.call_args_list[1].kwargs


def test_caps_preserved_and_no_extra_retry_ids(tmp_path: Path) -> None:
    task_path = tmp_path / "task.json"
    err1 = _syntax_error()
    err2 = _syntax_error()

    # Repeated syntax output errors cap at 2 attempts without retry-2
    with (
        mock.patch.object(runner, "run", side_effect=[err1, err2]) as mock_run,
        mock.patch.object(
            runner, "load_task", return_value=({"allowed_files": ["tests/test_a.py"]}, None)
        ),
    ):
        with pytest.raises(runner.ProposalError) as exc_info:
            runner.run_standalone(tmp_path, task_path, "worker", "w-cap", 30)
        assert "repeated provider output failure for w-cap after 2 output attempts" in str(exc_info.value)
        assert exc_info.value.__cause__ is err2
        assert [c.args[3] for c in mock_run.call_args_list] == ["w-cap", "w-cap-retry-1"]
        assert mock_run.call_count == 2
