"""Tests for post-Provider result processing, second quota, and no-replay matrix in worker-1 continuation."""

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
    from scripts.two_site_quota_continuation_admission import ContinuationAdmissionError
    from scripts.two_site_quota_resume_worker_one_once import execute_worker_one_once
except ImportError:
    from two_site_proposal_pipeline import ProposalError, QuotaBlockError  # type: ignore[no-redef]
    from two_site_proposal_runner import WORKER_MODEL  # type: ignore[no-redef]
    from two_site_quota_continuation_admission import ContinuationAdmissionError  # type: ignore[no-redef]
    from two_site_quota_resume_worker_one_once import execute_worker_one_once  # type: ignore[no-redef]


def _build_fixture_context(root: Path) -> dict[str, Any]:
    orig_prefix, cont_prefix = "orig-req-100", "cont-req-200"
    rev_dir = root / f".local/two-site-refactor/proposals/{orig_prefix}-verifier-0"
    rev_dir.mkdir(parents=True, exist_ok=True)
    (rev_dir / "status.json").write_bytes(b'{"conversation_id": "conv-rev-0"}')

    worker_info = {
        "result": {
            "status": "PROPOSAL_READY",
            "changes": [{"path": "a.py", "original_sha256": "0" * 64, "content": "v1", "replacements": []}],
        },
        "status": {"conversation_id": "conv-work-0", "role": "worker"},
        "result_sha": "1" * 64,
        "result_text": '{"status": "PROPOSAL_READY"}',
        "conversation_id": "conv-work-0",
    }
    artifacts = {
        "worker_index": 1,
        "attempted_run_id": f"{orig_prefix}-worker-1",
        "original_run_prefix": orig_prefix,
        "final_worker_run_id": f"{orig_prefix}-worker-0",
        "proposal_sha256": "1" * 64,
        "proposal_path": f".local/two-site-refactor/proposals/{orig_prefix}-worker-0/result.json",
        "reviewer_path": f".local/two-site-refactor/proposals/{orig_prefix}-verifier-0/result.json",
        "semantic_task_fingerprint": "task-fp-100",
    }
    evidence = {
        "worker_info": worker_info,
        "reviewer_status": {"conversation_id": "conv-rev-0"},
        "feedback_path": f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-1.json",
        "feedback_sha": "f" * 64,
    }
    terminal_receipt = {"receipt_id": orig_prefix}
    return {
        "continuation_run_prefix": cont_prefix,
        "original_run_prefix": orig_prefix,
        "source_head": "0" * 40,
        "initial_refs": {},
        "original_receipt": terminal_receipt,
        "original_terminal_receipt": terminal_receipt,
        "admitted_request": {
            "run_prefix": cont_prefix,
            "original_request_id": orig_prefix,
            "timeout": 30,
            "role": "worker",
        },
        "admitted_task_path": root / "task.json",
        "artifacts": artifacts,
        "evidence": evidence,
        "task": {"task_id": "task-100"},
        "sources": {"a.py": "v0"},
    }


def _quota_status_payload(run_id: str, head: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "role": "worker",
        "head": head,
        "model": WORKER_MODEL,
        "state": "BLOCKED",
        "conversation_id": "conv-work-1",
        "quota_evidence": {
            "kind": "QUOTA_RESET_UNKNOWN",
            "provider": "gemini",
            "model": WORKER_MODEL,
            "conversation_id": "conv-work-1",
            "reset_at_utc": None,
        },
    }


