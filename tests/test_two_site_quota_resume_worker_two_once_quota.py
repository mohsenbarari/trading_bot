"""Tests for quota error pass-through, bound extraction, and fail-closed validation in worker-2 continuation."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError, QuotaBlockError
    from scripts.two_site_quota_resume_worker_two_once import execute_worker_two_once
    from tests.test_two_site_quota_resume_worker_two_once_smoke import _build_fixture_context
except ImportError:
    from two_site_proposal_pipeline import ProposalError, QuotaBlockError  # type: ignore[no-redef]
    from two_site_quota_resume_worker_two_once import execute_worker_two_once  # type: ignore[no-redef]
    from test_two_site_quota_resume_worker_two_once_smoke import _build_fixture_context  # type: ignore[no-redef]


@contextmanager
def _patch_executor(ctx: dict[str, Any]):
    @contextmanager
    def mock_context(r: Path, q: dict[str, Any]):
        yield ctx

    fake_history = {
        "complete": True,
        "conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
        "ordered_conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
    }

    patches = [
        patch(
            "scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context",
            side_effect=mock_context,
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.validate_quota_continuation_request",
            return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None),
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.read_terminal_quota_record",
            return_value=ctx["original_terminal_receipt"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.load_worker_correction_evidence",
            return_value=ctx["evidence"],
        ),
        patch("scripts.two_site_quota_resume_worker_two_once.check_state"),
        patch("scripts.two_site_quota_resume_worker_two_once.ref_snapshot", return_value={}),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.complete_worker_two_history",
            return_value=fake_history,
        ),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        yield


class TestTwoSiteQuotaResumeWorkerTwoOnceQuota(unittest.TestCase):
    def test_direct_quota_block_error_passed_through_once(self) -> None:
        """(1) runner raises QuotaBlockError with exact keys; executor passes same through, runner once."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}
            expected_run_id = "cont-req-200-worker-2"

            quota_attempt = {
                "role": "worker",
                "run_id": expected_run_id,
                "status_sha256": "a" * 64,
            }
            quota_evidence = {
                "kind": "QUOTA_RESET_UNKNOWN",
                "provider": "gemini",
                "model": "models/gemini-2.5-pro",
                "conversation_id": "conv-work-2",
                "reset_at_utc": None,
            }
            expected_err = QuotaBlockError(quota_evidence, quota_attempt)

            runner_calls = 0
            pre_check_called = False

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                nonlocal runner_calls, pre_check_called
                runner_calls += 1
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                pre_check_called = True
                raise expected_err

            with _patch_executor(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(QuotaBlockError) as exc_info:
                    execute_worker_two_once(root, req)

            self.assertTrue(pre_check_called)
            self.assertEqual(runner_calls, 1)
            self.assertIs(exc_info.exception, expected_err)
            self.assertEqual(exc_info.exception.quota_evidence, quota_evidence)
            self.assertEqual(exc_info.exception.quota_attempt, quota_attempt)
            self.assertEqual(
                set(exc_info.exception.quota_attempt.keys()),
                {"role", "run_id", "status_sha256"},
            )

    def test_bound_quota_evidence_extracted_and_raised_without_retry(self) -> None:
        """(2) runner raises ProposalError; _extract_bound_quota_evidence converts to bound QuotaBlockError, no retry."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}
            expected_run_id = "cont-req-200-worker-2"

            extracted_attempt = {
                "role": "worker",
                "run_id": expected_run_id,
                "status_sha256": "b" * 64,
            }
            extracted_evidence = {
                "kind": "QUOTA_RESET_UNKNOWN",
                "provider": "gemini",
                "model": "models/gemini-2.5-pro",
                "conversation_id": "conv-work-2",
                "reset_at_utc": None,
            }

            runner_calls = 0
            pre_check_called = False

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                nonlocal runner_calls, pre_check_called
                runner_calls += 1
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                pre_check_called = True
                raise ProposalError("runner failed with observational quota in proposal dir")

            with _patch_executor(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ), patch(
                "scripts.two_site_quota_resume_worker_two_once._extract_bound_quota_evidence",
                return_value=(extracted_evidence, extracted_attempt),
            ):
                with self.assertRaises(QuotaBlockError) as exc_info:
                    execute_worker_two_once(root, req)

            self.assertTrue(pre_check_called)
            self.assertEqual(runner_calls, 1)
            self.assertEqual(exc_info.exception.quota_evidence, extracted_evidence)
            self.assertEqual(exc_info.exception.quota_attempt, extracted_attempt)
            self.assertEqual(
                set(exc_info.exception.quota_attempt.keys()),
                {"role", "run_id", "status_sha256"},
            )

    def test_unbound_proposal_error_propagates_without_replay(self) -> None:
        """(3) runner raises ProposalError with no bound quota evidence; stays ProposalError and no replay."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}

            runner_calls = 0
            pre_check_called = False

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                nonlocal runner_calls, pre_check_called
                runner_calls += 1
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                pre_check_called = True
                raise ProposalError("unbound crash during proposal generation")

            with _patch_executor(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ), patch(
                "scripts.two_site_quota_resume_worker_two_once._extract_bound_quota_evidence",
                return_value=None,
            ):
                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_two_once(root, req)

            self.assertTrue(pre_check_called)
            self.assertEqual(runner_calls, 1)
            self.assertNotIsInstance(exc_info.exception, QuotaBlockError)
            self.assertIn("unbound crash during proposal generation", str(exc_info.exception))

    def test_duplicate_run_id_blocks_before_runner(self) -> None:
        """(4) existing run dir/ID blocks before runner invocation."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}

            expected_run_id = "cont-req-200-worker-2"
            dup_dir = root / ".local/two-site-refactor/proposals" / expected_run_id
            dup_dir.mkdir(parents=True, exist_ok=True)

            runner_calls = 0

            def mock_runner(*args: Any, **kwargs: Any) -> Path:
                nonlocal runner_calls
                runner_calls += 1
                return root / "not_reached"

            with _patch_executor(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_two_once(root, req)

            self.assertEqual(runner_calls, 0)
            self.assertIn("worker run ID already exists", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
