from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_one_once import execute_worker_one_once


def _build_fixture_context(root: Path, worker_index: int = 1) -> dict:
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
            "changes": [{"path": "a.py", "original_sha256": "0" * 64, "content": "v1", "replacements": []}],
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


class TestTwoSiteQuotaResumeWorkerOneOnceSmoke(unittest.TestCase):
    def test_worker_one_once_smoke_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=1)
            req = {"role": "worker", "request_id": "req-1"}

            lock_state = {"held": False, "runner_checked": False, "validate_checked": False}

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
                lock_state["runner_checked"] = True
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                run_dir.mkdir(parents=True, exist_ok=True)
                return run_dir

            def mock_validate_run(*args, **kwargs):
                self.assertTrue(lock_state["held"])
                lock_state["validate_checked"] = True
                self.assertEqual(kwargs.get("prior_worker"), (
                    "orig-req-100-worker-0",
                    "1" * 64,
                    '{"status": "PROPOSAL_READY"}',
                    "conv-work-0",
                ))
                self.assertEqual(kwargs.get("expected_feedback_sha"), "f" * 64)
                self.assertEqual(kwargs.get("seen_convs"), {"conv-work-0", "conv-rev-0"})
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
                 patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner), \
                 patch("scripts.two_site_quota_resume_worker_one_once.validate_run", side_effect=mock_validate_run):

                res = execute_worker_one_once(root, req)

            self.assertFalse(lock_state["held"])
            self.assertTrue(lock_state["runner_checked"])
            self.assertTrue(lock_state["validate_checked"])
            self.assertEqual(res["state"], "WORKER_PROPOSAL_READY")
            self.assertEqual(res["correction_count"], 1)
            self.assertEqual(res["final_worker_run_id"], expected_run_id)

    def test_stop_from_pre_provider_check_prevents_simulated_popen(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=1)
            req = {"role": "worker", "request_id": "req-1"}

            @contextmanager
            def mock_context(r: Path, q: dict):
                yield ctx

            simulated_popen_called = False

            def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                nonlocal simulated_popen_called
                if pre_provider_check is not None:
                    pre_provider_check()
                simulated_popen_called = True
                return root / "not_reached"

            with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                 patch("scripts.two_site_quota_resume_worker_one_once.validate_quota_continuation_request", side_effect=ProposalError("STOP: quota halted")), \
                 patch("scripts.two_site_quota_resume_worker_one_once.check_state"), \
                 patch("scripts.two_site_quota_resume_worker_one_once.ref_snapshot", return_value={}), \
                 patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_one_once(root, req)
                self.assertIn("STOP", str(exc_info.exception))

            self.assertFalse(simulated_popen_called)

    def test_worker_index_two_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-1"}

            @contextmanager
            def mock_context(r: Path, q: dict):
                yield ctx

            runner_called = False

            def mock_runner(*args, **kwargs):
                nonlocal runner_called
                runner_called = True
                return root / "not_reached"

            with patch("scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context", side_effect=mock_context), \
                 patch("scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner):

                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_one_once(root, req)
                self.assertIn("worker_index must be 1", str(exc_info.exception))

            self.assertFalse(runner_called)
            expected_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-1"
            self.assertFalse(expected_dir.exists())


if __name__ == "__main__":
    unittest.main()
