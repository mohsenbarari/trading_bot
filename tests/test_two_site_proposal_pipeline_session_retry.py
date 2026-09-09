#!/usr/bin/env python3
"""Regression tests for run_provider_session bounded retry and fail-closed contract."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import call, patch

try:
    from scripts import two_site_proposal_pipeline as pipeline
    from scripts.two_site_proposal_runner import (
        ProposalError,
        RetryableProviderSessionError,
    )
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipeline  # type: ignore[no-redef]
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        ProposalError,
        RetryableProviderSessionError,
    )


class TestTwoSiteProposalPipelineSessionRetry(unittest.TestCase):
    """Focused offline regression tests for run_provider_session."""

    def test_one_retryable_error_then_success_returns_retry_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            feedback_path = root / "feedback.json"
            proposal_path = root / "proposal.json"
            cli_path = root / "cli"
            base_run_id = "test-base"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-1"

            tracker = mock.Mock()
            state_check = mock.Mock()
            tracker.attach_mock(state_check, "state_check")

            with patch.object(pipeline, "runner_run") as mock_run:
                tracker.attach_mock(mock_run, "runner_run")
                mock_run.side_effect = [
                    RetryableProviderSessionError("transient attempt 0"),
                    expected_dir,
                ]

                run_id, run_dir = pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="worker",
                    base_run_id=base_run_id,
                    timeout=120,
                    state_check=state_check,
                    feedback=feedback_path,
                    proposal=proposal_path,
                    cli=cli_path,
                )

                self.assertEqual(run_id, f"{base_run_id}-retry-1")
                self.assertEqual(run_dir, expected_dir)
                self.assertEqual(mock_run.call_count, 2)
                self.assertEqual(state_check.call_count, 2)

                expected_calls = [
                    call(
                        root,
                        task_path,
                        "worker",
                        base_run_id,
                        120,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                    call(
                        root,
                        task_path,
                        "worker",
                        f"{base_run_id}-retry-1",
                        120,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                ]
                self.assertEqual(mock_run.call_args_list, expected_calls)

                expected_sequence = [
                    call.state_check(),
                    call.runner_run(
                        root,
                        task_path,
                        "worker",
                        base_run_id,
                        120,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                    call.state_check(),
                    call.runner_run(
                        root,
                        task_path,
                        "worker",
                        f"{base_run_id}-retry-1",
                        120,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                ]
                self.assertEqual(tracker.mock_calls, expected_sequence)

    def test_two_retryable_errors_then_success_returns_retry_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            feedback_path = root / "feedback.json"
            proposal_path = root / "proposal.json"
            cli_path = root / "cli"
            base_run_id = "test-base"
            expected_dir = root / "evidence" / f"{base_run_id}-retry-2"

            tracker = mock.Mock()
            state_check = mock.Mock()
            tracker.attach_mock(state_check, "state_check")

            with patch.object(pipeline, "runner_run") as mock_run:
                tracker.attach_mock(mock_run, "runner_run")
                mock_run.side_effect = [
                    RetryableProviderSessionError("transient attempt 0"),
                    RetryableProviderSessionError("transient attempt 1"),
                    expected_dir,
                ]

                run_id, run_dir = pipeline.run_provider_session(
                    root=root,
                    task_path=task_path,
                    role="verifier",
                    base_run_id=base_run_id,
                    timeout=60,
                    state_check=state_check,
                    feedback=feedback_path,
                    proposal=proposal_path,
                    cli=cli_path,
                )

                self.assertEqual(run_id, f"{base_run_id}-retry-2")
                self.assertEqual(run_dir, expected_dir)
                self.assertEqual(mock_run.call_count, 3)
                self.assertEqual(state_check.call_count, 3)

                expected_calls = [
                    call(
                        root,
                        task_path,
                        "verifier",
                        base_run_id,
                        60,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                    call(
                        root,
                        task_path,
                        "verifier",
                        f"{base_run_id}-retry-1",
                        60,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                    call(
                        root,
                        task_path,
                        "verifier",
                        f"{base_run_id}-retry-2",
                        60,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                ]
                self.assertEqual(mock_run.call_args_list, expected_calls)

                expected_sequence = [
                    call.state_check(),
                    call.runner_run(
                        root,
                        task_path,
                        "verifier",
                        base_run_id,
                        60,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                    call.state_check(),
                    call.runner_run(
                        root,
                        task_path,
                        "verifier",
                        f"{base_run_id}-retry-1",
                        60,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                    call.state_check(),
                    call.runner_run(
                        root,
                        task_path,
                        "verifier",
                        f"{base_run_id}-retry-2",
                        60,
                        feedback=feedback_path,
                        proposal=proposal_path,
                        cli=cli_path,
                    ),
                ]
                self.assertEqual(tracker.mock_calls, expected_sequence)

    def test_three_retryable_errors_exhaust_retries_and_raise_proposal_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-exhaust"
            state_check = mock.Mock()

            err1 = RetryableProviderSessionError("transient error 1")
            err2 = RetryableProviderSessionError("transient error 2")
            err3 = RetryableProviderSessionError("transient error 3")

            with patch.object(pipeline, "runner_run") as mock_run:
                mock_run.side_effect = [err1, err2, err3]

                with self.assertRaises(ProposalError) as ctx:
                    pipeline.run_provider_session(
                        root=root,
                        task_path=task_path,
                        role="worker",
                        base_run_id=base_run_id,
                        timeout=30,
                        state_check=state_check,
                    )

                exc = ctx.exception
                self.assertEqual(mock_run.call_count, 3)
                self.assertEqual(state_check.call_count, 3)
                self.assertEqual(
                    [c.args[3] for c in mock_run.call_args_list],
                    [base_run_id, f"{base_run_id}-retry-1", f"{base_run_id}-retry-2"],
                )
                self.assertIs(type(exc), ProposalError)
                self.assertIsInstance(exc, ProposalError)
                self.assertNotIsInstance(exc, RetryableProviderSessionError)
                self.assertIn(base_run_id, str(exc))
                self.assertIn("after 3 attempts", str(exc))
                self.assertEqual(
                    str(exc),
                    f"provider session retry exhausted for {base_run_id} after 3 attempts",
                )
                self.assertIs(exc.__cause__, err3)

    def test_ordinary_proposal_error_propagates_immediately_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-proposal-error"
            state_check = mock.Mock()
            expected_err = ProposalError("fatal deterministic proposal error")

            with patch.object(pipeline, "runner_run") as mock_run:
                mock_run.side_effect = expected_err

                with self.assertRaises(ProposalError) as ctx:
                    pipeline.run_provider_session(
                        root=root,
                        task_path=task_path,
                        role="worker",
                        base_run_id=base_run_id,
                        timeout=30,
                        state_check=state_check,
                    )

                self.assertIs(ctx.exception, expected_err)
                self.assertEqual(mock_run.call_count, 1)
                self.assertEqual(state_check.call_count, 1)
                mock_run.assert_called_once_with(
                    root, task_path, "worker", base_run_id, 30,
                    feedback=None, proposal=None, cli=None,
                )

    def test_arbitrary_non_proposal_exception_propagates_immediately_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "test-non-proposal-error"
            state_check = mock.Mock()
            expected_err = RuntimeError("unexpected runtime failure")

            with patch.object(pipeline, "runner_run") as mock_run:
                mock_run.side_effect = expected_err

                with self.assertRaises(RuntimeError) as ctx:
                    pipeline.run_provider_session(
                        root=root,
                        task_path=task_path,
                        role="verifier",
                        base_run_id=base_run_id,
                        timeout=45,
                        state_check=state_check,
                    )

                self.assertIs(ctx.exception, expected_err)
                self.assertEqual(mock_run.call_count, 1)
                self.assertEqual(state_check.call_count, 1)
                mock_run.assert_called_once_with(
                    root, task_path, "verifier", base_run_id, 45,
                    feedback=None, proposal=None, cli=None,
                )

    def test_preexisting_base_evidence_dir_fails_before_runner_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "worker-run-base"
            proposals_dir = root / ".local/two-site-refactor/proposals"
            base_dir = proposals_dir / base_run_id
            base_dir.mkdir(parents=True, exist_ok=True)
            sentinel = base_dir / "sentinel.txt"
            sentinel.write_text("worker baseline payload", encoding="utf-8")

            state_check = mock.Mock()
            with patch.object(pipeline, "runner_run") as mock_run:
                with self.assertRaises(ProposalError) as ctx:
                    pipeline.run_provider_session(
                        root=root,
                        task_path=task_path,
                        role="worker",
                        base_run_id=base_run_id,
                        timeout=30,
                        state_check=state_check,
                    )

                exc = ctx.exception
                self.assertEqual(str(exc), f"worker run ID already exists: {base_run_id}")
                self.assertEqual(state_check.call_count, 1)
                mock_run.assert_not_called()

                self.assertTrue(base_dir.exists())
                self.assertTrue(base_dir.is_dir())
                self.assertTrue(sentinel.exists())
                self.assertEqual(
                    sentinel.read_text(encoding="utf-8"),
                    "worker baseline payload",
                )

    def test_preexisting_retry_1_evidence_dir_prevents_second_runner_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            task_path = root / "task.json"
            base_run_id = "verifier-run-base"
            proposals_dir = root / ".local/two-site-refactor/proposals"
            retry_1_id = f"{base_run_id}-retry-1"
            retry_1_dir = proposals_dir / retry_1_id
            retry_1_dir.mkdir(parents=True, exist_ok=True)
            sentinel = retry_1_dir / "sentinel.txt"
            sentinel.write_text("reviewer baseline payload", encoding="utf-8")

            state_check = mock.Mock()
            transient_err = RetryableProviderSessionError("transient provider failure")

            with patch.object(pipeline, "runner_run") as mock_run:
                mock_run.side_effect = [transient_err]

                with self.assertRaises(ProposalError) as ctx:
                    pipeline.run_provider_session(
                        root=root,
                        task_path=task_path,
                        role="verifier",
                        base_run_id=base_run_id,
                        timeout=45,
                        state_check=state_check,
                    )

                exc = ctx.exception
                self.assertEqual(str(exc), f"reviewer run ID already exists: {retry_1_id}")
                self.assertEqual(state_check.call_count, 2)
                self.assertEqual(mock_run.call_count, 1)
                mock_run.assert_called_once_with(
                    root,
                    task_path,
                    "verifier",
                    base_run_id,
                    45,
                    feedback=None,
                    proposal=None,
                    cli=None,
                )

                self.assertTrue(retry_1_dir.exists())
                self.assertTrue(retry_1_dir.is_dir())
                self.assertTrue(sentinel.exists())
                self.assertEqual(
                    sentinel.read_text(encoding="utf-8"),
                    "reviewer baseline payload",
                )


if __name__ == "__main__":
    unittest.main()
