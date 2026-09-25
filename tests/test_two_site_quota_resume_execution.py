"""Tests for two_site_quota_resume_execution verifier preflight continuation."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    import scripts.two_site_proposal_pipeline as pipeline_mod
    import scripts.two_site_proposal_runner as runner_mod
    import scripts.two_site_provider_broker as broker_mod
    import scripts.two_site_quota_resume_execution as target_mod
    from scripts.two_site_quota_resume_execution import (
        prepare_verifier_continuation,
        verifier_continuation_context,
    )
except ImportError:
    from two_site_proposal_pipeline import ProposalError
    import two_site_proposal_pipeline as pipeline_mod
    import two_site_proposal_runner as runner_mod
    import two_site_provider_broker as broker_mod
    import two_site_quota_resume_execution as target_mod
    from two_site_quota_resume_execution import (
        prepare_verifier_continuation,
        verifier_continuation_context,
    )


class TestTwoSiteQuotaResumeExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("/dummy/root")
        self.valid_req = {
            "role": "verifier",
            "run_prefix": "run_cont",
            "original_request_id": "0123456789abcdef0123456789abcdef",
        }
        self.artifacts = {
            "original_run_prefix": "run_orig",
            "final_worker_run_id": "run_orig-worker-0",
            "proposal_path": ".local/two-site-refactor/proposals/run_orig-worker-0/result.json",
            "proposal_sha256": "1" * 64,
            "pre_review_test_path": ".local/two-site-refactor/pipeline/run_orig/pre-review-tests-0.json",
            "pre_review_test_sha256": "2" * 64,
            "source_head": "1a3fe8984d52601ad13b881a21cc3069cb5a4b01",
        }

    @contextmanager
    def _default_patches(self):
        with (
            patch.object(target_mod, "pipeline_lock") as mock_lock,
            patch.object(target_mod, "validate_quota_continuation_request") as mock_adm,
            patch.object(target_mod, "read_terminal_quota_record") as mock_read_rec,
            patch.object(target_mod, "_derive_verifier_resume_artifacts") as mock_art,
            patch.object(target_mod, "_load_verifier_pre_review_receipt") as mock_test,
            patch.object(target_mod, "load_prior_worker_chain") as mock_worker,
            patch.object(target_mod, "load_task") as mock_task,
            patch.object(target_mod, "ref_snapshot") as mock_refs,
            patch.object(target_mod, "check_state") as mock_chk,
        ):
            mock_lock.return_value.__enter__.return_value = Path("/dummy/pipeline")
            mock_adm.return_value = (dict(self.valid_req), Path("task.json"), {})
            mock_read_rec.return_value = {"request": {"id": self.valid_req["original_request_id"]}}
            mock_art.return_value = dict(self.artifacts)
            mock_test.return_value = {"state": "PASSED"}
            mock_worker.return_value = {"result_sha": "1" * 64}
            mock_task.return_value = ({"task_id": "T1"}, {"file": "content"})
            mock_refs.return_value = {"HEAD": "1a3fe89"}
            yield {
                "lock": mock_lock,
                "adm": mock_adm,
                "read_rec": mock_read_rec,
                "art": mock_art,
                "test": mock_test,
                "worker": mock_worker,
                "task": mock_task,
                "refs": mock_refs,
                "chk": mock_chk,
            }

    def test_valid_composition(self) -> None:
        with self._default_patches() as mocks:
            ctx = prepare_verifier_continuation(self.root, self.valid_req)
            mocks["lock"].assert_called_once_with(self.root)
            self.assertEqual(mocks["adm"].call_count, 2)
            self.assertEqual(ctx["continuation_run_prefix"], "run_cont")
            self.assertEqual(ctx["original_run_prefix"], "run_orig")
            self.assertEqual(ctx["final_worker_run_id"], "run_orig-worker-0")
            self.assertEqual(ctx["proposal_sha256"], "1" * 64)
            self.assertEqual(ctx["pre_review_test_sha256"], "2" * 64)
            self.assertEqual(ctx["source_head"], "1a3fe8984d52601ad13b881a21cc3069cb5a4b01")
            self.assertEqual(ctx["original_receipt"]["request"]["id"], self.valid_req["original_request_id"])
            self.assertNotEqual(ctx.get("status"), "READY_FOR_CODEX")
            self.assertNotIn("READY_FOR_CODEX", ctx.values())

    def test_verifier_continuation_context_holds_lock_during_body(self) -> None:
        with self._default_patches() as mocks:
            lock_cm = mocks["lock"].return_value
            body_observed_lock = False

            self.assertEqual(lock_cm.__enter__.call_count, 0)
            self.assertEqual(lock_cm.__exit__.call_count, 0)

            with verifier_continuation_context(self.root, self.valid_req) as ctx:
                body_observed_lock = True
                mocks["lock"].assert_called_once_with(self.root)
                self.assertEqual(lock_cm.__enter__.call_count, 1)
                self.assertEqual(lock_cm.__exit__.call_count, 0)
                self.assertEqual(ctx["continuation_run_prefix"], "run_cont")
                self.assertEqual(ctx["original_run_prefix"], "run_orig")
                self.assertEqual(ctx["proposal_sha256"], "1" * 64)
                self.assertEqual(ctx["source_head"], "1a3fe8984d52601ad13b881a21cc3069cb5a4b01")

            self.assertTrue(body_observed_lock)
            self.assertEqual(lock_cm.__enter__.call_count, 1)
            self.assertEqual(lock_cm.__exit__.call_count, 1)

    def test_verifier_continuation_context_releases_lock_on_exception(self) -> None:
        with self._default_patches() as mocks:
            lock_cm = mocks["lock"].return_value
            self.assertEqual(lock_cm.__enter__.call_count, 0)
            self.assertEqual(lock_cm.__exit__.call_count, 0)

            with self.assertRaises(RuntimeError) as cm:
                with verifier_continuation_context(self.root, self.valid_req) as ctx:
                    self.assertEqual(lock_cm.__enter__.call_count, 1)
                    self.assertEqual(lock_cm.__exit__.call_count, 0)
                    self.assertEqual(ctx["continuation_run_prefix"], "run_cont")
                    raise RuntimeError("caller body exception")

            self.assertEqual(str(cm.exception), "caller body exception")
            self.assertEqual(lock_cm.__enter__.call_count, 1)
            self.assertEqual(lock_cm.__exit__.call_count, 1)

    def test_verifier_continuation_context_rejects_before_lock(self) -> None:
        bad_req = dict(self.valid_req, role="worker")
        with (
            patch.object(
                target_mod,
                "pipeline_lock",
                side_effect=AssertionError("pipeline_lock should not be called"),
            ) as mock_lock,
            patch.object(
                target_mod,
                "validate_quota_continuation_request",
                side_effect=AssertionError("admission should not be called"),
            ) as mock_adm,
        ):
            with self.assertRaises(ProposalError):
                with verifier_continuation_context("invalid_root", self.valid_req):
                    pass
            with self.assertRaises(ProposalError):
                with verifier_continuation_context(self.root, "invalid_req"):
                    pass
            with self.assertRaises(ProposalError):
                with verifier_continuation_context(self.root, bad_req):
                    pass
            mock_lock.assert_not_called()
            mock_adm.assert_not_called()

    def test_reject_non_verifier_role(self) -> None:
        bad_req = dict(self.valid_req, role="worker")
        with (
            patch.object(
                target_mod,
                "pipeline_lock",
                side_effect=AssertionError("pipeline_lock should not be called"),
            ) as mock_lock,
            patch.object(
                target_mod,
                "validate_quota_continuation_request",
                side_effect=AssertionError("admission should not be called"),
            ) as mock_adm,
        ):
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, bad_req)
            mock_lock.assert_not_called()
            mock_adm.assert_not_called()
        with self._default_patches() as mocks:
            mocks["adm"].return_value = (dict(self.valid_req, role="reviewer"), Path("task.json"), {})
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

    def test_wrong_receipt_worker_or_test_failure(self) -> None:
        with self._default_patches() as mocks:
            mocks["read_rec"].side_effect = ProposalError("receipt error")
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

        with self._default_patches() as mocks:
            mocks["test"].side_effect = ProposalError("test not passed")
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

        with self._default_patches() as mocks:
            mocks["worker"].side_effect = ProposalError("worker chain broken")
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

    def test_drift_across_admission(self) -> None:
        with self._default_patches() as mocks:
            first = (dict(self.valid_req), Path("task.json"), {})
            drifted = (dict(self.valid_req, run_prefix="drifted"), Path("task.json"), {})
            mocks["adm"].side_effect = [first, drifted]
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

        with self._default_patches() as mocks:
            first = (dict(self.valid_req), Path("task.json"), {})
            drift_path = (dict(self.valid_req), Path("task2.json"), {})
            mocks["adm"].side_effect = [first, drift_path]
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

        with self._default_patches() as mocks:
            mocks["task"].side_effect = [
                ({"task_id": "T1"}, {"f": "1"}),
                ({"task_id": "T1_drift"}, {"f": "1"}),
            ]
            with self.assertRaises(ProposalError):
                prepare_verifier_continuation(self.root, self.valid_req)

    def test_proves_no_provider_call(self) -> None:
        with self._default_patches():
            pipeline_entrypoint = (
                "scripts.two_site_proposal_pipeline.run_provider_session"
                if "scripts.two_site_proposal_pipeline" in sys.modules
                else f"{pipeline_mod.__name__}.run_provider_session"
            )
            runner_entrypoint = (
                "scripts.two_site_proposal_runner.run"
                if "scripts.two_site_proposal_runner" in sys.modules
                else f"{runner_mod.__name__}.run"
            )
            with (
                patch(
                    pipeline_entrypoint,
                    side_effect=AssertionError("Provider entrypoint run_provider_session called"),
                ) as mock_pipeline_run,
                patch(
                    runner_entrypoint,
                    side_effect=AssertionError("Provider entrypoint runner.run called"),
                ) as mock_runner_run,
            ):
                if hasattr(broker_mod, "execute_pipeline"):
                    broker_entrypoint = (
                        "scripts.two_site_provider_broker.execute_pipeline"
                        if "scripts.two_site_provider_broker" in sys.modules
                        else f"{broker_mod.__name__}.execute_pipeline"
                    )
                    with patch(
                        broker_entrypoint,
                        side_effect=AssertionError("Broker execute_pipeline called"),
                    ) as mock_broker_exec:
                        ctx = prepare_verifier_continuation(self.root, self.valid_req)
                        mock_pipeline_run.assert_not_called()
                        mock_runner_run.assert_not_called()
                        mock_broker_exec.assert_not_called()
                        self.assertIsInstance(ctx, dict)
                else:
                    ctx = prepare_verifier_continuation(self.root, self.valid_req)
                    mock_pipeline_run.assert_not_called()
                    mock_runner_run.assert_not_called()
                    self.assertIsInstance(ctx, dict)

    def test_static_ast_no_provider_or_broker_execution_imports_or_calls(self) -> None:
        try:
            source = inspect.getsource(target_mod)
        except (OSError, TypeError):
            source = Path(target_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_names: set[str] = set()
        called_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    name = alias.asname or alias.name
                    imported_names.add(name)
                    if "runner" in mod and alias.name == "run":
                        imported_names.add("runner_run")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)

        forbidden_direct = {"run_provider_session", "runner_run"}
        self.assertTrue(
            forbidden_direct.isdisjoint(imported_names),
            f"Preflight module imports forbidden symbols: {forbidden_direct & imported_names}",
        )
        self.assertTrue(
            forbidden_direct.isdisjoint(called_names),
            f"Preflight module calls forbidden symbols: {forbidden_direct & called_names}",
        )

        forbidden_broker_ops = {
            name
            for name in (imported_names | called_names)
            if "submit" in name.lower() or "execute" in name.lower()
        }
        self.assertEqual(
            forbidden_broker_ops,
            set(),
            f"Preflight module imports or calls Broker submit/execute operations: {forbidden_broker_ops}",
        )


if __name__ == "__main__":
    unittest.main()
