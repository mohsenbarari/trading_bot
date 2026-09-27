#!/usr/bin/env python3
"""Hermetic unit tests for Opus pipeline fallback model binding and dispatch."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import scripts.two_site_proposal_pipeline as pipeline
    from scripts import two_site_proposal_runner as runner
    from scripts.two_site_proposal_pipeline import (
        ForbiddenToolAttemptError,
        ProposalError,
        RetryableProviderSessionError,
        expected_bound_role_model,
        run_provider_session,
    )
    from scripts.two_site_proposal_runner import WORKER_MODEL
    from scripts.two_site_worker_model_routing import (
        OPUS_EFFORT,
        OPUS_MODEL,
        WorkerRoutingPolicy,
    )
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipeline  # type: ignore[no-redef]
    import two_site_proposal_runner as runner  # type: ignore[no-redef]
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ForbiddenToolAttemptError,
        ProposalError,
        RetryableProviderSessionError,
        expected_bound_role_model,
        run_provider_session,
    )
    from two_site_proposal_runner import WORKER_MODEL  # type: ignore[no-redef]
    from two_site_worker_model_routing import (  # type: ignore[no-redef]
        OPUS_EFFORT,
        OPUS_MODEL,
        WorkerRoutingPolicy,
    )

from tests.test_two_site_opus_runner_fallback import (
    ENABLED_POLICY,
    TASK_ID_GEMINI,
    TASK_ID_OPUS,
    _write_evidence,
)


class TestExpectedBoundRoleModel(unittest.TestCase):
    """Hermetic tests for expected_bound_role_model fallback validation and gates."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ctrl_id = "ctrl-exec-001"
        self.failed_id = "run-opus-failed-001"
        self.fallback_run_id = f"{self.failed_id}-gf"
        self.head = "0655392de0594432d41a73641e5134376b4c9e43"
        self.task = {
            "task_id": TASK_ID_OPUS,
            "goal": "Pipeline fallback model binding test",
            "context_files": [],
            "allowed_files": ["file.py"],
            "requirements": ["1. Test."],
            "test_selectors": ["tests.test_two_site_opus_pipeline_fallback"],
        }
        self.sources = {
            "file.py": {
                "sha256": "135529731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
                "content": "code_content\n",
            }
        }
        self.failed_dir = self.root / ".local/two-site-refactor/proposals" / self.failed_id
        self.policy_patcher = mock.patch.object(
            pipeline, "load_worker_routing_policy", return_value=ENABLED_POLICY
        )
        self.policy_patcher.start()
        self._restore_baseline()

    def tearDown(self) -> None:
        self.policy_patcher.stop()
        self.temp_dir.cleanup()

    def _restore_baseline(self) -> dict:
        _write_evidence(
            self.failed_dir,
            self.failed_id,
            self.task,
            self.sources,
            self.head,
            self.ctrl_id,
        )
        canonical_receipt = runner._load_bound_opus_fallback_receipt(
            self.root,
            self.failed_id,
            self.task,
            self.sources,
            self.head,
            self.ctrl_id,
        )
        self.baseline_status = {
            "run_id": self.fallback_run_id,
            "fallback_from_run_id": self.failed_id,
            "fallback_receipt": canonical_receipt,
            "model": runner.WORKER_MODEL,
            "controller_run_id": self.ctrl_id,
        }
        return self.baseline_status

    def test_authentic_bound_case_and_normal_opus_status(self) -> None:
        """Valid fallback binds WORKER_MODEL; normal status without fallback keys remains OPUS_MODEL."""
        status = copy.deepcopy(self.baseline_status)
        bound_model = expected_bound_role_model(
            self.root,
            "worker",
            self.fallback_run_id,
            self.head,
            self.task,
            self.sources,
            status,
        )
        self.assertEqual(bound_model, runner.WORKER_MODEL)

        normal_status = {"run_id": "run-opus-normal-001", "model": OPUS_MODEL}
        normal_model = expected_bound_role_model(
            self.root,
            "worker",
            "run-opus-normal-001",
            self.head,
            self.task,
            self.sources,
            normal_status,
        )
        self.assertEqual(normal_model, OPUS_MODEL)

    def test_asymmetric_and_null_keys_reject(self) -> None:
        """Asymmetric keys and null values in status fail closed."""
        self._restore_baseline()
        st_missing_rcpt = copy.deepcopy(self.baseline_status)
        del st_missing_rcpt["fallback_receipt"]
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_missing_rcpt
            )
        self.assertIn("asymmetric fallback keys in status", str(cm.exception))

        self._restore_baseline()
        st_missing_id = copy.deepcopy(self.baseline_status)
        del st_missing_id["fallback_from_run_id"]
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_missing_id
            )
        self.assertIn("asymmetric fallback keys in status", str(cm.exception))

        for null_key in ["fallback_from_run_id", "fallback_receipt"]:
            self._restore_baseline()
            st_null = copy.deepcopy(self.baseline_status)
            st_null[null_key] = None
            with self.assertRaises(ProposalError) as cm:
                expected_bound_role_model(
                    self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_null
                )
            self.assertIn("fallback key values cannot be null", str(cm.exception))

    def test_foreign_controller_wrong_role_wrong_route_wrong_model_reject(self) -> None:
        """Foreign controller, verifier role, wrong route, and mismatched model fail closed."""
        self._restore_baseline()
        st_foreign = copy.deepcopy(self.baseline_status)
        st_foreign["controller_run_id"] = "ctrl-foreign-999"
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_foreign
            )
        self.assertIn("failed run controller run ID does not match current controller", str(cm.exception))

        self._restore_baseline()
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "verifier", self.fallback_run_id, self.head, self.task, self.sources, self.baseline_status
            )
        self.assertIn("fallback requires role 'worker'", str(cm.exception))

        self._restore_baseline()
        gemini_task = dict(self.task, task_id=TASK_ID_GEMINI)
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, gemini_task, self.sources, self.baseline_status
            )
        self.assertIn("fallback requires selected route model to match policy model exactly", str(cm.exception))

        self._restore_baseline()
        st_wrong_model = copy.deepcopy(self.baseline_status)
        st_wrong_model["model"] = OPUS_MODEL
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_wrong_model
            )
        self.assertIn("status model mismatch", str(cm.exception))

    def test_wrong_run_ids_and_mutated_prior_evidence_or_receipt_reject(self) -> None:
        """Run ID mismatches, double-fallback ID, tampered prior stream, and modified receipt reject."""
        self._restore_baseline()
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", "run-mismatched-id", self.head, self.task, self.sources, self.baseline_status
            )
        self.assertIn("run_id mismatch for fallback", str(cm.exception))

        self._restore_baseline()
        st_bad_st_id = copy.deepcopy(self.baseline_status)
        st_bad_st_id["run_id"] = "run-wrong-st-id"
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_bad_st_id
            )
        self.assertIn("status run_id mismatch", str(cm.exception))

        self._restore_baseline()
        st_gf_loop = copy.deepcopy(self.baseline_status)
        st_gf_loop["fallback_from_run_id"] = f"{self.failed_id}-gf"
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", f"{self.failed_id}-gf-gf", self.head, self.task, self.sources, st_gf_loop
            )
        self.assertIn("invalid fallback_from_run_id", str(cm.exception))

        self._restore_baseline()
        (self.failed_dir / "stream.jsonl").write_bytes(b'{"corrupt": true}\n')
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, self.baseline_status
            )
        self.assertIn("invalid Opus failure receipt", str(cm.exception))

        self._restore_baseline()
        st_tampered_rcpt = copy.deepcopy(self.baseline_status)
        st_tampered_rcpt["fallback_receipt"] = dict(
            st_tampered_rcpt["fallback_receipt"], stream_sha256="0" * 64
        )
        with self.assertRaises(ProposalError) as cm:
            expected_bound_role_model(
                self.root, "worker", self.fallback_run_id, self.head, self.task, self.sources, st_tampered_rcpt
            )
        self.assertIn("status fallback_receipt does not match canonical receipt", str(cm.exception))


