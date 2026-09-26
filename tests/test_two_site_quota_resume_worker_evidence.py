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
            for explicit_origin in (False, True):
                artifacts = copy.deepcopy(self.sample_artifacts)
                artifacts["worker_index"] = worker_idx
                if explicit_origin:
                    artifacts["correction_origin"] = "REVIEWER"

                with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
                     mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
                     mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
                     mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback, \
                     mock.patch(f"{TARGET_MODULE}.load_bound_test_gate_feedback") as mock_test_gate:

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
                    mock_test_gate.assert_not_called()

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
                    self.assertEqual(result["reviewer_result"], self.sample_reviewer_result)
                    self.assertEqual(result["reviewer_status"], self.sample_reviewer_status)
                    self.assertEqual(result["reviewer_sha"], self.sample_reviewer_sha)
                    self.assertEqual(result["receipt"], self.sample_receipt)

                    # Detached copy assertion: mutating result must not mutate inputs or original payloads
                    result["task"]["task"]["goal"] = "MUTATED"
                    self.assertEqual(self.sample_task["task"]["goal"], "do work")
                    result["artifacts"]["worker_index"] = 99
                    self.assertEqual(artifacts["worker_index"], worker_idx)
                    artifacts["original_run_prefix"] = "MUTATED"
                    self.assertEqual(result["artifacts"]["original_run_prefix"], "run_20260925")

    def test_load_worker_correction_evidence_bootstrap_worker_1_and_2(self) -> None:
        test_gate_feedback = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": "1" * 64,
            "findings": ["bootstrap error"],
            "test_evidence": ["gate=P1-00-characterization-bootstrap"],
        }
        for worker_idx in (1, 2):
            artifacts = {
                "correction_origin": "BOOTSTRAP",
                "worker_index": worker_idx,
                "original_run_prefix": "run_20260925",
                "final_worker_run_id": "worker_run_1",
                "final_reviewer_run_id": None,
                "proposal_path": "runs/proposal.json",
                "reviewer_path": None,
                "pre_review_test_path": None,
                "expected_feedback_path": "runs/test_gate_feedback.json",
                "proposal_sha256": "1" * 64,
                "pre_review_test_sha256": None,
                "pre_review_test_state": None,
            }
            with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
                 mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
                 mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
                 mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback, \
                 mock.patch(f"{TARGET_MODULE}.load_bound_test_gate_feedback") as mock_test_gate:

                mock_prereq.return_value = {
                    "worker_info": self.sample_worker_info,
                    "receipt": None,
                    "prior_index": worker_idx - 1,
                }
                mock_task.return_value = (self.sample_task, self.sample_sources)
                mock_test_gate.return_value = (
                    test_gate_feedback,
                    "6" * 64,
                    "runs/test_gate_feedback.json",
                )

                manager = mock.MagicMock()
                manager.attach_mock(mock_prereq, "load_prior_worker_and_receipt")
                manager.attach_mock(mock_task, "load_task")
                manager.attach_mock(mock_test_gate, "load_bound_test_gate_feedback")

                result = load_worker_correction_evidence(self.root, self.admitted_task_path, artifacts)

                expected_calls = [
                    mock.call.load_prior_worker_and_receipt(self.root, self.admitted_task_path, artifacts),
                    mock.call.load_task(self.root, self.admitted_task_path),
                    mock.call.load_bound_test_gate_feedback(
                        self.root,
                        artifacts,
                        self.sample_worker_info,
                        None,
                    ),
                ]
                self.assertEqual(manager.mock_calls, expected_calls)
                mock_reviewer.assert_not_called()
                mock_feedback.assert_not_called()

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
                self.assertIsNone(result["reviewer_result"])
                self.assertIsNone(result["reviewer_status"])
                self.assertIsNone(result["reviewer_sha"])
                self.assertIsNone(result["receipt"])
                self.assertEqual(result["feedback"], test_gate_feedback)
                self.assertEqual(result["feedback_path"], "runs/test_gate_feedback.json")
                self.assertEqual(result["feedback_sha"], "6" * 64)

                # Detached copy assertion
                result["task"]["task"]["goal"] = "MUTATED"
                self.assertEqual(self.sample_task["task"]["goal"], "do work")
                result["artifacts"]["worker_index"] = 99
                self.assertEqual(artifacts["worker_index"], worker_idx)

    def test_load_worker_correction_evidence_test_failed_worker_1_and_2(self) -> None:
        test_gate_feedback = {
            "reviewer": "test-gate",
            "reviewed_proposal_sha256": "1" * 64,
            "findings": ["unit test failed"],
            "test_evidence": ["failed tests: test_foo"],
        }
        failed_receipt = {
            "state": "FAILED",
            "proposal_sha256": "1" * 64,
            "selectors": ["tests/test_sample.py"],
        }
        for worker_idx in (1, 2):
            artifacts = {
                "correction_origin": "TEST_FAILED",
                "worker_index": worker_idx,
                "original_run_prefix": "run_20260925",
                "final_worker_run_id": "worker_run_1",
                "final_reviewer_run_id": None,
                "proposal_path": "runs/proposal.json",
                "reviewer_path": None,
                "pre_review_test_path": "runs/pre_review.json",
                "expected_feedback_path": "runs/test_gate_feedback.json",
                "proposal_sha256": "1" * 64,
                "pre_review_test_sha256": "2" * 64,
                "pre_review_test_state": "FAILED",
            }
            with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
                 mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
                 mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
                 mock.patch(f"{TARGET_MODULE}.load_bound_correction_feedback") as mock_feedback, \
                 mock.patch(f"{TARGET_MODULE}.load_bound_test_gate_feedback") as mock_test_gate:

                mock_prereq.return_value = {
                    "worker_info": self.sample_worker_info,
                    "receipt": failed_receipt,
                    "prior_index": worker_idx - 1,
                }
                mock_task.return_value = (self.sample_task, self.sample_sources)
                mock_test_gate.return_value = (
                    test_gate_feedback,
                    "7" * 64,
                    "runs/test_gate_feedback.json",
                )

                manager = mock.MagicMock()
                manager.attach_mock(mock_prereq, "load_prior_worker_and_receipt")
                manager.attach_mock(mock_task, "load_task")
                manager.attach_mock(mock_test_gate, "load_bound_test_gate_feedback")

                result = load_worker_correction_evidence(self.root, self.admitted_task_path, artifacts)

                expected_calls = [
                    mock.call.load_prior_worker_and_receipt(self.root, self.admitted_task_path, artifacts),
                    mock.call.load_task(self.root, self.admitted_task_path),
                    mock.call.load_bound_test_gate_feedback(
                        self.root,
                        artifacts,
                        self.sample_worker_info,
                        failed_receipt,
                    ),
                ]
                self.assertEqual(manager.mock_calls, expected_calls)
                mock_reviewer.assert_not_called()
                mock_feedback.assert_not_called()

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
                self.assertIsNone(result["reviewer_result"])
                self.assertIsNone(result["reviewer_status"])
                self.assertIsNone(result["reviewer_sha"])
                self.assertEqual(result["receipt"], failed_receipt)
                self.assertEqual(result["feedback"], test_gate_feedback)

                # Detached copy assertion
                result["receipt"]["state"] = "MUTATED"
                self.assertEqual(failed_receipt["state"], "FAILED")

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

        # Failure in helper 3 -> helper 4 never called (REVIEWER path)
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

        # Failure in helper 4 -> exception propagates (REVIEWER path)
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

        # Failure in test-gate helper 3 -> propagates
        test_gate_artifacts = dict(self.sample_artifacts)
        test_gate_artifacts["correction_origin"] = "BOOTSTRAP"
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.validate_bound_prior_reviewer") as mock_reviewer, \
             mock.patch(f"{TARGET_MODULE}.load_bound_test_gate_feedback") as mock_test_gate:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": None,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            mock_test_gate.side_effect = ProposalError("test-gate helper failed")
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, test_gate_artifacts)
            self.assertIn("test-gate helper failed", str(ctx.exception))
            mock_reviewer.assert_not_called()

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

    def test_unknown_origin_and_path_validation(self) -> None:
        # Unknown origin rejection
        unknown_artifacts = dict(self.sample_artifacts)
        unknown_artifacts["correction_origin"] = "UNKNOWN_ORIGIN"
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": None,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, unknown_artifacts)
            self.assertIn("artifacts correction_origin invalid", str(ctx.exception))

        # Mismatched feedback path for test-gate origin
        test_gate_artifacts = dict(self.sample_artifacts)
        test_gate_artifacts["correction_origin"] = "BOOTSTRAP"
        with mock.patch(f"{TARGET_MODULE}.load_prior_worker_and_receipt") as mock_prereq, \
             mock.patch(f"{TARGET_MODULE}.load_task") as mock_task, \
             mock.patch(f"{TARGET_MODULE}.load_bound_test_gate_feedback") as mock_test_gate:
            mock_prereq.return_value = {
                "worker_info": self.sample_worker_info,
                "receipt": None,
            }
            mock_task.return_value = (self.sample_task, self.sample_sources)
            mock_test_gate.return_value = (
                {"reviewer": "test-gate"},
                "8" * 64,
                "runs/mismatched_test_gate.json",
            )
            with self.assertRaises(ProposalError) as ctx:
                load_worker_correction_evidence(self.root, self.admitted_task_path, test_gate_artifacts)
            self.assertIn("feedback path mismatch", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
