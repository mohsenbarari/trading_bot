from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_quota_resume_reviewer_call import validate_bound_prior_reviewer
except ImportError:
    from two_site_proposal_pipeline import ProposalError
    from two_site_quota_resume_reviewer_call import validate_bound_prior_reviewer


def _make_artifacts(index: int) -> dict:
    prefix = "run-20260925-120000"
    prior_idx = index - 1
    worker_id = f"{prefix}-worker-{prior_idx}"
    reviewer_id = f"{prefix}-reviewer-{prior_idx}"
    return {
        "task_id": "task-test-259",
        "semantic_task_fingerprint": "1" * 64,
        "source_head": "0123456789abcdef0123456789abcdef01234567",
        "original_run_prefix": prefix,
        "attempted_run_id": f"{prefix}-worker-{index}",
        "status_sha256": "2" * 64,
        "status_sha": "2" * 64,
        "quota_evidence": {
            "kind": "quota",
            "provider": "gemini",
            "model": "gemini",
            "conversation_id": "conv-worker",
            "reset_at_utc": "2026-09-25T18:00:00Z",
        },
        "worker_index": index,
        "final_worker_run_id": worker_id,
        "final_reviewer_run_id": reviewer_id,
        "proposal_path": f".local/two-site-refactor/proposals/{worker_id}/result.json",
        "proposal_sha256": "3" * 64,
        "reviewer_path": f".local/two-site-refactor/proposals/{reviewer_id}/result.json",
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{prefix}/pre-review-tests-{prior_idx}.json",
        "pre_review_test_sha256": "4" * 64,
        "pre_review_test_state": "PASSED",
        "changed_paths": ["scripts/worker.py"],
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{prefix}/feedback-correction-{index}.json",
    }


def _make_worker_info(sha: str = "3" * 64) -> dict:
    return {
        "status": {
            "run_id": "run-20260925-120000-worker-0",
            "role": "worker",
            "state": "PROPOSAL_READY",
            "conversation_id": "conv-worker-123",
        },
        "conversation_id": "conv-worker-123",
        "result_sha": sha,
        "result_text": '{"status": "PROPOSAL_READY"}',
    }


