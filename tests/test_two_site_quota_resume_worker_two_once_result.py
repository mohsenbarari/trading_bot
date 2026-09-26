"""Tests for post-Provider result processing, second quota, and no-replay matrix in worker-2 continuation."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError, QuotaBlockError
    from scripts.two_site_proposal_runner import WORKER_MODEL
    from scripts.two_site_quota_resume_worker_two_once import execute_worker_two_once
except ImportError:
    from two_site_proposal_pipeline import ProposalError, QuotaBlockError  # type: ignore[no-redef]
    from two_site_proposal_runner import WORKER_MODEL  # type: ignore[no-redef]
    from two_site_quota_resume_worker_two_once import execute_worker_two_once  # type: ignore[no-redef]


def _build_fixture_context(root: Path) -> dict[str, Any]:
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
        "worker_index": 2,
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


def _quota_status_payload(run_id: str, head: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "task_id": "task-100",
        "role": "worker",
        "head": head,
        "model": WORKER_MODEL,
        "state": "BLOCKED",
        "conversation_id": "conv-work-2",
        "quota_evidence": {
            "kind": "QUOTA_RESET_UNKNOWN",
            "provider": "gemini",
            "model": WORKER_MODEL,
            "conversation_id": "conv-work-2",
            "reset_at_utc": None,
        },
    }


@contextmanager
def _patch_base(ctx: dict[str, Any], check_state_fn: Any = None):
    @contextmanager
    def mock_context(r: Path, q: dict[str, Any]):
        yield ctx

    fake_history = {
        "complete": True,
        "conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
        "ordered_conversation_ids": ["conv-work-0", "conv-rev-0", "conv-work-1", "conv-rev-1"],
    }

    patches = [
        patch(
            "scripts.two_site_quota_resume_worker_two_once.worker_correction_continuation_context",
            side_effect=mock_context,
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.validate_quota_continuation_request",
            return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None),
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.read_terminal_quota_record",
            return_value=ctx["original_terminal_receipt"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.load_worker_correction_evidence",
            return_value=ctx["evidence"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.check_state",
            side_effect=check_state_fn,
        ),
        patch("scripts.two_site_quota_resume_worker_two_once.ref_snapshot", return_value={}),
        patch(
            "scripts.two_site_quota_resume_worker_two_once.complete_worker_two_history",
            return_value=fake_history,
        ),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        yield


class TestTwoSiteQuotaResumeWorkerTwoOnceResult(unittest.TestCase):
    def test_bound_second_quota_extraction(self) -> None:
        """(1) runner_run once, pre_provider_check invoked, writes BLOCKED status.json, raises QuotaBlockError."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-2"}
            run_id = "cont-req-200-worker-2"
            run_dir = root / f".local/two-site-refactor/proposals/{run_id}"

            calls, cb_called = [], False
            raw_bytes = b""

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                nonlocal cb_called, raw_bytes
                calls.append(rid)
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                cb_called = True
                run_dir.mkdir(parents=True, exist_ok=True)
                raw_bytes = json.dumps(_quota_status_payload(rid, ctx["source_head"])).encode("utf-8")
                (run_dir / "status.json").write_bytes(raw_bytes)
                raise ProposalError("quota block simulated")

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(QuotaBlockError) as exc_info:
                    execute_worker_two_once(root, req)

            self.assertTrue(cb_called)
            self.assertEqual(calls, [run_id])
            self.assertEqual(
                exc_info.exception.quota_attempt,
                {
                    "role": "worker",
                    "run_id": run_id,
                    "status_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                },
            )
            self.assertEqual(exc_info.exception.quota_evidence["provider"], "gemini")

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info2:
                    execute_worker_two_once(root, req)
                self.assertIn("already exists", str(exc_info2.exception))
            self.assertEqual(len(calls), 1)

    def test_invalid_or_linked_quota_not_upgraded(self) -> None:
        """(2) wrong role/head/model/state/quota or symlink/hardlink status is NOT upgraded; propagates original failure."""
        scenarios: list[tuple[str, Any]] = [
            ("wrong_role", {"role": "verifier"}),
            ("wrong_head", {"head": "1" * 40}),
            ("wrong_model", {"model": "wrong-model"}),
            ("wrong_state", {"state": "RUNNING"}),
            ("malformed_json", "NOT_JSON"),
            ("malformed_evidence", {"quota_evidence": {"invalid": True}}),
            ("symlink_status", "SYMLINK"),
            ("hardlink_status", "HARDLINK"),
        ]
        for case_name, override in scenarios:
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                ctx = _build_fixture_context(root)
                req = {"role": "worker", "request_id": "req-2"}
                run_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-2"
                calls = []

                def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                    calls.append(rid)
                    if pre_provider_check:
                        pre_provider_check()
                    run_dir.mkdir(parents=True, exist_ok=True)
                    st_p = run_dir / "status.json"
                    payload = _quota_status_payload(rid, ctx["source_head"])
                    if override == "NOT_JSON":
                        st_p.write_bytes(b"invalid{json")
                    elif override == "SYMLINK":
                        t = root / "ext.json"
                        t.write_bytes(json.dumps(payload).encode("utf-8"))
                        st_p.symlink_to(t)
                    elif override == "HARDLINK":
                        t = root / "ext_hard.json"
                        t.write_bytes(json.dumps(payload).encode("utf-8"))
                        os.link(t, st_p)
                    else:
                        payload.update(override)
                        st_p.write_bytes(json.dumps(payload).encode("utf-8"))
                    raise ProposalError("runner failure")

                with _patch_base(ctx), patch(
                    "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
                ):
                    with self.assertRaises(ProposalError) as exc_info:
                        execute_worker_two_once(root, req)
                    self.assertNotIsInstance(exc_info.exception, QuotaBlockError)
                    self.assertIn("runner failure", str(exc_info.exception))
                self.assertEqual(len(calls), 1)

    def test_ambiguous_runner_failure_no_retry(self) -> None:
        """(3) ambiguous runner failure without status never retries; existing dir rejects next call."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-2"}
            run_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-2"
            calls = []

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                calls.append(rid)
                if pre_provider_check:
                    pre_provider_check()
                run_dir.mkdir(parents=True, exist_ok=True)
                raise ProposalError("ambiguous crash")

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_two_once(root, req)
                self.assertNotIsInstance(exc_info.exception, QuotaBlockError)
                self.assertIn("ambiguous crash", str(exc_info.exception))
            self.assertEqual(len(calls), 1)

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info2:
                    execute_worker_two_once(root, req)
                self.assertIn("already exists", str(exc_info2.exception))
            self.assertEqual(len(calls), 1)

    def test_returned_run_dir_invalid_rejections(self) -> None:
        """(4) returned non-Path/symlink/wrong run_dir refuses."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-2"}
            expected_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-2"

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", return_value="not-a-path"
            ):
                with self.assertRaises(ProposalError) as exc:
                    execute_worker_two_once(root, req)
                self.assertIn("run_dir must be Path", str(exc.exception))

            real_dir = root / "real_dir"
            real_dir.mkdir(parents=True, exist_ok=True)

            def mock_symlink(*a: Any, **kw: Any) -> Path:
                expected_dir.parent.mkdir(parents=True, exist_ok=True)
                expected_dir.symlink_to(real_dir)
                return expected_dir

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", side_effect=mock_symlink
            ):
                with self.assertRaises(ProposalError) as exc:
                    execute_worker_two_once(root, req)
                self.assertIn("run_dir cannot be a symlink", str(exc.exception))

            expected_dir.unlink()
            wrong_dir = root / "wrong_dir"
            wrong_dir.mkdir(parents=True, exist_ok=True)
            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_two_once.runner_run", return_value=wrong_dir
            ):
                with self.assertRaises(ProposalError) as exc:
                    execute_worker_two_once(root, req)
                self.assertIn("run_dir mismatch", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
