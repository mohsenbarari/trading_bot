from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    from scripts.two_site_quota_resume_post_worker_zero_remainder import (
        execute_worker_zero_post_result_remainder,
    )
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    from two_site_quota_resume_post_worker_zero_remainder import (  # type: ignore[no-redef]
        execute_worker_zero_post_result_remainder,
    )

try:
    from tests import test_two_site_quota_resume_post_worker_zero as _fixture_module
except ImportError:
    import test_two_site_quota_resume_post_worker_zero as _fixture_module  # type: ignore[no-redef]


class TestTwoSiteQuotaResumePostWorkerZeroRemainderFileproof(unittest.TestCase):
    def setUp(self):
        self.fixture = _fixture_module.TestTwoSiteQuotaResumePostWorkerZero()
        self.fixture.setUp()
        self.fixture.base_ctx["admitted_request"]["timeout"] = 300
        self.fixture.admitted_request["timeout"] = 300
        pipeline_dir = self.fixture.root / ".local/two-site-refactor/pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if hasattr(self, "fixture"):
            self.fixture.tearDown()

    def test_file_backed_remainder_success(self):
        returned_state, run_dir, res_sha = self.fixture._create_real_worker_zero()
        run_id = returned_state["final_worker_run_id"]

        called_kwargs = {}

        def _mock_run_post_worker_remainder(*args, **kwargs):
            called_kwargs.update(kwargs)
            state = kwargs["state"]
            state["state"] = "READY_FOR_CODEX"
            state["final_reviewer_run_id"] = "run-post-reviewer-0"

        remainder_mod = sys.modules[execute_worker_zero_post_result_remainder.__module__]
        with patch.object(
            remainder_mod,
            "_run_post_worker_remainder",
            side_effect=_mock_run_post_worker_remainder,
        ):
            result_state = execute_worker_zero_post_result_remainder(
                self.fixture.root, {}, returned_state
            )

        self.assertEqual(result_state["state"], "READY_FOR_CODEX")
        self.assertEqual(result_state["final_reviewer_run_id"], "run-post-reviewer-0")
        self.assertEqual(result_state["final_worker_run_id"], run_id)
        self.assertEqual(result_state["proposal_sha256"], res_sha)

        evidence_dir = (
            self.fixture.root
            / ".local/two-site-refactor/pipeline"
            / self.fixture.base_ctx["continuation_run_prefix"]
        )
        self.assertTrue(evidence_dir.is_dir())
        self.assertEqual(called_kwargs.get("root"), self.fixture.root)
        self.assertEqual(called_kwargs.get("timeout"), 300)
        self.assertEqual(called_kwargs.get("run_prefix"), "run-post")
        self.assertEqual(called_kwargs.get("evidence_dir"), evidence_dir)
        self.assertEqual(called_kwargs.get("worker_dir"), run_dir)
        expected_current = (
            run_id,
            res_sha,
            (run_dir / "result.json").read_text("utf-8"),
            f"conv-{run_id}",
        )
        self.assertEqual(called_kwargs.get("current_worker"), expected_current)
        self.assertEqual(called_kwargs.get("seen_convs"), {f"conv-{run_id}"})
        self.assertEqual(
            called_kwargs.get("worker_res"),
            json.loads((run_dir / "result.json").read_text("utf-8")),
        )

    def test_result_json_tamper_fails_state_check(self):
        returned_state, run_dir, _ = self.fixture._create_real_worker_zero()
        result_file = run_dir / "result.json"

        called = False

        def _tamper_run_post_worker_remainder(*args, **kwargs):
            nonlocal called
            called = True
            res_data = json.loads(result_file.read_text("utf-8"))
            res_data["summary"] = "mutated-summary-drift"
            result_file.write_text(json.dumps(res_data), "utf-8")

            state_check = kwargs["state_check"]
            with self.assertRaises(ProposalError):
                state_check()
            state_check()

        remainder_mod = sys.modules[execute_worker_zero_post_result_remainder.__module__]
        with patch.object(
            remainder_mod,
            "_run_post_worker_remainder",
            side_effect=_tamper_run_post_worker_remainder,
        ):
            with self.assertRaises(ProposalError):
                execute_worker_zero_post_result_remainder(
                    self.fixture.root, {}, returned_state
                )

        self.assertTrue(called)

    def test_status_json_tamper_fails_state_check(self):
        returned_state, run_dir, _ = self.fixture._create_real_worker_zero()
        status_file = run_dir / "status.json"

        called = False

        def _tamper_run_post_worker_remainder(*args, **kwargs):
            nonlocal called
            called = True
            status_data = json.loads(status_file.read_text("utf-8"))
            status_data["conversation_id"] = "conv-tampered"
            status_file.write_text(json.dumps(status_data), "utf-8")

            state_check = kwargs["state_check"]
            with self.assertRaises(ProposalError):
                state_check()
            state_check()

        remainder_mod = sys.modules[execute_worker_zero_post_result_remainder.__module__]
        with patch.object(
            remainder_mod,
            "_run_post_worker_remainder",
            side_effect=_tamper_run_post_worker_remainder,
        ):
            with self.assertRaises(ProposalError):
                execute_worker_zero_post_result_remainder(
                    self.fixture.root, {}, returned_state
                )

        self.assertTrue(called)
