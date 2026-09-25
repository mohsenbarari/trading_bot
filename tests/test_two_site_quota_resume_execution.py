"""Tests for two_site_quota_resume_execution verifier preflight continuation."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    import scripts.two_site_quota_resume_execution as target_mod
    from scripts.two_site_quota_resume_execution import prepare_verifier_continuation
except ImportError:
    from two_site_proposal_pipeline import ProposalError
    import two_site_quota_resume_execution as target_mod
    from two_site_quota_resume_execution import prepare_verifier_continuation


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

    def test_reject_non_verifier_role(self) -> None:
        bad_req = dict(self.valid_req, role="worker")
        with self.assertRaises(ProposalError):
            prepare_verifier_continuation(self.root, bad_req)
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
            provider_fn = MagicMock()
            with patch("scripts.two_site_provider_broker.validate_request", provider_fn):
                ctx = prepare_verifier_continuation(self.root, self.valid_req)
                provider_fn.assert_not_called()
                self.assertIsInstance(ctx, dict)


if __name__ == "__main__":
    unittest.main()
