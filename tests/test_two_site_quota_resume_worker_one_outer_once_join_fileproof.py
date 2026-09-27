#!/usr/bin/env python3
"""Materialized outer once-join proof for Worker-1 resume lineage.

This is an isolated proof, NOT common Broker request admission,
real Worker/Reviewer Provider, real candidate sandbox,
Broker/Supervisor live hook, replay authority or full C5d completion.
Design note: observational quota, no replay authority.

Discloses synthetic seams:
- common dispatch admission, terminal quota receipt, resume artifact derivation
- once correction continuation context, ref_snapshot, check_state, runner_run
- post correction continuation context, ref/state check
- pipeline candidate test sandbox and provider session
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from contextlib import contextmanager
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
    import scripts.two_site_quota_resume_worker_one_once as once_mod
    from tests.test_two_site_proposal_pipeline import (
        make_runner,
        passed_receipt,
    )
    import tests.test_two_site_quota_resume_worker_one_outer_test_gate_fileproof as outer_gate_mod
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
    import scripts.two_site_quota_resume_worker_one_once as once_mod  # type: ignore[no-redef]
    from test_two_site_proposal_pipeline import (  # type: ignore[no-redef]
        make_runner,
        passed_receipt,
    )
    import test_two_site_quota_resume_worker_one_outer_test_gate_fileproof as outer_gate_mod  # type: ignore[no-redef]


class TestTwoSiteQuotaResumeWorkerOneOuterOnceJoinFileproof(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _run_positive_proof(self, origin: str) -> None:
        helper = outer_gate_mod.TestTwoSiteQuotaResumeWorkerOneOuterTestGateFileproof()
        helper.root = self.root
        env = helper._setup_env(origin)

        origin_lower = origin.lower()
        old_prefix = f"proof-gate-{origin_lower}"
        new_prefix = f"{origin_lower}-outer-once-join"

        ctx = env["ctx"]
        ctx["original_run_prefix"] = old_prefix
        ctx["continuation_run_prefix"] = new_prefix

        admitted = env["admitted"]
        admitted["run_prefix"] = new_prefix

        w0_run_id = ctx["artifacts"]["final_worker_run_id"]
        w0_sha = ctx["artifacts"]["proposal_sha256"]

        ctx["artifacts"]["proposal_path"] = (
            f".local/two-site-refactor/proposals/{w0_run_id}/result.json"
        )
        ctx["evidence"]["feedback_path"] = f"feedback-{origin_lower}.json"

        fb_sha = env["fb_sha"]
        task_path = env["task_path"]
        initial_refs = ctx["initial_refs"]

        @contextmanager
        def fake_worker_correction_continuation_context(
            r: Path, req: dict[str, Any]
        ):
            with pipeline_lock(r):
                yield ctx

        real_worker_runner, _ = make_runner(self.root, [{"content": "# v2\n"}])
        worker_provider_entries: list[str] = []

        def fake_once_runner(
            root: Path,
            t_path: Path,
            role: str,
            run_id: str,
            timeout: int,
            proposal: Path | None = None,
            feedback: Path | None = None,
            test_evidence: Path | None = None,
            cli: Path | None = None,
            pre_provider_check: Any = None,
        ) -> Path:
            if not callable(pre_provider_check):
                raise AssertionError("pre_provider_check must be callable")
            with self.assertRaises(ProposalError):
                with pipeline_lock(root):
                    pass
            pre_provider_check()
            worker_provider_entries.append(run_id)
            return real_worker_runner(
                root,
                t_path,
                role,
                run_id,
                timeout,
                proposal=proposal,
                feedback=feedback,
                test_evidence=test_evidence,
                cli=cli,
            )

        with self.assertRaises(AssertionError):
            fake_once_runner(
                self.root,
                task_path,
                "worker",
                "omitted-run",
                300,
                pre_provider_check=None,
            )
        self.assertEqual(worker_provider_entries, [])

        def fake_run_candidate_tests(
            root: Path,
            task: dict[str, Any],
            worker_res: dict[str, Any],
            worker_sha: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return passed_receipt(worker_sha)

        reviewer_lock_checked = [False]
        verifier_calls: list[str] = []

        def fake_run_provider_session(
            root: Path,
            t_path: Path,
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
            reviewer_lock_checked[0] = True
            if role == "verifier":
                verifier_calls.append(base_run_id)
            runner, _ = make_runner(root, [{"status": "VERIFIED"}])
            run_dir = runner(
                root,
                t_path,
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
            return_value=(admitted, task_path, None),
        ), patch.object(
            dispatch_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            dispatch_mod,
            "derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ), patch.object(
            once_mod,
            "worker_correction_continuation_context",
            side_effect=fake_worker_correction_continuation_context,
        ), patch.object(
            once_mod,
            "validate_quota_continuation_request",
            return_value=(admitted, task_path, None),
        ), patch.object(
            once_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            once_mod,
            "derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ), patch.object(
            once_mod,
            "load_worker_correction_evidence",
            return_value=ctx["evidence"],
        ), patch.object(
            once_mod,
            "ref_snapshot",
            return_value=initial_refs,
        ), patch.object(
            once_mod,
            "check_state",
            return_value=None,
        ), patch.object(
            once_mod,
            "runner_run",
            side_effect=fake_once_runner,
        ), patch.object(
            post_mod,
            "worker_correction_continuation_context",
            side_effect=fake_worker_correction_continuation_context,
        ), patch.object(
            post_mod,
            "validate_quota_continuation_request",
            return_value=(admitted, task_path, None),
        ), patch.object(
            post_mod,
            "read_terminal_quota_record",
            return_value=env["original_terminal_receipt"],
        ), patch.object(
            post_mod,
            "derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ), patch.object(
            post_mod,
            "load_worker_correction_evidence",
            return_value=ctx["evidence"],
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

        self.assertTrue(reviewer_lock_checked[0])

        with pipeline_lock(self.root):
            pass

        self.assertEqual(final_state["state"], "READY_FOR_CODEX")
        self.assertEqual(final_state["task_id"], admitted["task_id"])
        self.assertEqual(final_state["source_head"], admitted["source_head"])
        self.assertEqual(
            final_state["semantic_task_fingerprint"],
            admitted["semantic_task_fingerprint"],
        )
        self.assertEqual(final_state["correction_count"], 1)
        self.assertEqual(final_state["blockers"], [])

        expected_w1_id = f"{new_prefix}-worker-1"
        self.assertEqual(final_state["final_worker_run_id"], expected_w1_id)
        self.assertEqual(
            final_state["proposal_path"],
            f".local/two-site-refactor/proposals/{expected_w1_id}/result.json",
        )

        w1_dir = self.root / ".local/two-site-refactor/proposals" / expected_w1_id
        self.assertTrue(w1_dir.is_dir())
        w1_result_bytes = (w1_dir / "result.json").read_bytes()
        new_w1_sha = hashlib.sha256(w1_result_bytes).hexdigest()
        self.assertEqual(final_state["proposal_sha256"], new_w1_sha)

        w1_status = json.loads((w1_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(w1_status["result_sha256"], new_w1_sha)
        self.assertEqual(w1_status["input_proposal_sha256"], w0_sha)
        self.assertEqual(w1_status["input_feedback_sha256"], fb_sha)

        old_w1_id = f"{old_prefix}-worker-1"
        self.assertNotEqual(expected_w1_id, old_w1_id)
        old_w1_dir = self.root / ".local/two-site-refactor/proposals" / old_w1_id
        self.assertTrue(old_w1_dir.is_dir())
        old_w1_res = (old_w1_dir / "result.json").read_bytes()
        old_w1_sha = hashlib.sha256(old_w1_res).hexdigest()
        self.assertNotEqual(new_w1_sha, old_w1_sha)

        expected_reviewer_id = f"{new_prefix}-reviewer-1"
        self.assertEqual(
            final_state["final_reviewer_run_id"], expected_reviewer_id
        )
        self.assertEqual(
            final_state["reviewer_path"],
            f".local/two-site-refactor/proposals/{expected_reviewer_id}/result.json",
        )
        self.assertEqual(final_state["pre_review_test_state"], "PASSED")

        rcpt_path = self.root / final_state["pre_review_test_path"]
        self.assertTrue(rcpt_path.is_file())
        rcpt_bytes = rcpt_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(rcpt_bytes).hexdigest(),
            final_state["pre_review_test_sha256"],
        )
        rcpt_data = json.loads(rcpt_bytes.decode("utf-8"))
        self.assertEqual(rcpt_data["state"], "PASSED")
        self.assertEqual(rcpt_data["proposal_sha256"], new_w1_sha)

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
        self.assertEqual(rev_status["input_proposal_sha256"], new_w1_sha)
        self.assertEqual(rev_status["input_worker_run_id"], expected_w1_id)

        self.assertEqual(worker_provider_entries, [expected_w1_id])
        self.assertEqual(verifier_calls, [expected_reviewer_id])

        rev0_dir = (
            self.root
            / ".local/two-site-refactor/proposals"
            / f"{new_prefix}-reviewer-0"
        )
        self.assertFalse(rev0_dir.exists())

        w2_dir = (
            self.root
            / ".local/two-site-refactor/proposals"
            / f"{new_prefix}-worker-2"
        )
        self.assertFalse(w2_dir.exists())

        fb2_path = (
            self.root
            / ".local/two-site-refactor/pipeline"
            / new_prefix
            / "feedback-correction-2.json"
        )
        self.assertFalse(fb2_path.exists())

    def test_worker_one_outer_once_join_bootstrap(self) -> None:
        self._run_positive_proof("BOOTSTRAP")

    def test_worker_one_outer_once_join_test_failed(self) -> None:
        self._run_positive_proof("TEST_FAILED")


if __name__ == "__main__":
    unittest.main()
