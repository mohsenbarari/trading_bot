"""Unit tests for scripts.two_site_quota_resume_role_dispatch."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from scripts.two_site_proposal_pipeline import ProposalError, QuotaBlockError
from scripts.two_site_quota_resume_role_dispatch import (
    dispatch_quota_continuation_once,
)


class TestTwoSiteQuotaResumeRoleDispatch(unittest.TestCase):
    """Test quota continuation role dispatch behavior."""

    def setUp(self) -> None:
        self.root = Path("/fake/root")
        self.cli = Path("/fake/cli")
        self.base_request = {
            "schema_version": "1.0",
            "request_type": "QUOTA_CONTINUATION",
            "original_request_id": "req-orig-123",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
            "role": "verifier",
        }
        self.worker_zero_intermediate = {
            "state": "WORKER_PROPOSAL_READY",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "final_worker_run_id": "run-worker-0",
            "proposal_sha256": "a" * 64,
            "changed_paths": ["scripts/foo.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-0/result.json",
            "correction_count": 0,
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
        }
        self.worker_zero_final_uncorrected = {
            "state": "READY_FOR_CODEX",
            "final_reviewer_run_id": "run-rev-0",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
            "correction_count": 0,
            "final_worker_run_id": "run-worker-0",
            "proposal_sha256": "a" * 64,
            "changed_paths": ["scripts/foo.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-0/result.json",
        }
        self.worker_zero_final_corrected = {
            "state": "READY_FOR_CODEX",
            "final_reviewer_run_id": "run-rev-1",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
            "correction_count": 1,
            "final_worker_run_id": "run-worker-1",
            "proposal_sha256": "b" * 64,
            "changed_paths": ["scripts/bar.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-1/result.json",
        }
        self.worker_one_intermediate = {
            "state": "WORKER_PROPOSAL_READY",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "final_worker_run_id": "run-worker-1",
            "proposal_sha256": "b" * 64,
            "changed_paths": ["scripts/bar.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-1/result.json",
            "correction_count": 1,
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
        }
        self.worker_one_final_uncorrected = {
            "state": "READY_FOR_CODEX",
            "final_reviewer_run_id": "run-rev-1",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
            "correction_count": 1,
            "final_worker_run_id": "run-worker-1",
            "proposal_sha256": "b" * 64,
            "changed_paths": ["scripts/bar.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-1/result.json",
        }
        self.worker_one_final_corrected = {
            "state": "READY_FOR_CODEX",
            "final_reviewer_run_id": "run-rev-2",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
            "correction_count": 2,
            "final_worker_run_id": "run-worker-2",
            "proposal_sha256": "c" * 64,
            "changed_paths": ["scripts/baz.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-2/result.json",
        }
        self.worker_two_intermediate = {
            "state": "WORKER_PROPOSAL_READY",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "final_worker_run_id": "run-worker-2",
            "proposal_sha256": "c" * 64,
            "changed_paths": ["scripts/baz.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-2/result.json",
            "correction_count": 2,
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
        }
        self.worker_two_final = {
            "state": "READY_FOR_CODEX",
            "final_reviewer_run_id": "run-rev-2",
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
            "correction_count": 2,
            "final_worker_run_id": "run-worker-2",
            "proposal_sha256": "c" * 64,
            "changed_paths": ["scripts/baz.py"],
            "proposal_path": ".local/two-site-refactor/proposals/run-worker-2/result.json",
        }
        self.fake_receipt = {
            "schema_version": "1.0",
            "request": {"request_id": "req-orig-123"},
            "response": {"stdout": "{}"},
        }

    def _setup_worker_zero(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
    ) -> tuple[dict, dict]:
        req = dict(self.base_request, role="worker")
        admitted = dict(req)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {
            "worker_index": 0,
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
        }
        return req, admitted

    def _setup_worker_one(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
    ) -> tuple[dict, dict]:
        req = dict(self.base_request, role="worker")
        admitted = dict(req)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {
            "worker_index": 1,
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
        }
        return req, admitted

    def _setup_worker_two(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
    ) -> tuple[dict, dict]:
        req = dict(self.base_request, role="worker")
        admitted = dict(req)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {
            "worker_index": 2,
            "task_id": "task-456",
            "source_head": "0a6a0bc1069a142065d7cd6fb1a00c5cce52b307",
            "semantic_task_fingerprint": "627f44e6bea25f3f307ce4f4c4abfcc10a88e76f38fe77a9b8391a50130ee3e8",
        }
        return req, admitted

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_verifier_once")
    @patch("scripts.two_site_quota_resume_role_dispatch._derive_verifier_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_verifier_route_success(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_verifier: MagicMock,
    ) -> None:
        admitted = dict(self.base_request)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {"correction_count": 0}
        mock_exec_verifier.return_value = {
            "state": "READY_FOR_CODEX",
            "final_reviewer_run_id": "run-rev-0",
        }

        res = dispatch_quota_continuation_once(self.root, self.base_request, self.cli)

        mock_validate.assert_called_once_with(self.root, self.base_request)
        mock_read_record.assert_called_once_with(self.root, "req-orig-123")
        mock_derive_artifacts.assert_called_once_with(admitted, self.fake_receipt)
        mock_exec_verifier.assert_called_once_with(self.root, admitted, self.cli)
        self.assertEqual(res["state"], "READY_FOR_CODEX")

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_zero_route_success(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_zero: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, admitted = self._setup_worker_zero(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_zero.return_value = self.worker_zero_intermediate
        mock_remainder.return_value = self.worker_zero_final_uncorrected

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_derive_artifacts.assert_called_once_with(admitted, self.fake_receipt)
        mock_exec_worker_zero.assert_called_once_with(self.root, admitted, self.cli)
        mock_remainder.assert_called_once_with(
            self.root, admitted, self.worker_zero_intermediate, self.cli
        )
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 0)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_zero_malformed_intermediate_proves_adapter_not_called(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_zero: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_zero(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_zero.return_value = {"state": "WORKER_PROPOSAL_READY"}

        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)
        mock_remainder.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_zero_quota_block_error_propagates_without_retry(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_zero: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_zero(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        quota_err = QuotaBlockError(quota_evidence={"reason": "RESOURCE_EXHAUSTED"})
        mock_exec_worker_zero.side_effect = quota_err

        with self.assertRaises(QuotaBlockError) as cm:
            dispatch_quota_continuation_once(self.root, req, self.cli)
        self.assertIs(cm.exception, quota_err)
        mock_remainder.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_zero_route_correction_count_one_success(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_zero: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, admitted = self._setup_worker_zero(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_zero.return_value = self.worker_zero_intermediate
        mock_remainder.return_value = self.worker_zero_final_corrected

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_remainder.assert_called_once_with(
            self.root, admitted, self.worker_zero_intermediate, self.cli
        )
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 1)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_one_route_success(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_one: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, admitted = self._setup_worker_one(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_one.return_value = self.worker_one_intermediate
        mock_remainder.return_value = self.worker_one_final_uncorrected

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_derive_artifacts.assert_called_once_with(admitted, self.fake_receipt)
        mock_exec_worker_one.assert_called_once_with(self.root, admitted, self.cli)
        mock_remainder.assert_called_once_with(
            self.root, admitted, self.worker_one_intermediate, self.cli
        )
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 1)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_one_route_correction_count_two_success(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_one: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, admitted = self._setup_worker_one(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_one.return_value = self.worker_one_intermediate
        mock_remainder.return_value = self.worker_one_final_corrected

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_remainder.assert_called_once_with(
            self.root, admitted, self.worker_one_intermediate, self.cli
        )
        self.assertEqual(res["state"], "READY_FOR_CODEX")
        self.assertEqual(res["correction_count"], 2)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_one_malformed_intermediate_proves_adapter_not_called(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_one: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_one(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_one.return_value = {"state": "WORKER_PROPOSAL_READY"}

        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)
        mock_remainder.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_one_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_one_bad_final_lineage_or_correction_fails(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_one: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_one(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_one.return_value = self.worker_one_intermediate

        # Mismatched lineage in final remainder
        bad_lineage_final = dict(self.worker_one_final_uncorrected, task_id="wrong-task")
        mock_remainder.return_value = bad_lineage_final
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)

        # Out-of-range final correction_count
        bad_corr_final = dict(self.worker_one_final_uncorrected, correction_count=0)
        mock_remainder.return_value = bad_corr_final
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)

        # Missing expected final state
        bad_state_final = dict(self.worker_one_final_uncorrected, state="WORKER_PROPOSAL_READY")
        mock_remainder.return_value = bad_state_final
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_two_route_success(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_two: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        manager = MagicMock()
        manager.attach_mock(mock_exec_worker_two, "exec_w2")
        manager.attach_mock(mock_remainder, "remainder")
        req, admitted = self._setup_worker_two(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_two.return_value = self.worker_two_intermediate
        mock_remainder.return_value = self.worker_two_final

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_derive_artifacts.assert_called_once_with(admitted, self.fake_receipt)
        mock_exec_worker_two.assert_called_once_with(self.root, admitted, self.cli)
        mock_remainder.assert_called_once_with(
            self.root, admitted, self.worker_two_intermediate, self.cli
        )
        self.assertEqual(
            manager.mock_calls,
            [
                unittest.mock.call.exec_w2(self.root, admitted, self.cli),
                unittest.mock.call.remainder(
                    self.root, admitted, self.worker_two_intermediate, self.cli
                ),
            ],
        )
        self.assertEqual(res, self.worker_two_final)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_two_malformed_or_mismatched_intermediate_suppresses_remainder(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_two: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_two(
            mock_validate, mock_read_record, mock_derive_artifacts
        )

        mock_exec_worker_two.return_value = "not-a-dict"
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)
        mock_remainder.assert_not_called()

        mock_exec_worker_two.return_value = {"state": "WORKER_PROPOSAL_READY"}
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)
        mock_remainder.assert_not_called()

        bad_state = dict(self.worker_two_intermediate, state="READY_FOR_CODEX")
        mock_exec_worker_two.return_value = bad_state
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)
        mock_remainder.assert_not_called()

        for bad_corr in (1, True, "2", None):
            mock_exec_worker_two.return_value = dict(
                self.worker_two_intermediate, correction_count=bad_corr
            )
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req, self.cli)
            mock_remainder.assert_not_called()

        for field in ("task_id", "source_head", "semantic_task_fingerprint"):
            mock_exec_worker_two.return_value = dict(
                self.worker_two_intermediate, **{field: "mismatch"}
            )
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req, self.cli)
            mock_remainder.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_two_bad_final_state_reviewer_correction_lineage_unchanged_fails(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_two: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_two(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        mock_exec_worker_two.return_value = self.worker_two_intermediate

        mock_remainder.return_value = "not-a-dict"
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_remainder.return_value = dict(
            self.worker_two_final, state="WORKER_PROPOSAL_READY"
        )
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req, self.cli)

        for bad_rev in ("", "   ", None, 123):
            mock_remainder.return_value = dict(
                self.worker_two_final, final_reviewer_run_id=bad_rev
            )
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req, self.cli)

        for field in ("task_id", "source_head", "semantic_task_fingerprint"):
            mock_remainder.return_value = dict(self.worker_two_final, **{field: "wrong"})
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req, self.cli)

        for bad_corr in (0, 1, 3, True, "2"):
            mock_remainder.return_value = dict(
                self.worker_two_final, correction_count=bad_corr
            )
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req, self.cli)

        for field, bad_val in (
            ("final_worker_run_id", "run-worker-other"),
            ("proposal_sha256", "d" * 64),
            ("changed_paths", ["scripts/other.py"]),
            (
                "proposal_path",
                ".local/two-site-refactor/proposals/run-worker-other/result.json",
            ),
        ):
            mock_remainder.return_value = dict(self.worker_two_final, **{field: bad_val})
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req, self.cli)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_post_result_remainder")
    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_two_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_worker_two_quota_block_error_propagates_without_retry(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_two: MagicMock,
        mock_remainder: MagicMock,
    ) -> None:
        req, _ = self._setup_worker_two(
            mock_validate, mock_read_record, mock_derive_artifacts
        )
        quota_err = QuotaBlockError(quota_evidence={"reason": "RESOURCE_EXHAUSTED"})

        mock_exec_worker_two.side_effect = quota_err
        with self.assertRaises(QuotaBlockError) as cm:
            dispatch_quota_continuation_once(self.root, req, self.cli)
        self.assertIs(cm.exception, quota_err)
        mock_remainder.assert_not_called()

        mock_exec_worker_two.side_effect = None
        mock_exec_worker_two.return_value = self.worker_two_intermediate
        mock_remainder.side_effect = quota_err
        with self.assertRaises(QuotaBlockError) as cm:
            dispatch_quota_continuation_once(self.root, req, self.cli)
        self.assertIs(cm.exception, quota_err)
        mock_remainder.assert_called_once()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_verifier_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_admission_failure_never_invokes_executor(
        self,
        mock_validate: MagicMock,
        mock_exec_verifier: MagicMock,
    ) -> None:
        mock_validate.side_effect = ProposalError("admission rejected")
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, self.base_request)
        mock_exec_verifier.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_verifier_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_receipt_failure_never_invokes_executor(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_exec_verifier: MagicMock,
    ) -> None:
        mock_validate.return_value = (dict(self.base_request), Path("t.json"), {})
        mock_read_record.side_effect = ProposalError("terminal receipt missing")
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, self.base_request)
        mock_exec_verifier.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_verifier_once")
    @patch("scripts.two_site_quota_resume_role_dispatch._derive_verifier_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_artifact_derivation_failure_never_invokes_executor(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_verifier: MagicMock,
    ) -> None:
        mock_validate.return_value = (dict(self.base_request), Path("t.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.side_effect = ProposalError("artifact derivation failed")
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, self.base_request)
        mock_exec_verifier.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_reject_bool_out_of_range_or_alias_worker_index(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_zero: MagicMock,
    ) -> None:
        req = dict(self.base_request, role="worker")
        mock_validate.return_value = (dict(req), Path("t.json"), {})
        mock_read_record.return_value = self.fake_receipt

        for bad_index in (True, False, 3, -1, "0", 0.0, None):
            mock_derive_artifacts.return_value = {"worker_index": bad_index}
            with self.assertRaises(ProposalError):
                dispatch_quota_continuation_once(self.root, req)
            mock_exec_worker_zero.assert_not_called()

    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_raw_vs_admitted_request_mismatch_rejected(
        self,
        mock_validate: MagicMock,
    ) -> None:
        raw_req = dict(self.base_request, role="worker")
        admitted = dict(self.base_request, role="verifier")
        mock_validate.return_value = (admitted, Path("t.json"), {})

        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, raw_req)

    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_unsupported_role_rejected(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
    ) -> None:
        req = dict(self.base_request, role="auditor")
        mock_validate.return_value = (dict(req), Path("t.json"), {})
        mock_read_record.return_value = self.fake_receipt

        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req)

    @patch("scripts.two_site_quota_resume_role_dispatch.execute_worker_zero_once")
    @patch("scripts.two_site_quota_resume_role_dispatch.derive_worker_resume_artifacts")
    @patch("scripts.two_site_quota_resume_role_dispatch.read_terminal_quota_record")
    @patch("scripts.two_site_quota_resume_role_dispatch.validate_quota_continuation_request")
    def test_mismatched_returned_route_rejected(
        self,
        mock_validate: MagicMock,
        mock_read_record: MagicMock,
        mock_derive_artifacts: MagicMock,
        mock_exec_worker_zero: MagicMock,
    ) -> None:
        req = dict(self.base_request, role="worker")
        mock_validate.return_value = (dict(req), Path("t.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {"worker_index": 0}
        mock_exec_worker_zero.return_value = {
            "state": "WORKER_PROPOSAL_READY",
            "correction_count": 1,
        }

        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, req)

    def test_invalid_root_or_request_types(self) -> None:
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once("/not/a/path", self.base_request)  # type: ignore[arg-type]
        with self.assertRaises(ProposalError):
            dispatch_quota_continuation_once(self.root, "not-a-dict")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
