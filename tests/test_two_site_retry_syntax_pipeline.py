#!/usr/bin/env python3
"""Hermetic tests for Pipeline syntax retry handling in run_provider_session."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

try:
    from scripts import two_site_proposal_pipeline as pipeline
    from scripts.two_site_proposal_runner import (
        ForbiddenToolAttemptError,
        ProposalError,
        RetryableProviderOutputError,
        RetryableProviderSessionError,
    )
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipeline  # type: ignore[no-redef]
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        ForbiddenToolAttemptError,
        ProposalError,
        RetryableProviderOutputError,
        RetryableProviderSessionError,
    )


def _syntax_error(
    msg: str = "generated Python syntax is invalid: tests/test_a.py:10:5: invalid syntax",
) -> RetryableProviderOutputError:
    cause = ProposalError(msg)
    err = RetryableProviderOutputError("syntax invalid")
    err.__cause__ = cause
    return err


class TestTwoSiteRetrySyntaxPipeline(unittest.TestCase):
    """Focused hermetic unit tests for Pipeline.run_provider_session syntax retry."""

    def test_positive_worker_delivers_hint_on_next_attempt_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-w-pos"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-1"
            state_check = mock.Mock()
            err = _syntax_error()

            with (
                patch.object(pipeline, "runner_run", side_effect=[err, expected_dir]) as mock_run,
                patch.object(
                    pipeline, "load_task", return_value=({"allowed_files": ["tests/test_a.py"]}, None)
                ) as mock_load,
            ):
                run_id, run_dir = pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id=base_run_id,
                    timeout=30,
                    state_check=state_check,
                    expected_head="a" * 40,
                )

                self.assertEqual(run_id, f"{base_run_id}-retry-1")
                self.assertEqual(run_dir, expected_dir)
                mock_load.assert_called_once_with(root, task_path)
                self.assertEqual(mock_run.call_count, 2)
                self.assertEqual(state_check.call_count, 2)

                first_call = mock_run.call_args_list[0]
                self.assertEqual(first_call.args, (root, task_path, "worker", base_run_id, 30))
                self.assertNotIn("retry_hint", first_call.kwargs)
                self.assertEqual(
                    first_call.kwargs,
                    {"feedback": None, "proposal": None, "cli": None},
                )

                second_call = mock_run.call_args_list[1]
                self.assertEqual(second_call.args, (root, task_path, "worker", f"{base_run_id}-retry-1", 30))
                self.assertEqual(
                    second_call.kwargs,
                    {
                        "feedback": None,
                        "proposal": None,
                        "cli": None,
                        "retry_hint": {
                            "code": "PYTHON_SYNTAX_ERROR",
                            "path": "tests/test_a.py",
                            "line": 10,
                            "column": 5,
                        },
                    },
                )

    def test_negative_reviewer_ignores_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-v-neg"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-1"
            state_check = mock.Mock()
            err = _syntax_error()

            with (
                patch.object(pipeline, "runner_run", side_effect=[err, expected_dir]) as mock_run,
                patch.object(pipeline, "load_task") as mock_load,
            ):
                run_id, run_dir = pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="verifier",
                    base_run_id=base_run_id,
                    timeout=30,
                    state_check=state_check,
                    expected_head="a" * 40,
                )

                self.assertEqual(run_id, f"{base_run_id}-retry-1")
                self.assertEqual(run_dir, expected_dir)
                mock_load.assert_not_called()
                self.assertEqual(mock_run.call_count, 2)
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                self.assertNotIn("retry_hint", mock_run.call_args_list[1].kwargs)
                self.assertEqual(
                    mock_run.call_args_list[1].kwargs,
                    {"feedback": None, "proposal": None, "cli": None},
                )

    def test_negative_correction_worker_ignores_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            feedback_path = root / "feedback.json"
            proposal_path = root / "proposal.json"
            expected_dir = root / "evidence" / "test-corr-retry-1"
            state_check = mock.Mock()
            err = _syntax_error()

            # Correction worker with feedback
            with (
                patch.object(pipeline, "runner_run", side_effect=[err, expected_dir]) as mock_run,
                patch.object(pipeline, "load_task") as mock_load,
            ):
                pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id="test-corr",
                    timeout=30,
                    state_check=state_check,
                    feedback=feedback_path,
                    expected_head="a" * 40,
                )
                mock_load.assert_not_called()
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                self.assertNotIn("retry_hint", mock_run.call_args_list[1].kwargs)
                self.assertEqual(mock_run.call_args_list[1].kwargs["feedback"], feedback_path)

            # Correction worker with proposal
            with (
                patch.object(pipeline, "runner_run", side_effect=[err, expected_dir]) as mock_run,
                patch.object(pipeline, "load_task") as mock_load,
            ):
                pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id="test-corr-prop",
                    timeout=30,
                    state_check=state_check,
                    proposal=proposal_path,
                    expected_head="a" * 40,
                )
                mock_load.assert_not_called()
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                self.assertNotIn("retry_hint", mock_run.call_args_list[1].kwargs)
                self.assertEqual(mock_run.call_args_list[1].kwargs["proposal"], proposal_path)

    def test_negative_session_error_does_not_extract_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-w-sess"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-1"
            state_check = mock.Mock()
            err = RetryableProviderSessionError("session drop")
            err.__cause__ = ProposalError("generated Python syntax is invalid: tests/test_a.py:10:5: invalid syntax")

            with (
                patch.object(pipeline, "runner_run", side_effect=[err, expected_dir]) as mock_run,
                patch.object(pipeline, "load_task") as mock_load,
            ):
                pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id=base_run_id,
                    timeout=30,
                    state_check=state_check,
                    expected_head="a" * 40,
                )
                mock_load.assert_not_called()
                self.assertEqual(mock_run.call_count, 2)
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                self.assertNotIn("retry_hint", mock_run.call_args_list[1].kwargs)

    def test_negative_nonsyntax_output_error_does_not_extract_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-w-nonsyn"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-1"
            state_check = mock.Mock()
            err = RetryableProviderOutputError("malformed output")
            err.__cause__ = ProposalError("some other non-syntax proposal error")

            with (
                patch.object(pipeline, "runner_run", side_effect=[err, expected_dir]) as mock_run,
                patch.object(pipeline, "load_task") as mock_load,
            ):
                pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id=base_run_id,
                    timeout=30,
                    state_check=state_check,
                    expected_head="a" * 40,
                )
                mock_load.assert_not_called()
                self.assertEqual(mock_run.call_count, 2)
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                self.assertNotIn("retry_hint", mock_run.call_args_list[1].kwargs)

    def test_caps_preserved_no_extra_retry_after_two_output_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-w-two-out"
            state_check = mock.Mock()
            err1 = _syntax_error("generated Python syntax is invalid: tests/test_a.py:10:5: invalid syntax 1")
            err2 = _syntax_error("generated Python syntax is invalid: tests/test_a.py:20:8: invalid syntax 2")

            with (
                patch.object(pipeline, "runner_run", side_effect=[err1, err2]) as mock_run,
                patch.object(
                    pipeline, "load_task", return_value=({"allowed_files": ["tests/test_a.py"]}, None)
                ) as mock_load,
            ):
                with self.assertRaises(ProposalError) as ctx:
                    pipeline.run_provider_session(
                        root=root,
                        task_path=task_path,
                        role="worker",
                        base_run_id=base_run_id,
                        timeout=30,
                        state_check=state_check,
                        expected_head="a" * 40,
                    )

                exc = ctx.exception
                self.assertIs(type(exc), ProposalError)
                self.assertIs(exc.__cause__, err2)
                self.assertIn("repeated provider output failure for test-w-two-out after 2 output attempts", str(exc))
                self.assertEqual(mock_run.call_count, 2)
                self.assertEqual(mock_load.call_count, 1)
                self.assertEqual(
                    [c.args[3] for c in mock_run.call_args_list],
                    [base_run_id, f"{base_run_id}-retry-1"],
                )
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                self.assertIn("retry_hint", mock_run.call_args_list[1].kwargs)

    def test_stale_hint_cleared_after_session_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-stale-hint"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-2"
            state_check = mock.Mock()
            err_syn = _syntax_error()
            err_sess = RetryableProviderSessionError("transient session drop")

            with (
                patch.object(pipeline, "runner_run", side_effect=[err_syn, err_sess, expected_dir]) as mock_run,
                patch.object(
                    pipeline, "load_task", return_value=({"allowed_files": ["tests/test_a.py"]}, None)
                ) as mock_load,
            ):
                run_id, run_dir = pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id=base_run_id,
                    timeout=30,
                    state_check=state_check,
                    expected_head="a" * 40,
                )

                self.assertEqual(run_id, f"{base_run_id}-retry-2")
                self.assertEqual(run_dir, expected_dir)
                self.assertEqual(mock_run.call_count, 3)
                mock_load.assert_called_once_with(root, task_path)

                # Attempt 0: initial, no hint
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                # Attempt 1: retry-1 received hint from syntax error
                self.assertIn("retry_hint", mock_run.call_args_list[1].kwargs)
                # Attempt 2: retry-2 should have stale hint cleared after session drop
                self.assertNotIn("retry_hint", mock_run.call_args_list[2].kwargs)

    def test_tool_forbidden_fallback_removes_pending_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-fallback-hint"
            expected_fallback_dir = root / "evidence" / f"{base_run_id}-retry-1-gf"
            state_check = mock.Mock()
            err_syn = _syntax_error()
            err_tool = ForbiddenToolAttemptError("tool use forbidden")

            policy_mock = mock.Mock(enabled=True, model="claude-opus")
            route_mock = mock.Mock(model="claude-opus")

            with (
                patch.object(pipeline, "runner_run", side_effect=[err_syn, err_tool, expected_fallback_dir]) as mock_run,
                patch.object(
                    pipeline, "load_task", return_value=({"task_id": "test-task", "allowed_files": ["tests/test_a.py"]}, None)
                ) as mock_load,
                patch.object(pipeline, "load_worker_routing_policy", return_value=policy_mock),
                patch.object(pipeline, "select_worker_model_route", return_value=route_mock),
            ):
                run_id, run_dir = pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id=base_run_id,
                    timeout=30,
                    state_check=state_check,
                    expected_head="a" * 40,
                )

                self.assertEqual(run_id, f"{base_run_id}-retry-1-gf")
                self.assertEqual(run_dir, expected_fallback_dir)
                self.assertEqual(mock_run.call_count, 3)

                # Call 0: initial attempt
                self.assertNotIn("retry_hint", mock_run.call_args_list[0].kwargs)
                # Call 1: retry-1 attempt forward hint
                self.assertIn("retry_hint", mock_run.call_args_list[1].kwargs)
                # Call 2: fallback attempt must have retry_hint removed
                fallback_call = mock_run.call_args_list[2]
                self.assertNotIn("retry_hint", fallback_call.kwargs)
                self.assertEqual(fallback_call.kwargs["fallback_from_run_id"], f"{base_run_id}-retry-1")


if __name__ == "__main__":
    unittest.main()
