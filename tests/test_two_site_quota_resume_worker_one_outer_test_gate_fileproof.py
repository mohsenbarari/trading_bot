#!/usr/bin/env python3
"""Materialized outer test-gate proof for Worker-1 resume lineage.

This is an isolated proof, NOT common Broker request admission,
real Worker/Reviewer Provider, real candidate sandbox,
Broker/Supervisor live hook, replay authority or full C5d completion.
Design note: observational quota, no replay authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

try:
    import scripts.two_site_proposal_pipeline as pipeline_mod
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        pipeline_lock,
    )
    import scripts.two_site_quota_resume_post_worker_one as post_mod
    import scripts.two_site_quota_resume_role_dispatch as dispatch_mod
    from scripts.two_site_quota_resume_role_dispatch import (
        dispatch_quota_continuation_once,
    )
except ImportError:
    import two_site_proposal_pipeline as pipeline_mod  # type: ignore[no-redef]
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        pipeline_lock,
    )
    import two_site_quota_resume_post_worker_one as post_mod  # type: ignore[no-redef]
    import two_site_quota_resume_role_dispatch as dispatch_mod  # type: ignore[no-redef]
    from two_site_quota_resume_role_dispatch import (  # type: ignore[no-redef]
        dispatch_quota_continuation_once,
    )

try:
    from tests.test_two_site_proposal_pipeline import (
        HEAD,
        make_runner,
        passed_receipt,
    )
    from tests.test_two_site_quota_resume_worker_one_fileproof import (
        TestTwoSiteQuotaResumeWorkerOneFileproof,
    )
except ImportError:
    from test_two_site_proposal_pipeline import (  # type: ignore[no-redef]
        HEAD,
        make_runner,
        passed_receipt,
    )
    from test_two_site_quota_resume_worker_one_fileproof import (  # type: ignore[no-redef]
        TestTwoSiteQuotaResumeWorkerOneFileproof,
    )


class TestTwoSiteQuotaResumeWorkerOneOuterTestGateFileproof(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _setup_env(self, origin: str) -> dict[str, Any]:
        helper = TestTwoSiteQuotaResumeWorkerOneFileproof()
        helper.root = self.root
        ctx, returned_state, w1_status_path, w1_result_path, fb_sha, sig = (
            helper._setup_test_gate_scenario(origin)
        )
        task_path = self.root / "task.json"
        prefix = ctx["continuation_run_prefix"]
        admitted = {
            "role": "worker",
            "original_request_id": f"orig-{origin.lower()}-req",
            "task_id": returned_state["task_id"],
            "source_head": returned_state["source_head"],
            "semantic_task_fingerprint": returned_state["semantic_task_fingerprint"],
            "timeout": 300,
            "run_prefix": prefix,
        }
        original_terminal_receipt = {
            "terminal_quota": f"receipt-{origin.lower()}",
            "status": "TERMINAL_RECORDED",
            "request_id": f"orig-{origin.lower()}-req",
        }
        initial_refs: dict[str, str | None] = {
            "refs/heads/main": "0123456789abcdef0123456789abcdef01234567"
        }
        ctx["admitted_request"] = admitted
        ctx["admitted_task_path"] = task_path
        ctx["original_terminal_receipt"] = original_terminal_receipt
        ctx["initial_refs"] = initial_refs
        ctx["artifacts"]["task_id"] = returned_state["task_id"]
        ctx["artifacts"]["source_head"] = returned_state["source_head"]

        request = {
            "role": "worker",
            "original_request_id": f"orig-{origin.lower()}-req",
            "task_id": returned_state["task_id"],
        }
        return {
            "ctx": ctx,
            "returned_state": returned_state,
            "w1_status_path": w1_status_path,
            "w1_result_path": w1_result_path,
            "fb_sha": fb_sha,
            "sig": sig,
            "admitted": admitted,
            "task_path": task_path,
            "original_terminal_receipt": original_terminal_receipt,
            "request": request,
            "prefix": prefix,
        }

    def _run_positive_proof(self, origin: str) -> None:
        env = self._setup_env(origin)
        lock_checked = [False]
        verifier_calls: list[str] = []
        worker_calls: list[str] = []

        @contextmanager
        def fake_worker_correction_continuation_context(
            r: Path, req: dict[str, Any]
        ):
            with pipeline_lock(r):
                yield env["ctx"]

        def fake_run_candidate_tests(
            root: Path,
            task: dict[str, Any],
            worker_res: dict[str, Any],
            worker_sha: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return passed_receipt(worker_sha)

        def fake_run_provider_session(
            root: Path,
            task_path: Path,
            role: str,
            base_run_id: str,
            timeout: int,
            state_check: Any,
            feedback: Path | None = None,
            proposal: Path | None = None,
            cli: Path | None = None,
            test_evidence: Path | None = None,
            expected_head: str | None = None,
        ) -> tuple[str, Path]:
            state_check()
            with self.assertRaises(ProposalError):
                with pipeline_lock(root):
                    pass
            lock_checked[0] = True
            if role == "verifier":
                verifier_calls.append(base_run_id)
            else:
                worker_calls.append(base_run_id)
            runner, _ = make_runner(root, [{"status": "VERIFIED"}])
            run_dir = runner(
                root,
                task_path,
                role,
                base_run_id,
                timeout,
                feedback=feedback,
                proposal=proposal,
                cli=cli,
                test_evidence=test_evidence,
            )
            return base_run_id, run_dir

        with patch.object(
            dispatch_mod,
            "validate_quota_continuation_request",
            return_value=(env["admitted"], env["task_path"], None),
        ), patch.object(
            dispatch_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            dispatch_mod,
            "derive_worker_resume_artifacts",
            return_value=env["ctx"]["artifacts"],
        ), patch.object(
            dispatch_mod,
            "execute_worker_one_once",
            return_value=copy.deepcopy(env["returned_state"]),
        ), patch.object(
            post_mod,
            "worker_correction_continuation_context",
            side_effect=fake_worker_correction_continuation_context,
        ), patch.object(
            post_mod,
            "validate_quota_continuation_request",
            return_value=(env["admitted"], env["task_path"], None),
        ), patch.object(
            post_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            post_mod,
            "derive_worker_resume_artifacts",
            return_value=env["ctx"]["artifacts"],
        ), patch.object(
            post_mod,
            "load_worker_correction_evidence",
            return_value=env["ctx"]["evidence"],
        ), patch.object(
            post_mod,
            "check_state",
            return_value=None,
        ), patch.object(
            pipeline_mod,
            "check_state",
            return_value=None,
        ), patch.object(
            pipeline_mod,
            "run_candidate_tests",
            side_effect=fake_run_candidate_tests,
        ), patch.object(
            pipeline_mod,
            "run_provider_session",
            side_effect=fake_run_provider_session,
        ):
            final_state = dispatch_quota_continuation_once(
                self.root, env["request"]
            )

        self.assertTrue(lock_checked[0])

        # Verify pipeline lock released afterward and reacquirable
        with pipeline_lock(self.root):
            pass

        # Exact READY_FOR_CODEX lineage and state verified
        self.assertEqual(final_state["state"], "READY_FOR_CODEX")
        self.assertEqual(final_state["task_id"], "T1")
        self.assertEqual(
            final_state["source_head"], env["returned_state"]["source_head"]
        )
        self.assertEqual(
            final_state["semantic_task_fingerprint"],
            env["returned_state"]["semantic_task_fingerprint"],
        )
        self.assertEqual(
            final_state["final_worker_run_id"],
            env["returned_state"]["final_worker_run_id"],
        )
        self.assertEqual(
            final_state["proposal_sha256"],
            env["returned_state"]["proposal_sha256"],
        )
        self.assertEqual(
            final_state["changed_paths"],
            env["returned_state"]["changed_paths"],
        )
        self.assertEqual(
            final_state["proposal_path"],
            env["returned_state"]["proposal_path"],
        )
        self.assertEqual(final_state["correction_count"], 1)
        self.assertEqual(final_state["blockers"], [])

        expected_reviewer_id = f"{env['prefix']}-reviewer-1"
        self.assertEqual(
            final_state["final_reviewer_run_id"], expected_reviewer_id
        )
        self.assertEqual(
            final_state["reviewer_path"],
            f".local/two-site-refactor/proposals/{expected_reviewer_id}/result.json",
        )
        self.assertEqual(final_state["pre_review_test_state"], "PASSED")

        # Verify PASSED pre-review receipt file and exact SHA
        rcpt_path = self.root / final_state["pre_review_test_path"]
        self.assertTrue(rcpt_path.is_file())
        rcpt_bytes = rcpt_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(rcpt_bytes).hexdigest(),
            final_state["pre_review_test_sha256"],
        )
        rcpt_data = json.loads(rcpt_bytes.decode("utf-8"))
        self.assertEqual(rcpt_data["state"], "PASSED")
        self.assertEqual(
            rcpt_data["proposal_sha256"],
            env["returned_state"]["proposal_sha256"],
        )

        # Verify reviewer invocation and reviewer status on disk
        self.assertEqual(verifier_calls, [expected_reviewer_id])
        self.assertEqual(worker_calls, [])

        reviewer_dir = (
            self.root
            / ".local/two-site-refactor/proposals"
            / expected_reviewer_id
        )
        self.assertTrue((reviewer_dir / "status.json").is_file())
        rev_status = json.loads(
            (reviewer_dir / "status.json").read_text(encoding="utf-8")
        )
        self.assertEqual(rev_status["state"], "VERIFIED")
        self.assertEqual(
            rev_status["input_proposal_sha256"],
            env["returned_state"]["proposal_sha256"],
        )
        self.assertEqual(
            rev_status["input_worker_run_id"],
            env["returned_state"]["final_worker_run_id"],
        )

        # Verify no Reviewer-0 and no Worker-2 / replay
        rev0_dir = (
            self.root
            / ".local/two-site-refactor/proposals"
            / f"{env['prefix']}-reviewer-0"
        )
        self.assertFalse(rev0_dir.exists())

        w2_dir = (
            self.root
            / ".local/two-site-refactor/proposals"
            / f"{env['prefix']}-worker-2"
        )
        self.assertFalse(w2_dir.exists())

        fb2_path = (
            self.root
            / ".local/two-site-refactor/pipeline"
            / env["prefix"]
            / "feedback-correction-2.json"
        )
        self.assertFalse(fb2_path.exists())

    def _run_negative_drift(self, origin: str, *, drift_target: str) -> None:
        env = self._setup_env(origin)
        w1_status_path = env["w1_status_path"]
        st_data = json.loads(w1_status_path.read_text())

        if drift_target == "status_sha":
            st_data["result_sha256"] = "0" * 64
        elif drift_target == "feedback_sha":
            st_data["input_feedback_sha256"] = "0" * 64
        else:
            raise ValueError(f"unknown drift_target: {drift_target}")

        w1_status_path.write_text(json.dumps(st_data))

        sandbox_called = [False]
        provider_called = [False]

        @contextmanager
        def fake_worker_correction_continuation_context(
            r: Path, req: dict[str, Any]
        ):
            with pipeline_lock(r):
                yield env["ctx"]

        def bad_candidate_tests(*args: Any, **kwargs: Any) -> Any:
            sandbox_called[0] = True
            raise AssertionError("run_candidate_tests must not be called")

        def bad_provider_session(*args: Any, **kwargs: Any) -> Any:
            provider_called[0] = True
            raise AssertionError("run_provider_session must not be called")

        with patch.object(
            dispatch_mod,
            "validate_quota_continuation_request",
            return_value=(env["admitted"], env["task_path"], None),
        ), patch.object(
            dispatch_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            dispatch_mod,
            "derive_worker_resume_artifacts",
            return_value=env["ctx"]["artifacts"],
        ), patch.object(
            dispatch_mod,
            "execute_worker_one_once",
            return_value=copy.deepcopy(env["returned_state"]),
        ), patch.object(
            post_mod,
            "worker_correction_continuation_context",
            side_effect=fake_worker_correction_continuation_context,
        ), patch.object(
            post_mod,
            "validate_quota_continuation_request",
            return_value=(env["admitted"], env["task_path"], None),
        ), patch.object(
            post_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            post_mod,
            "derive_worker_resume_artifacts",
            return_value=env["ctx"]["artifacts"],
        ), patch.object(
            post_mod,
            "load_worker_correction_evidence",
            return_value=env["ctx"]["evidence"],
        ), patch.object(
            post_mod,
            "check_state",
            return_value=None,
        ), patch.object(
            pipeline_mod,
            "check_state",
            return_value=None,
        ), patch.object(
            pipeline_mod,
            "run_candidate_tests",
            side_effect=bad_candidate_tests,
        ), patch.object(
            pipeline_mod,
            "run_provider_session",
            side_effect=bad_provider_session,
        ):
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, env["request"])

        self.assertFalse(sandbox_called[0])
        self.assertFalse(provider_called[0])

        # Verify pipeline lock released and reacquirable
        with pipeline_lock(self.root):
            pass

        # Verify no Reviewer-1 proposal directory created
        rev1_dir = (
            self.root
            / ".local/two-site-refactor/proposals"
            / f"{env['prefix']}-reviewer-1"
        )
        self.assertFalse(rev1_dir.exists())

    def test_positive_bootstrap_origin(self) -> None:
        self._run_positive_proof("BOOTSTRAP")

    def test_positive_test_failed_origin(self) -> None:
        self._run_positive_proof("TEST_FAILED")

    def test_negative_status_sha_drift_bootstrap_origin(self) -> None:
        self._run_negative_drift("BOOTSTRAP", drift_target="status_sha")

    def test_negative_status_sha_drift_test_failed_origin(self) -> None:
        self._run_negative_drift("TEST_FAILED", drift_target="status_sha")

    def test_negative_feedback_sha_drift_bootstrap_origin(self) -> None:
        self._run_negative_drift("BOOTSTRAP", drift_target="feedback_sha")

    def test_negative_feedback_sha_drift_test_failed_origin(self) -> None:
        self._run_negative_drift("TEST_FAILED", drift_target="feedback_sha")


if __name__ == "__main__":
    unittest.main()
