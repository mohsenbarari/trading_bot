"""Unit tests for scripts.two_site_quota_resume_role_dispatch."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from scripts.two_site_proposal_pipeline import ProposalError
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
            "role": "verifier",
        }
        self.fake_receipt = {
            "schema_version": "1.0",
            "request": {"request_id": "req-orig-123"},
            "response": {"stdout": "{}"},
        }

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
    ) -> None:
        req = dict(self.base_request, role="worker")
        admitted = dict(req)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {"worker_index": 0}
        mock_exec_worker_zero.return_value = {
            "state": "WORKER_PROPOSAL_READY",
            "correction_count": 0,
        }

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_derive_artifacts.assert_called_once_with(admitted, self.fake_receipt)
        mock_exec_worker_zero.assert_called_once_with(self.root, admitted, self.cli)
        self.assertEqual(res["correction_count"], 0)

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
    ) -> None:
        req = dict(self.base_request, role="worker")
        admitted = dict(req)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {"worker_index": 1}
        mock_exec_worker_one.return_value = {
            "state": "WORKER_PROPOSAL_READY",
            "correction_count": 1,
        }

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_exec_worker_one.assert_called_once_with(self.root, admitted, self.cli)
        self.assertEqual(res["correction_count"], 1)

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
    ) -> None:
        req = dict(self.base_request, role="worker")
        admitted = dict(req)
        mock_validate.return_value = (admitted, Path("tasks/task.json"), {})
        mock_read_record.return_value = self.fake_receipt
        mock_derive_artifacts.return_value = {"worker_index": 2}
        mock_exec_worker_two.return_value = {
            "state": "WORKER_PROPOSAL_READY",
            "correction_count": 2,
        }

        res = dispatch_quota_continuation_once(self.root, req, self.cli)

        mock_exec_worker_two.assert_called_once_with(self.root, admitted, self.cli)
        self.assertEqual(res["correction_count"], 2)

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