class TestOpusPipelineDispatch(unittest.TestCase):
    """Hermetic unit tests for run_provider_session Opus fallback dispatch and retry boundaries."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.task_path = self.root / "task.json"
        self.task = {
            "task_id": TASK_ID_OPUS,
            "goal": "Dispatch test",
            "context_files": [],
            "allowed_files": ["file.py"],
            "requirements": ["1. Test."],
            "test_selectors": ["tests.test_two_site_opus_pipeline_fallback"],
        }
        self.task_path.write_text(json.dumps(self.task), encoding="utf-8")
        self.feedback = self.root / "feedback.json"
        self.feedback.write_text('{"findings": []}', encoding="utf-8")
        self.proposal = self.root / "proposal.json"
        self.proposal.write_text('{"changes": []}', encoding="utf-8")
        self.cli = self.root / "mock_cli"
        self.cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.cli.chmod(0o755)
        self.evidence = self.root / "evidence.json"
        self.evidence.write_text('{"evidence": []}', encoding="utf-8")
        self.head = "0655392de0594432d41a73641e5134376b4c9e43"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_eligible_opus_failure_triggers_gf_fallback(self) -> None:
        """Eligible ForbiddenToolAttemptError triggers exactly one -gf fallback with matching kwargs."""
        base_run_id = "run-dispatch-worker-0"
        fallback_run_id = f"{base_run_id}-gf"
        dummy_dir = self.root / ".local/two-site-refactor/proposals" / fallback_run_id
        calls: list[dict] = []

        def fake_runner_run(root, task_path, role, run_id, timeout, **kwargs):
            calls.append({"role": role, "run_id": run_id, "timeout": timeout, "kwargs": dict(kwargs)})
            if len(calls) == 1:
                raise ForbiddenToolAttemptError("Opus attempted native tool finish")
            return dummy_dir

        state_check = mock.MagicMock()
        with (
            mock.patch.object(pipeline, "runner_run", side_effect=fake_runner_run),
            mock.patch.object(pipeline, "load_worker_routing_policy", return_value=ENABLED_POLICY),
            mock.patch.object(pipeline, "load_task", return_value=(self.task, {})),
        ):
            actual_id, actual_dir = run_provider_session(
                self.root,
                self.task_path,
                "worker",
                base_run_id,
                300,
                state_check,
                feedback=self.feedback,
                proposal=self.proposal,
                cli=self.cli,
                test_evidence=self.evidence,
                expected_head=self.head,
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(actual_id, fallback_run_id)
        self.assertEqual(actual_dir, dummy_dir)

        expected_shared_kwargs = {
            "feedback": self.feedback,
            "proposal": self.proposal,
            "cli": self.cli,
            "test_evidence": self.evidence,
        }
        self.assertEqual(calls[0]["role"], "worker")
        self.assertEqual(calls[0]["run_id"], base_run_id)
        self.assertEqual(calls[0]["timeout"], 300)
        self.assertEqual(calls[0]["kwargs"], expected_shared_kwargs)
        self.assertNotIn("fallback_from_run_id", calls[0]["kwargs"])

        self.assertEqual(calls[1]["role"], "worker")
        self.assertEqual(calls[1]["run_id"], fallback_run_id)
        self.assertEqual(calls[1]["timeout"], 300)
        expected_fallback_kwargs = dict(expected_shared_kwargs, fallback_from_run_id=base_run_id)
        self.assertEqual(calls[1]["kwargs"], expected_fallback_kwargs)
        self.assertEqual(state_check.call_count, 2)

    def test_ineligible_typed_errors_propagate_after_one_attempt(self) -> None:
        """Ineligible verifier role, disabled policy, and Gemini bucket propagate after 1 call."""
        disabled_policy = WorkerRoutingPolicy(
            schema_version="1.0",
            enabled=False,
            percent=22,
            model=OPUS_MODEL,
            effort=OPUS_EFFORT,
        )
        cases = [
            ("verifier_role", "verifier", ENABLED_POLICY, TASK_ID_OPUS),
            ("disabled_policy", "worker", disabled_policy, TASK_ID_OPUS),
            ("gemini_bucket", "worker", ENABLED_POLICY, TASK_ID_GEMINI),
        ]
        for case_name, role, policy, task_id in cases:
            with self.subTest(case=case_name):
                calls: list[str] = []

                def fake_runner_run(root, task_path, r, run_id, timeout, **kwargs):
                    calls.append(run_id)
                    raise ForbiddenToolAttemptError("Opus forbidden tool attempt")

                state_check = mock.MagicMock()
                case_task = dict(self.task, task_id=task_id)
                with (
                    mock.patch.object(pipeline, "runner_run", side_effect=fake_runner_run),
                    mock.patch.object(pipeline, "load_worker_routing_policy", return_value=policy),
                    mock.patch.object(pipeline, "load_task", return_value=(case_task, {})),
                ):
                    with self.assertRaises(ForbiddenToolAttemptError):
                        run_provider_session(
                            self.root,
                            self.task_path,
                            role,
                            f"run-{case_name}-001",
                            300,
                            state_check,
                            feedback=self.feedback,
                            proposal=self.proposal,
                            cli=self.cli,
                            test_evidence=self.evidence,
                            expected_head=self.head,
                        )
                self.assertEqual(len(calls), 1)

    def test_generic_retryable_session_error_remains_bounded_and_never_fallback(self) -> None:
        """Generic RetryableProviderSessionError exhausts bounded retries and never adds fallback_from_run_id."""
        calls: list[dict] = []

        def fake_runner_run(root, task_path, role, run_id, timeout, **kwargs):
            calls.append({"run_id": run_id, "kwargs": dict(kwargs)})
            raise RetryableProviderSessionError("transient session error")

        state_check = mock.MagicMock()
        base_run_id = "run-generic-retry-001"
        with (
            mock.patch.object(pipeline, "runner_run", side_effect=fake_runner_run),
            mock.patch.object(pipeline, "load_worker_routing_policy", return_value=ENABLED_POLICY),
            mock.patch.object(pipeline, "load_task", return_value=(self.task, {})),
        ):
            with self.assertRaises(ProposalError) as cm:
                run_provider_session(
                    self.root,
                    self.task_path,
                    "worker",
                    base_run_id,
                    300,
                    state_check,
                    feedback=self.feedback,
                    proposal=self.proposal,
                    cli=self.cli,
                    test_evidence=self.evidence,
                    expected_head=self.head,
                )
            self.assertIn("provider session retry exhausted for", str(cm.exception))
            self.assertIn("after 3 attempts", str(cm.exception))

        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [c["run_id"] for c in calls],
            [base_run_id, f"{base_run_id}-retry-1", f"{base_run_id}-retry-2"],
        )
        for call in calls:
            self.assertNotIn("fallback_from_run_id", call["kwargs"])
            self.assertEqual(call["kwargs"]["feedback"], self.feedback)
            self.assertEqual(call["kwargs"]["proposal"], self.proposal)
            self.assertEqual(call["kwargs"]["cli"], self.cli)
            self.assertEqual(call["kwargs"]["test_evidence"], self.evidence)

    def test_generic_retryable_session_error_succeeds_on_retry_without_fallback(self) -> None:
        """Transient RetryableProviderSessionError succeeds on bounded retry-1 without fallback keys."""
        calls: list[dict] = []
        base_run_id = "run-retry-ok-001"
        dummy_dir = self.root / ".local/two-site-refactor/proposals" / f"{base_run_id}-retry-1"

        def fake_runner_run(root, task_path, role, run_id, timeout, **kwargs):
            calls.append({"run_id": run_id, "kwargs": dict(kwargs)})
            if len(calls) == 1:
                raise RetryableProviderSessionError("transient drop on 1")
            return dummy_dir

        state_check = mock.MagicMock()
        with (
            mock.patch.object(pipeline, "runner_run", side_effect=fake_runner_run),
            mock.patch.object(pipeline, "load_worker_routing_policy", return_value=ENABLED_POLICY),
            mock.patch.object(pipeline, "load_task", return_value=(self.task, {})),
        ):
            actual_id, actual_dir = run_provider_session(
                self.root,
                self.task_path,
                "worker",
                base_run_id,
                300,
                state_check,
                feedback=self.feedback,
                proposal=self.proposal,
                cli=self.cli,
                test_evidence=self.evidence,
                expected_head=self.head,
            )

        self.assertEqual(actual_id, f"{base_run_id}-retry-1")
        self.assertEqual(actual_dir, dummy_dir)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertNotIn("fallback_from_run_id", call["kwargs"])


if __name__ == "__main__":
    unittest.main()
