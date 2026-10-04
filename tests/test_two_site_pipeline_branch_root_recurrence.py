#!/usr/bin/env python3
"""Offline unit tests for branch-gate root factor recurrence prevention."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
import unittest
from unittest.mock import patch

try:
    import scripts.two_site_proposal_pipeline as pipeline
    from scripts.two_site_proposal_pipeline import (
        BRANCH, ProposalError, run_pipeline,
    )
    from scripts.two_site_proposal_runner import file_sha256
    import tests.test_two_site_pipeline_pre_review_gate as support
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipeline  # type: ignore[no-redef]
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        BRANCH, ProposalError, run_pipeline,
    )
    from two_site_proposal_runner import file_sha256  # type: ignore[no-redef]
    import test_two_site_pipeline_pre_review_gate as support  # type: ignore[no-redef]


class TestPipelineBranchRootRecurrence(unittest.TestCase):
    """Unit tests for narrow branch root factor detection and repeat prevention."""

    def setUp(self) -> None:
        self._support_fixture = support.TestPipelinePreReviewGate()
        self._support_fixture.setUp()
        self.root = self._support_fixture.root
        self.task_path = self._support_fixture.task_path
        self.state = self._support_fixture.state

    def tearDown(self) -> None:
        self._support_fixture.tearDown()

    def test_renamed_param_branch_root_recurrence_aborts_before_worker_2(self) -> None:
        stdout0 = (
            "FAILED tests/test_two_site_codex_autopilot.py::test_repo[param_alpha] - AutopilotError\n"
            f"E   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n"
            ">   validate_repository(root)\n"
        )
        stdout1 = (
            "FAILED tests/test_two_site_codex_autopilot.py::test_repo[param_beta_renamed] - AutopilotError\n"
            f"E   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n"
            ">   validate_repository(root)\n"
        )
        rcpt0 = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=stdout0,
        )
        rcpt1 = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=stdout1,
        )

        self.assertNotEqual(
            pipeline.test_failure_signature(rcpt0),
            pipeline.test_failure_signature(rcpt1),
        )
        self.assertEqual(
            pipeline._branch_root_failure_factor(rcpt0),
            pipeline._branch_root_failure_factor(rcpt1),
        )

        runner, runner_calls = support.make_runner(self.root, [
            {"content": "# v1\n"},
            {"content": "# v2\n"},
        ])
        candidate_tester, candidate_calls = support.make_candidate_tester([rcpt0, rcpt1])

        with patch.object(pipeline, "git", side_effect=support.fake_git), \
             patch.object(pipeline, "runner_run", side_effect=runner), \
             patch.object(pipeline, "run_candidate_tests", side_effect=candidate_tester):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-rec", 300, None, self.state)

        err_msg = str(ctx.exception)
        self.assertIn("repeated branch root factor", err_msg)
        expected_factor = pipeline._branch_root_failure_factor(rcpt0)
        self.assertIn(str(expected_factor), err_msg)

        self.assertEqual(len(runner_calls), 2)
        self.assertEqual(runner_calls[0][:2], ("worker", "run-rec-worker-0"))
        self.assertEqual(runner_calls[1][:2], ("worker", "run-rec-worker-1"))
        self.assertFalse(any(c[0] == "verifier" for c in runner_calls))
        self.assertFalse(any(c[1] == "run-rec-worker-2" for c in runner_calls))
        self.assertEqual(len(candidate_calls), 2)

        pipeline_dir = self.root / ".local/two-site-refactor/pipeline/run-rec"
        rcpt0_path = pipeline_dir / "pre-review-tests-0.json"
        rcpt1_path = pipeline_dir / "pre-review-tests-1.json"
        rcpt2_path = pipeline_dir / "pre-review-tests-2.json"
        self.assertTrue(rcpt0_path.exists())
        self.assertTrue(rcpt1_path.exists())
        self.assertFalse(rcpt2_path.exists())

        fb1_path = pipeline_dir / "feedback-correction-1.json"
        fb2_path = pipeline_dir / "feedback-correction-2.json"
        self.assertTrue(fb1_path.exists())
        self.assertFalse(fb2_path.exists())

        self.assertNotEqual(self.state["state"], "READY_FOR_CODEX")

    def test_identical_branch_failure_retains_repeated_signature_behavior(self) -> None:
        stdout = (
            "FAILED tests/test_two_site_codex_autopilot.py::test_repo - AutopilotError\n"
            f"E   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n"
            ">   validate_repository(root)\n"
        )
        rcpt0 = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=stdout,
        )
        rcpt1 = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=stdout,
        )

        self.assertEqual(
            pipeline.test_failure_signature(rcpt0),
            pipeline.test_failure_signature(rcpt1),
        )

        runner, runner_calls = support.make_runner(self.root, [
            {"content": "# v1\n"},
            {"content": "# v2\n"},
        ])
        candidate_tester, candidate_calls = support.make_candidate_tester([rcpt0, rcpt1])

        with patch.object(pipeline, "git", side_effect=support.fake_git), \
             patch.object(pipeline, "runner_run", side_effect=runner), \
             patch.object(pipeline, "run_candidate_tests", side_effect=candidate_tester):
            with self.assertRaises(ProposalError) as ctx:
                run_pipeline(self.root, self.task_path, "run-ident", 300, None, self.state)

        err_msg = str(ctx.exception)
        self.assertIn("repeated test failure signature", err_msg)
        self.assertNotIn("repeated branch root factor", err_msg)

        self.assertEqual(len(runner_calls), 2)
        self.assertEqual(runner_calls[0][:2], ("worker", "run-ident-worker-0"))
        self.assertEqual(runner_calls[1][:2], ("worker", "run-ident-worker-1"))
        self.assertFalse(any(c[0] == "verifier" for c in runner_calls))
        self.assertFalse(any(c[1] == "run-ident-worker-2" for c in runner_calls))
        self.assertEqual(len(candidate_calls), 2)
        self.assertNotEqual(self.state["state"], "READY_FOR_CODEX")

    def test_branch_failure_then_passed_proceeds_to_reviewer_and_ready(self) -> None:
        stdout0 = (
            "FAILED tests/test_two_site_codex_autopilot.py::test_repo - AutopilotError\n"
            f"E   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n"
            ">   validate_repository(root)\n"
        )
        rcpt0 = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=stdout0,
        )
        rcpt1 = support.build_test_receipt(
            state="PASSED",
            returncode=0,
            stdout_tail="1 passed in 0.04s\n",
        )

        runner, runner_calls = support.make_runner(self.root, [
            {"content": "# v1\n"},
            {"content": "# v2\n"},
            {"status": "VERIFIED"},
        ])
        candidate_tester, candidate_calls = support.make_candidate_tester([rcpt0, rcpt1])

        with patch.object(pipeline, "git", side_effect=support.fake_git), \
             patch.object(pipeline, "runner_run", side_effect=runner), \
             patch.object(pipeline, "run_candidate_tests", side_effect=candidate_tester):
            res = run_pipeline(self.root, self.task_path, "run-f2p-br", 300, None, self.state)

        self.assertEqual(len(runner_calls), 3)
        self.assertEqual(runner_calls[0][:2], ("worker", "run-f2p-br-worker-0"))
        self.assertEqual(runner_calls[1][:2], ("worker", "run-f2p-br-worker-1"))
        self.assertEqual(runner_calls[2][:2], ("verifier", "run-f2p-br-reviewer-1"))
        self.assertNotIn("run-f2p-br-reviewer-0", [c[1] for c in runner_calls])

        rcpt1_path = self.root / ".local/two-site-refactor/pipeline/run-f2p-br/pre-review-tests-1.json"
        self.assertTrue(rcpt1_path.exists())
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 1)
        self.assertEqual(res["pre_review_test_state"], "PASSED")
        self.assertEqual(res["final_worker_run_id"], "run-f2p-br-worker-1")
        self.assertEqual(res["final_reviewer_run_id"], "run-f2p-br-reviewer-1")

    def test_detector_distinguishes_different_exceptions_branches_and_untyped_lookalikes(self) -> None:
        rcpt_diff_type = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=f"FAILED test - ValueError\nE   ValueError: autopilot requires branch {BRANCH}\n",
        )
        self.assertIsNone(pipeline._branch_root_failure_factor(rcpt_diff_type))

        rcpt_diff_msg = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED test - AutopilotError\nE   scripts.two_site_codex_autopilot.AutopilotError: integration HEAD is invalid\n",
        )
        self.assertIsNone(pipeline._branch_root_failure_factor(rcpt_diff_msg))

        rcpt_diff_branch = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail="FAILED test - AutopilotError\nE   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch other-branch\n",
        )
        self.assertIsNone(pipeline._branch_root_failure_factor(rcpt_diff_branch))

        rcpt_untyped = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=f"fatal error: scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n",
            stderr_tail="",
        )
        self.assertIsNone(pipeline._branch_root_failure_factor(rcpt_untyped))

        rcpt_valid = support.build_test_receipt(
            state="FAILED",
            returncode=1,
            stdout_tail=f"FAILED test\nE   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n",
        )
        factor = pipeline._branch_root_failure_factor(rcpt_valid)
        self.assertIsNotNone(factor)
        self.assertIsInstance(factor, str)
        self.assertEqual(len(factor), 64)

    def test_detector_and_receipt_validation_rejects_non_failed_and_malformed(self) -> None:
        rcpt_passed = support.build_test_receipt(state="PASSED", returncode=0)
        with self.assertRaises(ProposalError):
            pipeline._branch_root_failure_factor(rcpt_passed)

        with self.assertRaises(Exception):
            pipeline._branch_root_failure_factor({"state": "UNKNOWN"})
        with self.assertRaises(Exception):
            pipeline._branch_root_failure_factor("not a mapping")  # type: ignore[arg-type]
        with self.assertRaises(Exception):
            pipeline._branch_root_failure_factor({})

    def test_detector_invariance_under_ordering_and_local_names_but_distinct_selectors(self) -> None:
        sel_a = ["tests/test_two_site_codex_autopilot.py"]
        sel_b = ["tests/test_two_site_proposal_pipeline.py"]
        stdout_a = (
            "FAILED tests/test_autopilot.py::test_repo - AutopilotError\n"
            f"E   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n"
            ">   var_alpha = git_branch(root)\n"
            ">   self.assertEqual(var_alpha, expected_val)\n"
        )
        stdout_b = (
            "FAILED tests/test_autopilot.py::test_repo - AutopilotError\n"
            f"E   scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch {BRANCH}\n"
            ">   self.assertEqual(var_beta, expected_val)\n"
            ">   var_beta = git_branch(root)\n"
        )
        rcpt_a = support.build_test_receipt(
            state="FAILED", returncode=1, stdout_tail=stdout_a, selectors=sel_a,
        )
        rcpt_b = support.build_test_receipt(
            state="FAILED", returncode=1, stdout_tail=stdout_b, selectors=sel_a,
        )
        self.assertEqual(
            pipeline._branch_root_failure_factor(rcpt_a),
            pipeline._branch_root_failure_factor(rcpt_b),
        )

        rcpt_diff_sel = support.build_test_receipt(
            state="FAILED", returncode=1, stdout_tail=stdout_a, selectors=sel_b,
        )
        self.assertNotEqual(
            pipeline._branch_root_failure_factor(rcpt_a),
            pipeline._branch_root_failure_factor(rcpt_diff_sel),
        )

        rcpt_multi_1 = support.build_test_receipt(
            state="FAILED", returncode=1, stdout_tail=stdout_a, selectors=sel_a + sel_b,
        )
        rcpt_multi_2 = support.build_test_receipt(
            state="FAILED", returncode=1, stdout_tail=stdout_a, selectors=sel_b + sel_a,
        )
        self.assertEqual(
            pipeline._branch_root_failure_factor(rcpt_multi_1),
            pipeline._branch_root_failure_factor(rcpt_multi_2),
        )


if __name__ == "__main__":
    unittest.main()
