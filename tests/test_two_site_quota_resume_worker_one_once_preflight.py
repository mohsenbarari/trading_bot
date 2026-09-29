from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_provider_broker import BrokerError
from scripts.two_site_quota_resume_worker_one_once import execute_worker_one_once


def _build_fixture_context(root: Path, worker_index: int = 1) -> dict[str, Any]:
    orig_prefix = "orig-req-100"
    cont_prefix = "cont-req-200"
    proposal_sha = "1" * 64
    feedback_sha = "f" * 64
    final_worker_run_id = f"{orig_prefix}-worker-0"

    rev_dir = root / ".local/two-site-refactor/proposals" / f"{orig_prefix}-verifier-0"
    rev_dir.mkdir(parents=True, exist_ok=True)
    (rev_dir / "status.json").write_bytes(b'{"conversation_id": "conv-rev-0"}')

    worker_info = {
        "result": {
            "status": "PROPOSAL_READY",
            "changes": [
                {
                    "path": "a.py",
                    "original_sha256": "0" * 64,
                    "content": "v1",
                    "replacements": [],
                }
            ],
        },
        "status": {"conversation_id": "conv-work-0", "role": "worker"},
        "result_sha": proposal_sha,
        "result_text": '{"status": "PROPOSAL_READY"}',
        "conversation_id": "conv-work-0",
    }
    artifacts = {
        "worker_index": worker_index,
        "attempted_run_id": f"{orig_prefix}-worker-1",
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": final_worker_run_id,
        "proposal_sha256": proposal_sha,
        "proposal_path": f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{orig_prefix}-verifier-0/result.json",
        "semantic_task_fingerprint": "task-fp-100",
    }
    evidence = {
        "worker_info": worker_info,
        "reviewer_status": {"conversation_id": "conv-rev-0"},
        "feedback_path": f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-1.json",
        "feedback_sha": feedback_sha,
    }
    admitted = {
        "run_prefix": cont_prefix,
        "original_request_id": orig_prefix,
        "timeout": 30,
        "role": "worker",
    }
    terminal_receipt = {"receipt_id": orig_prefix}
    task = {"task_id": "task-100"}
    sources = {"a.py": "v0"}
    return {
        "continuation_run_prefix": cont_prefix,
        "original_run_prefix": orig_prefix,
        "source_head": "0" * 40,
        "initial_refs": {},
        "original_receipt": terminal_receipt,
        "original_terminal_receipt": terminal_receipt,
        "admitted_request": admitted,
        "admitted_task_path": root / "task.json",
        "artifacts": artifacts,
        "evidence": evidence,
        "task": task,
        "sources": sources,
    }


