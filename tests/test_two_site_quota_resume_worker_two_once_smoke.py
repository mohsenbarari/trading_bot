from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_quota_resume_worker_two_once import execute_worker_two_once


def _build_fixture_context(root: Path, worker_index: int = 2) -> dict:
    orig_prefix = "orig-req-100"
    cont_prefix = "cont-req-200"
    proposal_sha = "1" * 64
    feedback_sha = "f" * 64
    final_worker_run_id = f"{orig_prefix}-worker-1"

    rev_dir = root / ".local/two-site-refactor/proposals" / f"{orig_prefix}-verifier-1"
    rev_dir.mkdir(parents=True, exist_ok=True)
    (rev_dir / "status.json").write_bytes(b'{"conversation_id": "conv-rev-1"}')

    worker_info = {
        "result": {
            "status": "PROPOSAL_READY",
            "changes": [{"path": "a.py", "original_sha256": "0" * 64, "content": "v1", "replacements": []}],
        },
        "status": {"conversation_id": "conv-work-1", "role": "worker"},
        "result_sha": proposal_sha,
        "result_text": '{"status": "PROPOSAL_READY"}',
        "conversation_id": "conv-work-1",
    }
    artifacts = {
        "worker_index": worker_index,
        "attempted_run_id": f"{orig_prefix}-worker-2",
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": final_worker_run_id,
        "proposal_sha256": proposal_sha,
        "proposal_path": f".local/two-site-refactor/proposals/{final_worker_run_id}/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{orig_prefix}-verifier-1/result.json",
        "semantic_task_fingerprint": "task-fp-100",
    }
    evidence = {
        "worker_info": worker_info,
        "reviewer_status": {"conversation_id": "conv-rev-1"},
        "feedback_path": f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-2.json",
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


class TestTwoSiteQuotaResumeWorkerTwoOnceSmoke(unittest.TestCase):
    def test_worker_two_once_smoke_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}

            lock_state = {"held": False, "runner_calls": 0, "validate_checked": False}

            @contextmanager
            def mock_context(r: Path, q: dict):
                lock_state["held"] = True
                try:
                    yield ctx
                finally:
                    lock_state["held"] = False

            expected_run_id = "cont-req-200-worker-2"
            run_dir = root / ".local/two-site-refactor/proposals" / expected_run_id

            def mock_runner(r, t_path, role, run_id, timeout, *, pre_provider_check=None, **kwargs):
                self.assertTrue(lock_state["held"])
                lock_state["runner_calls"] += 1
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                run_dir.mkdir(parents=True, exist_ok=True)
                return run_dir

            fake_history = {
                "complete": True,
                "conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
                "ordered_conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
            }

            def mock_validate_run(*args, **kwargs):
                self.assertTrue(lock_state["held"])
                lock_state["validate_checked"] = True
                self.assertEqual(kwargs.get("prior_worker"), (
                    "orig-req-100-worker-1",
                    "1" * 64,
                    '{"status": "PROPOSAL_READY"}',
                    "conv-work-1",
                ))
                self.assertEqual(kwargs.get("expected_feedback_sha"), "f" * 64)
                self.assertEqual(kwargs.get("seen_convs"), {"conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"})
                new_res = {
                    "status": "PROPOSAL_READY",
                    "changes": [{"path": "a.py", "original_sha256": "0" * 64, "content": "v2", "replacements": []}],
                }
                new_status = {"status": "PROPOSAL_READY", "conversation_id": "conv-work-2"}
                return new_res, new_status, "2" * 64, '{"status": "PROPOSAL_READY"}'

            with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                 patch("scripts.two_site_quota_resume_worker_two_once.validate_quota_continuation_request", return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None)), \
                 patch("scripts.two_site_quota_resume_worker_two_once.read_terminal_quota_record", return_value=ctx["original_terminal_receipt"]), \
                 patch("scripts.two_site_quota_resume_worker_two_once.derive_worker_resume_artifacts", return_value=ctx["artifacts"]), \
                 patch("scripts.two_site_quota_resume_worker_two_once.load_worker_correction_evidence", return_value=ctx["evidence"]), \
                 patch("scripts.two_site_quota_resume_worker_two_once.check_state"), \
                 patch("scripts.two_site_quota_resume_worker_two_once.ref_snapshot", return_value={}), \
                 patch("scripts.two_site_quota_resume_worker_two_once.complete_worker_two_history", return_value=fake_history), \
                 patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner), \
                 patch("scripts.two_site_quota_resume_worker_two_once.validate_run", side_effect=mock_validate_run):

                res = execute_worker_two_once(root, req)

            self.assertFalse(lock_state["held"])
            self.assertEqual(lock_state["runner_calls"], 1)
            self.assertTrue(lock_state["validate_checked"])
            self.assertEqual(res["state"], "WORKER_PROPOSAL_READY")
            self.assertEqual(res["correction_count"], 2)
            self.assertEqual(res["final_worker_run_id"], expected_run_id)

    def test_worker_index_rejected_if_not_two(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=1)
            req = {"role": "worker", "request_id": "req-2"}

            @contextmanager
            def mock_context(r: Path, q: dict):
                yield ctx

            runner_called = False

            def mock_runner(*args, **kwargs):
                nonlocal runner_called
                runner_called = True
                return root / "not_reached"

            with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                 patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner):

                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_two_once(root, req)
                self.assertIn("worker_index must be 2", str(exc_info.exception))

            self.assertFalse(runner_called)
            expected_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-2"
            self.assertFalse(expected_dir.exists())

    def test_incomplete_history_rejected_pre_runner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}

            @contextmanager
            def mock_context(r: Path, q: dict):
                yield ctx

            runner_called = False

            def mock_runner(*args, **kwargs):
                nonlocal runner_called
                runner_called = True
                return root / "not_reached"

            incomplete_history = {"complete": False}

            with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                 patch("scripts.two_site_quota_resume_worker_two_once.check_state"), \
                 patch("scripts.two_site_quota_resume_worker_two_once.ref_snapshot", return_value={}), \
                 patch("scripts.two_site_quota_resume_worker_two_once.complete_worker_two_history", return_value=incomplete_history), \
                 patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner):

                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_two_once(root, req)
                self.assertIn("worker-2 history must be complete", str(exc_info.exception))

            self.assertFalse(runner_called)

    def test_duplicate_run_id_rejected_pre_runner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root, worker_index=2)
            req = {"role": "worker", "request_id": "req-2"}

            dup_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-2"
            dup_dir.mkdir(parents=True, exist_ok=True)

            @contextmanager
            def mock_context(r: Path, q: dict):
                yield ctx

            runner_called = False

            def mock_runner(*args, **kwargs):
                nonlocal runner_called
                runner_called = True
                return root / "not_reached"

            with patch("scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context", side_effect=mock_context), \
                 patch("scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner):

                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_two_once(root, req)
                self.assertIn("worker run ID already exists", str(exc_info.exception))

            self.assertFalse(runner_called)


if __name__ == "__main__":
    unittest.main()
