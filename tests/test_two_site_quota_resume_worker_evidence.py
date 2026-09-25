"""Hermetic unit tests for load_worker_correction_evidence."""

from __future__ import annotations

import copy
from pathlib import Path
import unittest
from unittest import mock

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_quota_resume_worker_evidence import (
        load_worker_correction_evidence,
    )
    TARGET_MODULE = "scripts.two_site_quota_resume_worker_evidence"
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_quota_resume_worker_evidence import (  # type: ignore[no-redef]
        load_worker_correction_evidence,
    )
    TARGET_MODULE = "two_site_quota_resume_worker_evidence"


class TestTwoSiteQuotaResumeWorkerEvidence(unittest.TestCase):
    """Validate sequence, short-circuit, and detached output of load_worker_correction_evidence."""

    def setUp(self) -> None:
        self.root = Path("/test/repo/root")
        self.admitted_task_path = "tasks/admitted_task_sample.json"
        self.sample_artifacts = {
            "worker_index": 1,
            "original_run_prefix": "run_20260925",
            "final_worker_run_id": "worker_run_1",
            "final_reviewer_run_id": "reviewer_run_1",
            "proposal_path": "runs/proposal.json",
            "reviewer_path": "runs/reviewer.json",
            "pre_review_test_path": "runs/pre_review.json",
            "expected_feedback_path": "runs/correction_feedback.json",
            "proposal_sha256": "1" * 64,
            "pre_review_test_sha256": "2" * 64,
            "pre_review_test_state": "PASSED",
            "source_head": "3" * 40,
        }
        self.sample_worker_info = {
            "conversation_id": "conv-123",
            "status": {"state": "PROPOSAL_READY", "conversation_id": "conv-123"},
            "result_text": "proposal output",
            "result_sha": "1" * 64,
        }
        self.sample_receipt = {
            "state": "PASSED",
            "proposal_sha256": "1" * 64,
            "selectors": ["tests/test_sample.py"],
        }
        self.sample_task = {"task": {"task_id": "T-001", "goal": "do work"}}
        self.sample_sources = {"scripts/worker.py": {"sha256": "a" * 64, "content": "# code"}}
        self.sample_reviewer_result = {
            "status": "CHANGES_REQUIRED",
            "findings": ["finding 1", "finding 2"],
        }
        self.sample_reviewer_status = {"state": "CHANGES_REQUIRED"}
        self.sample_reviewer_sha = "4" * 64
        self.sample_feedback = {
            "reviewer": "gemini",
            "findings": ["finding 1", "finding 2"],
            "test_evidence": ["receipt_sha256=..."],
        }
        self.sample_feedback_sha = "5" * 64
        self.sample_feedback_path = "runs/correction_feedback.json"

    def test_load_worker_correction_evidence_success_worker_1_and_2(self) -> None:
        for worker_idx in (1, 2):
            artifacts = copy.deepcopy(self.sample_artifacts)
            artifacts["worker_index"] = worker_idx

            with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
                 mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
                 mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
                 mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:

                mock_prereq.return_value = {
                    "worker_info": self.sample_worker_info,
                    "receipt": self.sample_receipt,
                    "prior_index": worker_idx - 1,
                }
                mock_task.return_value = (self.sample_task, self.sample_sources)
                mock_reviewer.return_value = (
                    self.sample_reviewer_result,
                    self.sample_reviewer_status,
                    self.sample_reviewer_sha,
                )
                mock_feedback.return_value = (
                    self.sample_feedback,
                    self.sample_feedback_sha,
                    self.sample_feedback_path,
                )

                manager = mock.MagicMock()
                manager.attach_mock(mock_prereq, "load_prior_worker_and_receipt")
                manager.attach_mock(mock_task, "load_task")
                manager.attach_mock(mock_reviewer, "validate_bound_prior_reviewer")
                manager.attach_mock(mock_feedback, "load_bound_correction_feedback")

                result = load_worker_correction_evidence(self.root, self.admitted_task_path, artifacts)

                expected_calls = [
                    mock.call.load_prior_worker_and_receipt(self.root, self.admitted_task_path, artifacts),
                    mock.call.load_task(self.root, self.admitted_task_path),
                    mock.call.validate_bound_prior_reviewer(
                        self.root,
                        artifacts,
                        self.sample_worker_info,
                        self.sample_receipt,
                        self.sample_task,
                        self.sample_sources,
                    ),
                    mock.call.load_bound_correction_feedback(
                        self.root,
                        artifacts,
                        self.sample_reviewer_result,
                        self.sample_receipt,
                    ),
                ]
                self.assertEqual(manager.mock_calls, expected_calls)

                expected_keys = {
                    "artifacts",
                    "feedback",
                    "feedback_path",
                    "feedback_sha",
                    "receipt",
                    "reviewer_result",
                    "reviewer_sha",
                    "reviewer_status",
                    "sources",
                    "task",
                    "worker_info",
                }
                self.assertEqual(set(result.keys()), expected_keys)

                # Detached copy assertion: mutating result must not mutate inputs or original payloads
                result["task"]["task"]["goal"] = "MUTATED"
                self.assertEqual(self.sample_task["task"]["goal"], "do work")
                result["artifacts"]["worker_index"] = 99
                self.assertEqual(artifacts["worker_index"], worker_idx)
                artifacts["original_run_prefix"] = "MUTATED"
                self.assertEqual(result["artifacts"]["original_run_prefix"], "run_20260925")

    def test_short_circuit_and_fail_closed_on_helper_failures(self) -> None:
        # Failure in helper 1 -> helpers 2, 3, 4 never called
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:
            mock_prereq.side_effect = ProposalError("helper 1 failed")
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, self.sample_artifacts)
            self.assertIn("helper 1 failed", str(ctx.exception))
            mock_task.assert_not_called()
            mock_reviewer.assert_not_called()
            mock_feedback.assert_not_called()

        # Failure in helper 2 -> helpers 3, 4 never called
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": self.sample_receipt,
            }
            mock_task.side_effect = ProposalError("helper 2 failed")
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, self.sample_artifacts)
            self.assertIn("helper 2 failed", str(ctx.exception))
            mock_reviewer.assert_not_called()
            mock_feedback.assert_not_called()

        # Failure in helper 3 -> helper 4 never called
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": self.sample_receipt,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            mock_reviewer.side_effect = ProposalError("helper 3 failed")
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, self.sample_artifacts)
            self.assertIn("helper 3 failed", str(ctx.exception))
            mock_feedback.assert_not_called()

        # Failure in helper 4 -> exception propagates
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": self.sample_receipt,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            mock_reviewer.return_value = (
                self.sample_reviewer_result,
                self.sample_reviewer_status,
                self.sample_reviewer_sha,
            )
            mock_feedback.side_effect = ProposalError("helper 4 failed")
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, self.sample_artifacts)
            self.assertIn("helper 4 failed", str(ctx.exception))

    def test_mismatched_feedback_path_raises_proposal_error(self) -> None:
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": self.sample_receipt,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            mock_reviewer.return_value = (
                self.sample_reviewer_result,
                self.sample_reviewer_status,
                self.sample_reviewer_sha,
            )
            mock_feedback.return_value = (
                self.sample_feedback,
                self.sample_feedback_sha,
                "runs/mismatched_path.json",
            )
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, self.sample_artifacts)
            self.assertIn("feedback path mismatch", str(ctx.exception))

        # Missing expected_feedback_path in artifacts
        artifacts_without_feedback_path = dict(self.sample_artifacts)
        del artifacts_without_feedback_path["expected_feedback_path"]
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": self.sample_receipt,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            mock_reviewer.return_value = (
                self.sample_reviewer_result,
                self.sample_reviewer_status,
                self.sample_reviewer_sha,
            )
            mock_feedback.return_value = (
                self.sample_feedback,
                self.sample_feedback_sha,
                self.sample_feedback_path,
            )
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, artifacts_without_feedback_path)
            self.assertIn("expected_feedback_path", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
