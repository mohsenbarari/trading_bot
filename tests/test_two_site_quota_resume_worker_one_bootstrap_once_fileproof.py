#!/usr/bin/env python3
"""Proof of worker-1 BOOTSTRAP and TEST_FAILED continuation under pipeline lock.

Discloses mocked acquisition/receipt/artifact/evidence/state/ref/runner seams and
bounded offline limits (observational quota, no replay authority; no outer, live,
replay, or C5d completion claim).
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from typing import Any
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_proposal_pipeline import (
        ProposalError,
        pipeline_lock,
        validate_run,
    )
    import scripts.two_site_quota_resume_worker_one_once as once_mod
    from scripts.two_site_quota_resume_worker_one_once import (
        execute_worker_one_once,
    )
    from tests.test_two_site_proposal_pipeline import make_runner
    import tests.test_two_site_quota_resume_worker_one_fileproof as fileproof_mod
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        pipeline_lock,
        validate_run,
    )
    import two_site_quota_resume_worker_one_once as once_mod  # type: ignore[no-redef]
    from two_site_quota_resume_worker_one_once import (  # type: ignore[no-redef]
        execute_worker_one_once,
    )
    from test_two_site_proposal_pipeline import make_runner  # type: ignore[no-redef]
    import test_two_site_quota_resume_worker_one_fileproof as fileproof_mod  # type: ignore[no-redef]


class TestTwoSiteQuotaResumeWorkerOneBootstrapOnceFileproof(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = fileproof_mod.TestTwoSiteQuotaResumeWorkerOneFileproof()
        self.helper.setUp()
        self.root = self.helper.root

    def tearDown(self) -> None:
        self.helper.tearDown()

    def _prove_origin(self, origin: str) -> None:
        ctx, _, _, fixture_w1_result_path, fb_sha, _ = (
            self.helper._setup_test_gate_scenario(origin)
        )
        self.assertEqual(ctx["artifacts"]["correction_origin"], origin)
        self.assertEqual(ctx["evidence"]["feedback"]["reviewer"], "test-gate")
        if origin == "BOOTSTRAP":
            self.assertIsNone(ctx["evidence"]["receipt"])
        else:
            self.assertEqual(ctx["evidence"]["receipt"]["state"], "FAILED")

        origin_lower = origin.lower()
        new_prefix = f"{origin_lower}-once-resume"
        ctx["original_run_prefix"] = f"proof-gate-{origin_lower}"
        ctx["continuation_run_prefix"] = new_prefix

        w0_run_id = ctx["artifacts"]["final_worker_run_id"]
        w0_sha = ctx["artifacts"]["proposal_sha256"]
        w0_dir = self.root / f".local/two-site-refactor/proposals/{w0_run_id}"
        w0_status = json.loads((w0_dir / "status.json").read_text())

        ctx["artifacts"]["proposal_path"] = (
            f".local/two-site-refactor/proposals/{w0_run_id}/result.json"
        )
        ctx["evidence"]["feedback_path"] = f"feedback-{origin_lower}.json"

        task_path = self.root / "task.json"
        ctx["admitted_task_path"] = task_path
        ctx["admitted_request"] = {
            "role": "worker",
            "timeout": 300,
            "original_request_id": f"orig-{origin_lower}-req",
        }
        ctx["original_terminal_receipt"] = {
            "terminal_quota": f"receipt-{origin_lower}"
        }
        initial_refs = {
            "refs/heads/main": "0123456789abcdef0123456789abcdef01234567"
        }

        @contextmanager
        def fake_context(root_arg: Path, req_arg: dict[str, Any]):
            with pipeline_lock(root_arg):
                yield ctx

        expected_w1_run_id = f"{new_prefix}-worker-1"
        # This response is CANONICAL against original sources, not a raw
        # one-path correction. Keep the repaired originally-new test explicitly.
        fixture_w1_result = json.loads(fixture_w1_result_path.read_text())
        response = dict(fixture_w1_result, summary=f"synthetic resumed {new_prefix}")
        expected_paths = list(ctx["task"]["allowed_files"])
        self.assertEqual([change["path"] for change in response["changes"]], expected_paths)
        self.assertEqual(response["changes"][0]["content"], "# v2\n")
        if origin == "BOOTSTRAP":
            self.assertIsNone(response["changes"][1]["original_sha256"])
        real_runner, _ = make_runner(self.root, [{"result_override": response}])
        provider_entries: list[str] = []

        def fake_runner(
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
            assert callable(pre_provider_check), (
                "pre_provider_check must be callable"
            )
            with self.assertRaises(ProposalError):
                with pipeline_lock(root):
                    pass
            pre_provider_check()
            provider_entries.append(run_id)
            return real_runner(
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
            fake_runner(
                self.root,
                task_path,
                "worker",
                "omitted-run",
                300,
                pre_provider_check=None,
            )
        self.assertEqual(provider_entries, [])

        with patch.object(
            once_mod, "worker_correction_continuation_context", fake_context
        ), patch.object(
            once_mod,
            "validate_quota_continuation_request",
            MagicMock(return_value=(ctx["admitted_request"], task_path, None)),
        ), patch.object(
            once_mod,
            "read_terminal_quota_record",
            MagicMock(return_value=ctx["original_terminal_receipt"]),
        ), patch.object(
            once_mod,
            "derive_worker_resume_artifacts",
            MagicMock(return_value=ctx["artifacts"]),
        ), patch.object(
            once_mod,
            "load_worker_correction_evidence",
            MagicMock(return_value=ctx["evidence"]),
        ), patch.object(
            once_mod, "ref_snapshot", MagicMock(return_value=initial_refs)
        ), patch.object(
            once_mod, "check_state", MagicMock(return_value=None)
        ), patch.object(
            once_mod, "runner_run", fake_runner
        ):
            result_state = execute_worker_one_once(
                self.root, {"continuation": True}
            )

        with pipeline_lock(self.root) as p_dir:
            self.assertTrue(p_dir.is_dir())

        self.assertEqual(result_state["state"], "WORKER_PROPOSAL_READY")
        self.assertEqual(
            result_state["final_worker_run_id"], expected_w1_run_id
        )

        w1_dir = (
            self.root
            / f".local/two-site-refactor/proposals/{expected_w1_run_id}"
        )
        self.assertTrue(w1_dir.is_dir())

        w1_status = json.loads((w1_dir / "status.json").read_text())
        w1_res_text = (w1_dir / "result.json").read_text()
        w1_sha = hashlib.sha256(w1_res_text.encode("utf-8")).hexdigest()

        self.assertEqual(result_state["proposal_sha256"], w1_sha)
        self.assertEqual(w1_status["result_sha256"], w1_sha)
        self.assertEqual(w1_status["input_proposal_sha256"], w0_sha)
        self.assertEqual(w1_status["input_feedback_sha256"], fb_sha)
        self.assertEqual(json.loads(w1_res_text), response)
        self.assertEqual(result_state["changed_paths"], expected_paths)
        delta = json.loads((w1_dir / "delta-result.json").read_text())
        self.assertEqual([change["path"] for change in delta["changes"]], expected_paths)
        self.assertEqual(delta["changes"][0]["content"], "# v2\n")
        if origin == "BOOTSTRAP":
            retained = delta["changes"][1]
            self.assertEqual(retained["content"], response["changes"][1]["content"])
            self.assertEqual(retained["original_sha256"],
                             hashlib.sha256(b"import main\n").hexdigest())
        self.assertNotEqual(w1_sha, hashlib.sha256(fixture_w1_result_path.read_bytes()).hexdigest())

        self.assertNotEqual(expected_w1_run_id, w0_run_id)
        orig_w1_id = f"{ctx['original_run_prefix']}-worker-1"
        self.assertNotEqual(expected_w1_run_id, orig_w1_id)
        self.assertEqual(provider_entries, [expected_w1_run_id])
        self.assertNotEqual(
            w1_status["conversation_id"], w0_status["conversation_id"]
        )

    def test_worker_one_bootstrap_once_fileproof(self) -> None:
        self._prove_origin("BOOTSTRAP")

    def test_worker_one_test_failed_once_fileproof(self) -> None:
        self._prove_origin("TEST_FAILED")


if __name__ == "__main__":
    unittest.main()