class TestTwoSiteQuotaResumeWorkerOneOncePreflight(unittest.TestCase):
    def test_pre_provider_fault_matrix_prevents_simulated_popen(self) -> None:
        """Parametrized pre-provider drift matrix ensures simulated Popen is never reached."""
        drift_scenarios = [
            ("direct_stop", "STOP: admission halt"),
            ("request_drift", "continuation request drifted across re-admission"),
            ("task_path_drift", "admitted task path drifted across re-admission"),
            ("terminal_receipt_drift", "original terminal receipt drifted in pre_provider_check"),
            ("artifacts_drift", "worker resume artifacts drifted in pre_provider_check"),
            ("evidence_drift", "worker correction evidence drifted in pre_provider_check"),
            ("reviewer_status_bytes_drift", "prior reviewer status bytes drifted in pre_provider_check"),
            ("check_state_drift", "state ref drifted during preflight"),
            ("late_file_stop", "stop requested before continuation dispatch"),
            ("late_broker_flag_stop", "stop requested before continuation dispatch"),
        ]

        for case_name, expected_err in drift_scenarios:
            with self.subTest(drift_case=case_name):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=1)
                    req = {"role": "worker", "request_id": "req-1"}

                    lock_state = {
                        "held": False,
                        "held_during_preflight": False,
                    }

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
                        "simulated_popen_called": False,
                    }

                    def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                        runner_state["runner_calls"] += 1
                        if pre_provider_check is not None:
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
                        runner_state["simulated_popen_called"] = True
                        return root / "not_reached"

                    val_res = (ctx["admitted_request"], ctx["admitted_task_path"], None)
                    val_side_effect = None
                    rec_res = ctx["original_terminal_receipt"]
                    art_res = ctx["artifacts"]
                    evi_res = ctx["evidence"]
                    bounded_bytes_seq = [b'{"conversation_id": "conv-rev-0"}', b'{"conversation_id": "conv-rev-0"}']
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
                    elif case_name == "reviewer_status_bytes_drift":
                        bounded_bytes_seq = [
                            b'{"conversation_id": "conv-rev-0"}',
                            b'{"conversation_id": "conv-rev-drifted"}',
                        ]

                    bytes_call_count = 0

                    def mock_bounded_file_bytes(*args, **kwargs):
                        nonlocal bytes_call_count
                        idx = min(bytes_call_count, len(bounded_bytes_seq) - 1)
                        bytes_call_count += 1
                        return bounded_bytes_seq[idx]

                    with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_one_once.validate_quota_continuation_request", return_value=val_res, side_effect=val_side_effect), \
                         patch("scripts.two_site_quota_resume_worker_one_once.read_terminal_quota_record", return_value=rec_res), \
                         patch("scripts.two_site_quota_resume_worker_one_once.derive_worker_resume_artifacts", return_value=art_res), \
                         patch("scripts.two_site_quota_resume_worker_one_once.load_worker_correction_evidence", return_value=evi_res), \
                         patch("scripts.two_site_quota_resume_worker_one_once.bounded_file_bytes", side_effect=mock_bounded_file_bytes), \
                         patch("scripts.two_site_quota_resume_worker_one_once.check_state", side_effect=mock_check_state), \
                         patch("scripts.two_site_quota_resume_worker_one_once.ref_snapshot", return_value={}), \
                         patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_one_once(root, req)
                        self.assertIn(expected_err, str(exc_info.exception))
                        if expected_err == "stop requested before continuation dispatch":
                            self.assertIsInstance(exc_info.exception, BrokerError)

                    self.assertTrue(runner_state["callback_invoked"])
                    self.assertFalse(runner_state["simulated_popen_called"])
                    self.assertEqual(runner_state["runner_calls"], 1)
                    self.assertTrue(lock_state["held_during_preflight"])
                    self.assertFalse(lock_state["held"])
                    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
                    if proposals_dir.exists():
                        self.assertEqual(list(proposals_dir.glob("*retry*")), [])

    def test_preexisting_run_id_rejects_runner_entirely(self) -> None:
        """Preexisting canonical and retry candidate run IDs reject runner invocation."""
        cases = [
            ("canonical", "cont-req-200-worker-1"),
            ("retry_1", "cont-req-200-worker-1-retry-1"),
            ("retry_2", "cont-req-200-worker-1-retry-2"),
        ]

        for label, existing_run_id in cases:
            with self.subTest(candidate=label):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=1)
                    req = {"role": "worker", "request_id": "req-1"}

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

                    with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_one_once(root, req)
                        self.assertIn(f"worker run ID already exists: {existing_run_id}", str(exc_info.exception))

                    self.assertEqual(runner_calls, 0)
                    self.assertFalse(lock_state["held"])

    def test_run_id_collision_and_length_limit(self) -> None:
        """Collision with original/attempted run ID or exceeding 80 chars rejects before runner."""
        sub_cases = [
            ("collision_attempted", {"attempted_run_id": "cont-req-200-worker-1"}, "cannot match original run ID"),
            ("collision_orig", {"continuation_run_prefix": "orig-req-100"}, "cannot match original run ID"),
            ("too_long", {"continuation_run_prefix": "x" * 75}, "exceeds 80 characters"),
        ]

        for label, override_ctx, expected_err in sub_cases:
            with self.subTest(sub_case=label):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=1)
                    for k, v in override_ctx.items():
                        if k in ctx:
                            ctx[k] = v
                        else:
                            ctx["artifacts"][k] = v
                    req = {"role": "worker", "request_id": "req-1"}

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

                    with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_one_once(root, req)
                        self.assertIn(expected_err, str(exc_info.exception))

                    self.assertEqual(runner_calls, 0)
                    self.assertFalse(lock_state["held"])

    def test_root_validation_and_early_refusal(self) -> None:
        """Non-Path, non-directory, or nonexistent root is refused before entering context."""
        req = {"role": "worker", "request_id": "req-1"}
        with self.assertRaises(ProposalError) as exc_info:
            execute_worker_one_once("not_a_path", req)  # type: ignore[arg-type]
        self.assertIn("root must be a Path", str(exc_info.exception))

        with tempfile.TemporaryDirectory() as td:
            non_dir_file = Path(td) / "file.txt"
            non_dir_file.write_text("not a directory")
            with self.assertRaises(ProposalError) as exc_info:
                execute_worker_one_once(non_dir_file, req)
            self.assertIn("root directory does not exist", str(exc_info.exception))

            missing_dir = Path(td) / "missing_subdir"
            with self.assertRaises(FileNotFoundError):
                execute_worker_one_once(missing_dir, req)

    def test_worker_index_and_evidence_schema_refusals(self) -> None:
        """Worker index != 1 and evidence/reviewer schema faults reject before runner_run."""
        fault_cases = [
            ("worker_index_2", {"artifacts.worker_index": 2}, "worker_index must be 1, got 2"),
            ("worker_index_bool", {"artifacts.worker_index": True}, "worker_index must be 1, got True"),
            ("worker_index_str", {"artifacts.worker_index": "1"}, "worker_index must be 1, got '1'"),
            ("missing_worker_info", {"evidence.worker_info": None}, "missing or invalid worker_info in evidence"),
            ("worker_info_bad_keys", {"evidence.worker_info": {"result": {}, "status": {}}}, "worker_info schema keys mismatch"),
            ("prior_result_sha_mismatch", {"evidence.worker_info.result_sha": "9" * 64}, "prior result_sha mismatch"),
            ("reviewer_conv_match_worker", {"evidence.reviewer_status.conversation_id": "conv-work-0"}, "reviewer conversation_id must differ from prior worker conversation_id"),
            ("reviewer_conv_empty", {"evidence.reviewer_status.conversation_id": ""}, "reviewer conversation_id missing or empty"),
            ("missing_reviewer_path", {"artifacts.reviewer_path": None}, "artifacts missing reviewer_path"),
        ]

        for label, overrides, expected_err in fault_cases:
            with self.subTest(fault_case=label):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td).resolve()
                    ctx = _build_fixture_context(root, worker_index=1)
                    req = {"role": "worker", "request_id": "req-1"}

                    for path_key, val in overrides.items():
                        parts = path_key.split(".")
                        target = ctx
                        for p in parts[:-1]:
                            target = target[p]
                        target[parts[-1]] = val

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

                    with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                         patch("scripts.two_site_quota_resume_worker_one_once.check_state"), \
                         patch("scripts.two_site_quota_resume_worker_one_once.ref_snapshot", return_value={}), \
                         patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                        with self.assertRaises(ProposalError) as exc_info:
                            execute_worker_one_once(root, req)
                        self.assertIn(expected_err, str(exc_info.exception))

                    self.assertEqual(runner_calls, 0)
                    self.assertFalse(lock_state["held"])


if __name__ == "__main__":
    unittest.main()