@contextmanager
def _patch_base(ctx: dict[str, Any], check_state_fn: Any = None):
    @contextmanager
    def mock_context(r: Path, q: dict[str, Any]):
        yield ctx

    patches = [
        patch(
            "scripts.two_site_quota_resume_worker_one_once.worker_correction_continuation_context",
            side_effect=mock_context,
        ),
        patch(
            "scripts.two_site_quota_resume_worker_one_once.validate_quota_continuation_request",
            return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None),
        ),
        patch(
            "scripts.two_site_quota_resume_worker_one_once.read_terminal_quota_record",
            return_value=ctx["original_terminal_receipt"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_one_once.derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_one_once.load_worker_correction_evidence",
            return_value=ctx["evidence"],
        ),
        patch(
            "scripts.two_site_quota_resume_worker_one_once.check_state",
            side_effect=check_state_fn,
        ),
        patch("scripts.two_site_quota_resume_worker_one_once.ref_snapshot", return_value={}),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        yield


class TestTwoSiteQuotaResumeWorkerOneOnceResult(unittest.TestCase):
    def test_bound_second_quota_extraction(self) -> None:
        """(1) runner_run once, pre_provider_check invoked, writes BLOCKED status.json, raises QuotaBlockError."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-1"}
            run_id = "cont-req-200-worker-1"
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
                "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(QuotaBlockError) as exc_info:
                    execute_worker_one_once(root, req)

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
                "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info2:
                    execute_worker_one_once(root, req)
                self.assertIn("already exists", str(exc_info2.exception))
            self.assertEqual(len(calls), 1)

    def test_invalid_or_linked_quota_not_upgraded(self) -> None:
        """(2) wrong role/head/model/linked status or malformed quota is NOT upgraded; propagates original failure."""
        scenarios: list[tuple[str, Any]] = [\
            ("wrong_role", {"role": "verifier"}),\
            ("wrong_head", {"head": "1" * 40}),\
            ("wrong_model", {"model": "wrong-model"}),\
            ("wrong_state", {"state": "RUNNING"}),\
            ("malformed_json", "NOT_JSON"),\
            ("malformed_evidence", {"quota_evidence": {"invalid": True}}),\
            ("symlink_status", "SYMLINK"),\
            ("hardlink_status", "HARDLINK"),\
        ]
        for case_name, override in scenarios:
            with self.subTest(case=case_name), tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                ctx = _build_fixture_context(root)
                req = {"role": "worker", "request_id": "req-1"}
                run_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-1"
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
                    "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
                ):
                    with self.assertRaises(ProposalError) as exc_info:
                        execute_worker_one_once(root, req)
                    self.assertNotIsInstance(exc_info.exception, QuotaBlockError)
                    self.assertIn("runner failure", str(exc_info.exception))
                self.assertEqual(len(calls), 1)

    def test_ambiguous_runner_failure_no_retry(self) -> None:
        """(3) ambiguous runner failure without status never retries; existing dir rejects next call."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-1"}
            run_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-1"
            calls = []

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                calls.append(rid)
                if pre_provider_check:
                    pre_provider_check()
                run_dir.mkdir(parents=True, exist_ok=True)
                raise ProposalError("ambiguous crash")

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info:
                    execute_worker_one_once(root, req)
                self.assertNotIsInstance(exc_info.exception, QuotaBlockError)
                self.assertIn("ambiguous crash", str(exc_info.exception))
            self.assertEqual(len(calls), 1)

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ProposalError) as exc_info2:
                    execute_worker_one_once(root, req)
                self.assertIn("already exists", str(exc_info2.exception))
            self.assertEqual(len(calls), 1)

    def test_returned_run_dir_invalid_rejections(self) -> None:
        """(4) returned symlink/mismatched/non-Path run_dir rejects."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-1"}
            expected_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-1"

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_one_once.runner_run", return_value="not-a-path"
            ):
                with self.assertRaises(ProposalError) as exc:
                    execute_worker_one_once(root, req)
                self.assertIn("run_dir must be Path", str(exc.exception))

            real_dir = root / "real_dir"
            real_dir.mkdir(parents=True, exist_ok=True)

            def mock_symlink(*a: Any, **kw: Any) -> Path:
                expected_dir.parent.mkdir(parents=True, exist_ok=True)
                expected_dir.symlink_to(real_dir)
                return expected_dir

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_symlink
            ):
                with self.assertRaises(ProposalError) as exc:
                    execute_worker_one_once(root, req)
                self.assertIn("run_dir cannot be a symlink", str(exc.exception))

            expected_dir.unlink()
            wrong_dir = root / "wrong_dir"
            wrong_dir.mkdir(parents=True, exist_ok=True)
            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_one_once.runner_run", return_value=wrong_dir
            ):
                with self.assertRaises(ProposalError) as exc:
                    execute_worker_one_once(root, req)
                self.assertIn("run_dir mismatch", str(exc.exception))

    def test_post_run_validation_failure_reused_conv_and_check_state_drift(self) -> None:
        """(5) validate_run failure, reused conv signal and post-run check_state drift prevent WORKER_PROPOSAL_READY."""
        cases = [\
            ("validate_failure", "validate_err", None, "worker did not complete PROPOSAL_READY"),\
            ("reused_conv", "reused_conv_err", None, "reused conversation_id in cont-req-200-worker-1"),\
            ("drift_post_runner", "ok", 4, "drift detected post runner"),\
            ("drift_post_validate", "ok", 5, "drift detected post validate"),\
        ]
        for case_label, v_mode, drift_idx, err_msg in cases:
            with self.subTest(case=case_label), tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                ctx = _build_fixture_context(root)
                req = {"role": "worker", "request_id": "req-1"}
                run_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-1"
                calls = []

                def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                    calls.append(rid)
                    if pre_provider_check:
                        pre_provider_check()
                    run_dir.mkdir(parents=True, exist_ok=True)
                    return run_dir

                def mock_validate(*a: Any, **kw: Any) -> Any:
                    if v_mode == "validate_err":
                        raise ProposalError("worker did not complete PROPOSAL_READY")
                    if v_mode == "reused_conv_err":
                        raise ProposalError("reused conversation_id in cont-req-200-worker-1")
                    return (
                        {
                            "status": "PROPOSAL_READY",
                            "changes": [
                                {
                                    "path": "a.py",
                                    "original_sha256": "0" * 64,
                                    "content": "v2",
                                    "replacements": [],
                                }
                            ],
                        },
                        {"conversation_id": "conv-work-1"},
                        "2" * 64,
                        "{}",
                    )

                state_calls = 0

                def mock_check_state(*a: Any, **kw: Any) -> None:
                    nonlocal state_calls
                    state_calls += 1
                    if drift_idx and state_calls == drift_idx:
                        raise ProposalError(err_msg)

                with _patch_base(ctx, check_state_fn=mock_check_state), patch(
                    "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
                ), patch(
                    "scripts.two_site_quota_resume_worker_one_once.validate_run", side_effect=mock_validate
                ):
                    with self.assertRaises(ProposalError) as exc_info:
                        execute_worker_one_once(root, req)
                    self.assertIn(err_msg, str(exc_info.exception))
                self.assertEqual(len(calls), 1)

    def test_unchanged_proposal_sha_or_changes_reject(self) -> None:
        """(6) unchanged proposal SHA or changed_paths identical to prior result reject."""
        prior_changes = [{"path": "a.py", "original_sha256": "0" * 64, "content": "v1", "replacements": []}]
        new_changes = [{"path": "a.py", "original_sha256": "0" * 64, "content": "v2", "replacements": []}]
        scenarios = [\
            ("unchanged_sha", "1" * 64, new_changes, "correction proposal SHA must differ"),\
            ("unchanged_changes", "2" * 64, prior_changes, "correction proposal changes must differ"),\
        ]
        for case_name, res_sha, res_changes, err_msg in scenarios:
            with self.subTest(scenario=case_name), tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                ctx = _build_fixture_context(root)
                req = {"role": "worker", "request_id": "req-1"}
                run_dir = root / ".local/two-site-refactor/proposals/cont-req-200-worker-1"
                calls = []

                def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                    calls.append(rid)
                    if pre_provider_check:
                        pre_provider_check()
                    run_dir.mkdir(parents=True, exist_ok=True)
                    return run_dir

                def mock_validate(*a: Any, **kw: Any) -> Any:
                    return {"status": "PROPOSAL_READY", "changes": res_changes}, {"conversation_id": "conv-work-1"}, res_sha, "{}"

                with _patch_base(ctx), patch(
                    "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
                ), patch(
                    "scripts.two_site_quota_resume_worker_one_once.validate_run", side_effect=mock_validate
                ):
                    with self.assertRaises(ProposalError) as exc_info:
                        execute_worker_one_once(root, req)
                    self.assertIn(err_msg, str(exc_info.exception))
                self.assertEqual(len(calls), 1)

    def test_direct_stop_from_pre_provider_check_propagates(self) -> None:
        """(7) direct ContinuationAdmissionError from pre_provider_check propagates with no simulated Popen."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            ctx = _build_fixture_context(root)
            req = {"role": "worker", "request_id": "req-1"}
            simulated_popen, calls = False, []

            def mock_runner(r: Any, t_path: Any, role: Any, rid: str, timeout: Any, *, pre_provider_check: Any = None, **kw: Any) -> Path:
                nonlocal simulated_popen
                calls.append(rid)
                self.assertIsNotNone(pre_provider_check)
                pre_provider_check()
                simulated_popen = True
                return root / "not_reached"

            with _patch_base(ctx), patch(
                "scripts.two_site_quota_resume_worker_one_once.validate_quota_continuation_request",
                side_effect=ContinuationAdmissionError("STOP sentinel detected"),
            ), patch(
                "scripts.two_site_quota_resume_worker_one_once.runner_run", side_effect=mock_runner
            ):
                with self.assertRaises(ContinuationAdmissionError) as exc_info:
                    execute_worker_one_once(root, req)
                self.assertIn("STOP sentinel detected", str(exc_info.exception))
            self.assertFalse(simulated_popen)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
