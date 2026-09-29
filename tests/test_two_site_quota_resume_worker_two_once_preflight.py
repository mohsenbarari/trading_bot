from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_provider_broker import BrokerError
from scripts.two_site_quota_resume_worker_two_once import execute_worker_two_once
from tests.test_two_site_quota_resume_worker_two_once_smoke import _build_fixture_context


class TestTwoSiteQuotaResumeWorkerTwoOncePreflight(unittest.TestCase):
    def test_pre_provider_fault_matrix_prevents_simulated_popen(self) -> None:
        """Requirement 1: STOP, request/task, receipt/artifact/evidence drift fail before Provider."""
        drift_scenarios = [
            ("direct_stop", "STOP: admission halt"),
            ("request_drift", "continuation request drifted across re-admission"),
            ("task_path_drift", "admitted task path drifted across re-admission"),
            ("check_state_drift", "state ref drifted during preflight"),
            ("terminal_receipt_drift", "original terminal receipt drifted in pre_provider_check"),
            ("artifacts_drift", "worker resume artifacts drifted in pre_provider_check"),
            ("evidence_drift", "worker correction evidence drifted in pre_provider_check"),
            ("late_file_stop", "stop requested before continuation dispatch"),
            ("late_broker_flag_stop", "stop requested before continuation dispatch"),
        ]

        for case_name, expected_err in drift_scenarios:
            with self.subTest(drift_case=case_name):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=2)
                    req = {"role": "worker", "request_id": "req-2"}

                    lock_state = {"held": False, "held_during_preflight": False}

                    @contextmanager
                    def mock_context(r: Path, q: dict):
                        lock_state["held"] = True
                        try:
                            yield ctx
                        finally:
                            lock_state["held"] = False

                    runner_state = {
                        "runner_calls": 0,
                        "callback_invoked": False,
                        "provider_entered": False,
                    }

                    def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                        runner_state["runner_calls"] += 1
                        self.assertIsNotNone(pre_provider_check)
                        runner_state["callback_invoked"] = True
                        if lock_state["held"]:
                            lock_state["held_during_preflight"] = True
                        if case_name == "late_file_stop":
                            stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
                            stop_path.parent.mkdir(parents=True, exist_ok=True)
                            stop_path.write_text("stop")
                        if case_name == "late_broker_flag_stop":
                            with patch("scripts.two_site_provider_broker._STOP", True):
                                pre_provider_check()
                        else:
                            pre_provider_check()
                        runner_state["provider_entered"] = True
                        return root / ".local/two-site-refactor/proposals" / run_id

                    val_res = (ctx["admitted_request"], ctx["admitted_task_path"], None)
                    val_side_effect = None
                    rec_res = ctx["original_terminal_receipt"]
                    art_res = ctx["artifacts"]
                    evi_res = ctx["evidence"]

                    check_state_calls = 0

                    def mock_check_state(*args, **kwargs):
                        nonlocal check_state_calls
                        check_state_calls += 1
                        if case_name == "check_state_drift" and check_state_calls > 1:
                            raise ProposalError("state ref drifted during preflight")

                    if case_name == "direct_stop":
                        val_side_effect = ProposalError("STOP: admission halt")
                    elif case_name == "request_drift":
                        drifted_req = dict(ctx["admitted_request"])
                        drifted_req["timeout"] = 999
                        val_res = (drifted_req, ctx["admitted_task_path"], None)
                    elif case_name == "task_path_drift":
                        val_res = (ctx["admitted_request"], root / "drifted_task.json", None)
                    elif case_name == "terminal_receipt_drift":
                        rec_res = {"receipt_id": "drifted-receipt"}
                    elif case_name == "artifacts_drift":
                        drifted_art = dict(ctx["artifacts"])
                        drifted_art["semantic_task_fingerprint"] = "drifted-fp"
                        art_res = drifted_art
                    elif case_name == "evidence_drift":
                        drifted_evi = dict(ctx["evidence"])
                        drifted_evi["feedback_sha"] = "0" * 64
                        evi_res = drifted_evi

                    fake_history = {
                        "complete": True,
                        "conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
                        "ordered_conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
                    }

                    with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_two_once.validate_quota_continuation_request", return_value=val_res, side_effect=val_side_effect), \
                         patch("scripts.two_site_quota_resume_worker_two_once.read_terminal_quota_record", return_value=rec_res), \
                         patch("scripts.two_site_quota_resume_worker_two_once.derive_worker_resume_artifacts", return_value=art_res), \
                         patch("scripts.two_site_quota_resume_worker_two_once.load_worker_correction_evidence", return_value=evi_res), \
                         patch("scripts.two_site_quota_resume_worker_two_once.check_state", side_effect=mock_check_state), \
                         patch("scripts.two_site_quota_resume_worker_two_once.ref_snapshot", return_value={}), \
                         patch("scripts.two_site_quota_resume_worker_two_once.complete_worker_two_history", return_value=fake_history), \
                         patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_two_once(root, req)
                        self.assertIn(expected_err, str(exc_info.exception))
                        if expected_err == "stop requested before continuation dispatch":
                            self.assertIsInstance(exc_info.exception, BrokerError)

                    self.assertEqual(runner_state["runner_calls"], 1)
                    self.assertTrue(runner_state["callback_invoked"])
                    self.assertFalse(runner_state["provider_entered"])
                    self.assertTrue(lock_state["held_during_preflight"])
                    self.assertFalse(lock_state["held"])
                    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
                    if proposals_dir.exists():
                        self.assertEqual(list(proposals_dir.glob("*retry*")), [])

    def test_history_and_reviewer_status_drift_fail_before_provider(self) -> None:
        """Requirement 2: History and Reviewer1 raw-status drift fail before Provider."""
        drift_scenarios = [
            ("reviewer_status_bytes_drift", "prior reviewer status bytes drifted in pre_provider_check"),
            ("history_incomplete", "worker-2 history must be complete in pre_provider_check"),
            ("history_drift", "worker-2 history drifted in pre_provider_check"),
        ]

        for case_name, expected_err in drift_scenarios:
            with self.subTest(drift_case=case_name):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=2)
                    req = {"role": "worker", "request_id": "req-2"}

                    lock_state = {"held": False, "held_during_preflight": False}

                    @contextmanager
                    def mock_context(r: Path, q: dict):
                        lock_state["held"] = True
                        try:
                            yield ctx
                        finally:
                            lock_state["held"] = False

                    runner_state = {
                        "runner_calls": 0,
                        "callback_invoked": False,
                        "provider_entered": False,
                    }

                    rev_status_path = (root / ctx["artifacts"]["reviewer_path"]).with_name("status.json")

                    def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                        runner_state["runner_calls"] += 1
                        self.assertIsNotNone(pre_provider_check)
                        runner_state["callback_invoked"] = True
                        if lock_state["held"]:
                            lock_state["held_during_preflight"] = True
                        if case_name == "reviewer_status_bytes_drift":
                            rev_status_path.write_bytes(b'{"conversation_id": "conv-rev-drifted"}')
                        pre_provider_check()
                        runner_state["provider_entered"] = True
                        return root / ".local/two-site-refactor/proposals" / run_id

                    valid_history = {
                        "complete": True,
                        "conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
                        "ordered_conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
                    }

                    history_calls = 0

                    def mock_complete_history(*args, **kwargs):
                        nonlocal history_calls
                        history_calls += 1
                        if history_calls == 1:
                            return valid_history
                        if case_name == "history_incomplete":
                            return {"complete": False}
                        if case_name == "history_drift":
                            return {
                                "complete": True,
                                "conversation_ids": ["conv-drifted"],
                                "ordered_conversation_ids": ["conv-drifted"],
                            }
                        return valid_history

                    with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_two_once.validate_quota_continuation_request", return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None)), \
                         patch("scripts.two_site_quota_resume_worker_two_once.read_terminal_quota_record", return_value=ctx["original_terminal_receipt"]), \
                         patch("scripts.two_site_quota_resume_worker_two_once.derive_worker_resume_artifacts", return_value=ctx["artifacts"]), \
                         patch("scripts.two_site_quota_resume_worker_two_once.load_worker_correction_evidence", return_value=ctx["evidence"]), \
                         patch("scripts.two_site_quota_resume_worker_two_once.check_state"), \
                         patch("scripts.two_site_quota_resume_worker_two_once.ref_snapshot", return_value={}), \
                         patch("scripts.two_site_quota_resume_worker_two_once.complete_worker_two_history", side_effect=mock_complete_history), \
                         patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_two_once(root, req)
                        self.assertIn(expected_err, str(exc_info.exception))

                    self.assertEqual(runner_state["runner_calls"], 1)
                    self.assertTrue(runner_state["callback_invoked"])
                    self.assertFalse(runner_state["provider_entered"])
                    self.assertTrue(lock_state["held_during_preflight"])
                    self.assertFalse(lock_state["held"])

    def test_preexisting_continuation_retry_id_blocks_before_runner(self) -> None:
        """Requirement 3: Retry ID collision blocks without runner invocation."""
        cases = [
            ("canonical", "cont-req-200-worker-2"),
            ("retry_1", "cont-req-200-worker-2-retry-1"),
            ("retry_2", "cont-req-200-worker-2-retry-2"),
        ]

        for label, existing_run_id in cases:
            with self.subTest(candidate=label):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=2)
                    req = {"role": "worker", "request_id": "req-2"}

                    proposals_dir = root / ".local/two-site-refactor/proposals"
                    conflict_path = proposals_dir / existing_run_id
                    conflict_path.mkdir(parents=True, exist_ok=True)

                    lock_state = {"held": False}

                    @contextmanager
                    def mock_context(r: Path, q: dict):
                        lock_state["held"] = True
                        try:
                            yield ctx
                        finally:
                            lock_state["held"] = False

                    runner_calls = 0

                    def mock_runner(*args, **kwargs):
                        nonlocal runner_calls
                        runner_calls += 1
                        return root / "not_reached"

                    with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_two_once(root, req)
                        self.assertIn(f"worker run ID already exists: {existing_run_id}", str(exc_info.exception))

                    self.assertEqual(runner_calls, 0)
                    self.assertFalse(lock_state["held"])


if __name__ == "__main__":
    unittest.main()