class TestTwoSiteQuotaResumeReviewerCall(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {"task_id": "task-test-259"}
        self.sources = {"file.py": "abc"}
        self.test_receipt = {"kind": "receipt"}

    @patch("scripts.two_site_quota_resume_reviewer_call.validate_run")
    def test_worker_1_positive_exact_call(self, mock_validate_run: MagicMock) -> None:
        artifacts = _make_artifacts(1)
        worker_info = _make_worker_info()
        mock_result = {"status": "CHANGES_REQUIRED", "findings": ["needs fix"]}
        mock_status = {"state": "CHANGES_REQUIRED"}
        mock_validate_run.return_value = (mock_result, mock_status, "9" * 64, "{}")

        res, st, sha = validate_bound_prior_reviewer(
            root="/test-root",
            artifacts=artifacts,
            worker_info=worker_info,
            test_receipt=self.test_receipt,
            task=self.task,
            sources=self.sources,
        )

        self.assertEqual(res, mock_result)
        self.assertIsNot(res, mock_result)
        self.assertEqual(st, mock_status)
        self.assertIsNot(st, mock_status)
        self.assertEqual(sha, "9" * 64)

        mock_validate_run.assert_called_once_with(
            root=Path("/test-root"),
            run_dir=Path("/test-root/.local/two-site-refactor/proposals/run-20260925-120000-reviewer-0"),
            role="verifier",
            expected_run_id="run-20260925-120000-reviewer-0",
            expected_head="0123456789abcdef0123456789abcdef01234567",
            expected_task=self.task,
            expected_sources=self.sources,
            seen_convs={"conv-worker-123"},
            prior_worker=(
                "run-20260925-120000-worker-0",
                "3" * 64,
                '{"status": "PROPOSAL_READY"}',
                "conv-worker-123",
            ),
            expected_test_receipt=self.test_receipt,
            expected_test_evidence_sha="4" * 64,
        )

    @patch("scripts.two_site_quota_resume_reviewer_call.validate_run")
    def test_worker_2_positive_exact_call(self, mock_validate_run: MagicMock) -> None:
        artifacts = _make_artifacts(2)
        worker_info = _make_worker_info()
        mock_result = {"status": "CHANGES_REQUIRED", "findings": ["still invalid"]}
        mock_status = {"state": "CHANGES_REQUIRED"}
        mock_validate_run.return_value = (mock_result, mock_status, "8" * 64, "{}")

        res, st, sha = validate_bound_prior_reviewer(
            root="/test-root",
            artifacts=artifacts,
            worker_info=worker_info,
            test_receipt=self.test_receipt,
            task=self.task,
            sources=self.sources,
        )
        self.assertEqual(sha, "8" * 64)
        mock_validate_run.assert_called_once()

    @patch("scripts.two_site_quota_resume_reviewer_call.validate_run")
    def test_alias_only_dict_rejected_before_validate_run(self, mock_val: MagicMock) -> None:
        alias_only = {
            "worker_index": 1,
            "original_prefix": "run-20260925-120000",
            "prior_worker_id": "run-20260925-120000-worker-0",
            "prior_reviewer_id": "run-20260925-120000-reviewer-0",
            "proposal_sha": "3" * 64,
            "pre_review_sha": "4" * 64,
            "source_head": "0123456789abcdef0123456789abcdef01234567",
        }
        with self.assertRaises(ProposalError):
            validate_bound_prior_reviewer(
                root="/test-root",
                artifacts=alias_only,
                worker_info=_make_worker_info(),
                test_receipt=self.test_receipt,
                task=self.task,
                sources=self.sources,
            )
        mock_val.assert_not_called()

    @patch("scripts.two_site_quota_resume_reviewer_call.validate_run")
    def test_floats_bool_bad_head_rejected_before_validate_run(self, mock_val: MagicMock) -> None:
        base = _make_artifacts(1)
        worker_info = _make_worker_info()

        for bad_index in (1.0, 2.0, True, False, 0, 3):
            art = dict(base, worker_index=bad_index)
            with self.assertRaises(ProposalError):
                validate_bound_prior_reviewer(
                    root="/test-root",
                    artifacts=art,
                    worker_info=worker_info,
                    test_receipt=self.test_receipt,
                    task=self.task,
                    sources=self.sources,
                )
            mock_val.assert_not_called()

        for bad_head in ("short", "0123456789ABCDEF0123456789ABCDEF01234567", "g" * 40, "1" * 39):
            art = dict(base, source_head=bad_head)
            with self.assertRaises(ProposalError):
                validate_bound_prior_reviewer(
                    root="/test-root",
                    artifacts=art,
                    worker_info=worker_info,
                    test_receipt=self.test_receipt,
                    task=self.task,
                    sources=self.sources,
                )
            mock_val.assert_not_called()

    @patch("scripts.two_site_quota_resume_reviewer_call.validate_run")
    def test_worker_info_sha_mismatch_and_path_mismatch(self, mock_val: MagicMock) -> None:
        artifacts = _make_artifacts(1)
        worker_info = _make_worker_info(sha="f" * 64)
        with self.assertRaises(ProposalError):
            validate_bound_prior_reviewer(
                root="/test-root",
                artifacts=artifacts,
                worker_info=worker_info,
                test_receipt=self.test_receipt,
                task=self.task,
                sources=self.sources,
            )
        mock_val.assert_not_called()

        bad_path_art = dict(artifacts, proposal_path=".local/other/path")
        with self.assertRaises(ProposalError):
            validate_bound_prior_reviewer(
                root="/test-root",
                artifacts=bad_path_art,
                worker_info=_make_worker_info(),
                test_receipt=self.test_receipt,
                task=self.task,
                sources=self.sources,
            )
        mock_val.assert_not_called()

    @patch("scripts.two_site_quota_resume_reviewer_call.validate_run")
    def test_reviewer_status_and_findings_requirements(self, mock_val: MagicMock) -> None:
        artifacts = _make_artifacts(1)
        worker_info = _make_worker_info()

        mock_val.return_value = (
            {"status": "VERIFIED", "findings": []},
            {"state": "VERIFIED"},
            "9" * 64,
            "{}",
        )
        with self.assertRaises(ProposalError):
            validate_bound_prior_reviewer(
                root="/test-root",
                artifacts=artifacts,
                worker_info=worker_info,
                test_receipt=self.test_receipt,
                task=self.task,
                sources=self.sources,
            )

        mock_val.return_value = (
            {"status": "CHANGES_REQUIRED", "findings": []},
            {"state": "CHANGES_REQUIRED"},
            "9" * 64,
            "{}",
        )
        with self.assertRaises(ProposalError):
            validate_bound_prior_reviewer(
                root="/test-root",
                artifacts=artifacts,
                worker_info=worker_info,
                test_receipt=self.test_receipt,
                task=self.task,
                sources=self.sources,
            )


if __name__ == "__main__":
    unittest.main()
