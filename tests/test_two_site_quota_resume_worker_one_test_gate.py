from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_one_once import execute_worker_one_once
from tests.test_two_site_quota_resume_worker_one_once_smoke import _build_fixture_context


def _make_test_gate_context(root: Path, origin: str) -> dict[str, Any]:
    ctx = _build_fixture_context(root, worker_index=1)
    ctx["artifacts"]["correction_origin"] = origin
    ctx["artifacts"]["reviewer_path"] = None
    ctx["artifacts"]["final_reviewer_run_id"] = None
    ctx["evidence"]["reviewer_status"] = None
    ctx["evidence"]["reviewer_result"] = None
    ctx["evidence"]["reviewer_sha"] = None
    ctx["evidence"]["feedback"] = {"reviewer": "test-gate"}
    return ctx


class TestTwoSiteQuotaResumeWorkerOneTestGate(unittest.TestCase):
    def test_positive_worker_one_test_gate_origins(self) -> None:
        """Prove clean path for BOOTSTRAP and TEST_FAILED origins under lock."""
        for origin in ("BOOTSTRAP", "TEST_FAILED"):
            with self.subTest(origin=origin):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _make_test_gate_context(root, origin)
                    req = {"role": "worker", "request_id": "req-1"}

                    lock_state = {"held": False}
                    runner_state = {"pre_provider_called": False, "simulated_popen": False}
                    validate_state = {"called": False}

                    @contextmanager
                    def mock_context(r: Path, q: dict):
                        lock_state["held"] = True
                        try:
                            yield ctx
                        finally:
                            lock_state["held"] = False

                    expected_run_id = "cont-req-200-worker-1"
                    run_dir = root / ".local/two-site-refactor/proposals" / expected_run_id

                    def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                        self.assertTrue(lock_state["held"])
                        self.assertIsNotNone(pre_provider_check)
                        pre_provider_check()
                        runner_state["pre_provider_called"] = True
                        runner_state["simulated_popen"] = True
                        run_dir.mkdir(parents=True, exist_ok=True)
                        return run_dir

                    def mock_validate_run(*args, **kwargs):
                        self.assertTrue(lock_state["held"])
                        validate_state["called"] = True
                        self.assertEqual(kwargs.get("seen_convs"), {"conv-work-0"})
                        self.assertEqual(kwargs.get("expected_feedback_sha"), "f" * 64)
                        new_res = {
                            "status": "PROPOSAL_READY",
                            "changes": [{"path": "a.py", "original_sha256": "0" * 64, "content": "v2", "replacements": []}],
                        }
                        new_status = {"status": "PROPOSAL_READY", "conversation_id": "conv-work-1"}
                        return new_res, new_status, "2" * 64, '{"status": "PROPOSAL_READY"}'

                    with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_one_once.validate_quota_continuation_request", return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None)), \
                         patch("scripts.two_site_quota_resume_worker_one_once.read_terminal_quota_record", return_value=ctx["original_terminal_receipt"]), \
                         patch("scripts.two_site_quota_resume_worker_one_once.derive_worker_resume_artifacts", return_value=ctx["artifacts"]), \
                         patch("scripts.two_site_quota_resume_worker_one_once.load_worker_correction_evidence", return_value=ctx["evidence"]), \
                         patch("scripts.two_site_quota_resume_worker_one_once.check_state"), \
                         patch("scripts.two_site_quota_resume_worker_one_once.ref_snapshot", return_value={}), \
                         patch("scripts.two_site_quota_resume_worker_one_once.bounded_file_bytes") as mock_bounded, \
                         patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner), \
                         patch("scripts.two_site_quota_resume_worker_one_once.validate_run", side_effect=mock_validate_run):

                        res = execute_worker_one_once(root, req)

                    self.assertFalse(lock_state["held"])
                    self.assertTrue(runner_state["pre_provider_called"])
                    self.assertTrue(runner_state["simulated_popen"])
                    self.assertTrue(validate_state["called"])
                    self.assertEqual(res["state"], "WORKER_PROPOSAL_READY")
                    self.assertEqual(res["final_worker_run_id"], expected_run_id)
                    self.assertEqual(mock_bounded.call_count, 0)

    def test_pre_provider_failures_prevent_simulated_popen(self) -> None:
        """Prove STOP/admission and evidence feedback_sha drift prevent simulated Popen."""
        scenarios = [
            ("stop_admission", "STOP: admission halt"),
            ("feedback_sha_drift", "worker correction evidence drifted in pre_provider_check"),
        ]
        for origin in ("BOOTSTRAP", "TEST_FAILED"):
            for case_name, err_pattern in scenarios:
                with self.subTest(origin=origin, scenario=case_name):
                    with tempfile.TemporaryDirectory() as td:
                        root = Path(td).resolve()
                        ctx = _make_test_gate_context(root, origin)
                        req = {"role": "worker", "request_id": "req-1"}

                        lock_state = {"held": False}
                        runner_state = {"pre_provider_called": False, "simulated_popen": False}

                        @contextmanager
                        def mock_context(r: Path, q: dict):
                            lock_state["held"] = True
                            try:
                                yield ctx
                            finally:
                                lock_state["held"] = False

                        def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                            self.assertTrue(lock_state["held"])
                            self.assertIsNotNone(pre_provider_check)
                            runner_state["pre_provider_called"] = True
                            pre_provider_check()
                            runner_state["simulated_popen"] = True
                            return root / "not_reached"

                        val_side_effect = None
                        evi_return = ctx["evidence"]
                        if case_name == "stop_admission":
                            val_side_effect = ProposalError("STOP: admission halt")
                        elif case_name == "feedback_sha_drift":
                            drifted = dict(ctx["evidence"])
                            drifted["feedback_sha"] = "0" * 64
                            evi_return = drifted

                        with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                             patch("scripts.two_site_quota_resume_worker_one_once.validate_quota_continuation_request", return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None), side_effect=val_side_effect), \
                             patch("scripts.two_site_quota_resume_worker_one_once.read_terminal_quota_record", return_value=ctx["original_terminal_receipt"]), \
                             patch("scripts.two_site_quota_resume_worker_one_once.derive_worker_resume_artifacts", return_value=ctx["artifacts"]), \
                             patch("scripts.two_site_quota_resume_worker_one_once.load_worker_correction_evidence", return_value=evi_return), \
                             patch("scripts.two_site_quota_resume_worker_one_once.check_state"), \
                             patch("scripts.two_site_quota_resume_worker_one_once.ref_snapshot", return_value={}), \
                             patch("scripts.two_site_quota_resume_worker_one_once.bounded_file_bytes") as mock_bounded, \
                             patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                            with self.assertRaises(ProposalError) as exc_info:
                                execute_worker_one_once(root, req)
                            self.assertIn(err_pattern, str(exc_info.exception))

                        self.assertFalse(lock_state["held"])
                        self.assertTrue(runner_state["pre_provider_called"])
                        self.assertFalse(runner_state["simulated_popen"])
                        self.assertEqual(mock_bounded.call_count, 0)


if __name__ == "__main__":
    unittest.main()
