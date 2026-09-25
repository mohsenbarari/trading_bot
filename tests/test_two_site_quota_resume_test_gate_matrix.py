"""Boundary and fault matrix tests for plan_test_receipt_gate."""
from __future__ import annotations

import copy
import unittest

from scripts.two_site_quota_resume_remainder_feedback import (
    ProposalError,
    plan_test_receipt_gate,
)

try:
    from scripts.two_site_proposal_pipeline import (
        test_failure_signature as _failure_signature,
    )
except ImportError:
    from two_site_proposal_pipeline import (
        test_failure_signature as _failure_signature,
    )


class TestTwoSiteQuotaResumeTestGateMatrix(unittest.TestCase):
    """Boundary matrix and fault isolation test suite for plan_test_receipt_gate."""

    def setUp(self) -> None:
        self.worker_sha = "a" * 64
        self.valid_receipt = {
            "state": "PASSED",
            "proposal_sha256": self.worker_sha,
            "selectors": ["tests/test_foo.py", "tests/test_bar.py"],
            "unsupported_specs": [],
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "snapshot_files": 2,
            "snapshot_bytes": 1024,
            "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all",
        }
        self.failed_receipt = {
            "state": "FAILED",
            "proposal_sha256": self.worker_sha,
            "selectors": ["tests/test_foo.py", "tests/test_bar.py"],
            "unsupported_specs": [],
            "returncode": 1,
            "stdout_tail": "",
            "stderr_tail": "AssertionError: test failure",
            "snapshot_files": 2,
            "snapshot_bytes": 1024,
            "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all",
        }

    def test_valid_passed_boundary_matrix(self) -> None:
        initial_seen = {"b" * 64}
        for count in (0, 1, 2):
            with self.subTest(count=count):
                seen_input = set(initial_seen)
                action, feedback, sig, seen_out = plan_test_receipt_gate(
                    self.valid_receipt,
                    self.worker_sha,
                    count,
                    seen_input,
                )
                self.assertEqual(action, "RUN_REVIEWER")
                self.assertIsNone(feedback)
                self.assertIsNone(sig)
                self.assertEqual(seen_out, initial_seen)
                self.assertIsNot(seen_out, seen_input)

    def test_failed_receipt_correction_and_exhaustion(self) -> None:
        for count in (0, 1):
            with self.subTest(count=count):
                action, feedback, sig, seen_out = plan_test_receipt_gate(
                    self.failed_receipt,
                    self.worker_sha,
                    count,
                    set(),
                )
                self.assertEqual(action, "CORRECTION_REQUIRED")
                self.assertNotEqual(action, "RUN_REVIEWER")
                self.assertIsInstance(feedback, dict)
                self.assertIsInstance(sig, str)
                self.assertEqual(seen_out, {sig})

        with self.assertRaises(ProposalError) as cm:
            plan_test_receipt_gate(
                self.failed_receipt,
                self.worker_sha,
                2,
                set(),
            )
        self.assertIn("correction exhaustion", str(cm.exception))

    def test_repeated_failure_signature_rejected(self) -> None:
        sig = _failure_signature(self.failed_receipt)
        with self.assertRaises(ProposalError) as cm:
            plan_test_receipt_gate(
                self.failed_receipt,
                self.worker_sha,
                0,
                {sig},
            )
        self.assertIn("repeated test failure signature", str(cm.exception))

    def test_invalid_correction_count_types_and_values(self) -> None:
        invalid_types = [True, False, 1.0, "0", None, [0]]
        for val in invalid_types:
            with self.subTest(invalid_type=val):
                with self.assertRaises(ProposalError) as cm:
                    plan_test_receipt_gate(self.valid_receipt, self.worker_sha, val, set())  # type: ignore[arg-type]
                self.assertIn("correction_count must be a built-in integer", str(cm.exception))

        invalid_values = [-1, 3, 10]
        for val in invalid_values:
            with self.subTest(invalid_value=val):
                with self.assertRaises(ProposalError) as cm:
                    plan_test_receipt_gate(self.valid_receipt, self.worker_sha, val, set())
                self.assertIn("invalid correction_count", str(cm.exception))

    def test_invalid_seen_signature_types_and_values(self) -> None:
        invalid_sets = [None, [self.worker_sha], "invalid", 123]
        for bad_set in invalid_sets:
            with self.subTest(bad_set=bad_set):
                with self.assertRaises(ProposalError) as cm:
                    plan_test_receipt_gate(self.valid_receipt, self.worker_sha, 0, bad_set)  # type: ignore[arg-type]
                self.assertIn("seen_test_signatures must be a set", str(cm.exception))

        invalid_items = [{123}, {""}, {"a" * 63}, {"a" * 65}, {"A" * 64}, {"g" * 64}]
        for bad_item_set in invalid_items:
            with self.subTest(bad_item_set=bad_item_set):
                with self.assertRaises(ProposalError) as cm:
                    plan_test_receipt_gate(self.valid_receipt, self.worker_sha, 0, bad_item_set)  # type: ignore[arg-type]
                self.assertIn("elements must be lowercase 64-hex SHA strings", str(cm.exception))

    def test_invalid_worker_sha(self) -> None:
        bad_shas = [None, 123, "", "a" * 63, "a" * 65, "A" * 64, "z" * 64]
        for bad_sha in bad_shas:
            with self.subTest(bad_sha=bad_sha):
                with self.assertRaises(ProposalError) as cm:
                    plan_test_receipt_gate(self.valid_receipt, bad_sha, 0, set())  # type: ignore[arg-type]
                self.assertIn("worker_sha must be a lowercase 64-hex SHA", str(cm.exception))

    def test_real_validator_rejects_malformed_receipts(self) -> None:
        receipt_missing_key = dict(self.valid_receipt)
        receipt_missing_key.pop("isolation")
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(receipt_missing_key, self.worker_sha, 0, set())

        receipt_extra_key = {**self.valid_receipt, "extra_key": 123}
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(receipt_extra_key, self.worker_sha, 0, set())

        bad_rc_passed = {**self.valid_receipt, "returncode": 1}
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(bad_rc_passed, self.worker_sha, 0, set())

        bad_rc_failed = {**self.failed_receipt, "returncode": 0}
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(bad_rc_failed, self.worker_sha, 0, set())

        bad_selectors = {**self.valid_receipt, "selectors": "not-a-list"}
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(bad_selectors, self.worker_sha, 0, set())

        wrong_sha_receipt = {**self.valid_receipt, "proposal_sha256": "f" * 64}
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(wrong_sha_receipt, self.worker_sha, 0, set())

        bad_state_receipt = {**self.valid_receipt, "state": "UNKNOWN"}
        with self.assertRaises(ProposalError):
            plan_test_receipt_gate(bad_state_receipt, self.worker_sha, 0, set())

    def test_input_detachment_and_immutability(self) -> None:
        receipt_snapshot = copy.deepcopy(self.valid_receipt)
        plan_test_receipt_gate(self.valid_receipt, self.worker_sha, 0, set())
        self.assertEqual(self.valid_receipt, receipt_snapshot)

        failed_snapshot = copy.deepcopy(self.failed_receipt)
        input_seen = {"c" * 64}
        seen_snapshot = set(input_seen)
        action, feedback, sig, output_seen = plan_test_receipt_gate(
            self.failed_receipt,
            self.worker_sha,
            0,
            input_seen,
        )
        self.assertEqual(self.failed_receipt, failed_snapshot)
        self.assertEqual(input_seen, seen_snapshot)
        self.assertIsNot(output_seen, input_seen)
        self.assertIn("c" * 64, output_seen)
        self.assertIn(sig, output_seen)

    def test_failed_never_returns_run_reviewer_and_passed_no_feedback(self) -> None:
        for count in (0, 1):
            action, _, _, _ = plan_test_receipt_gate(self.failed_receipt, self.worker_sha, count, set())
            self.assertNotEqual(action, "RUN_REVIEWER")

        for count in (0, 1, 2):
            action, feedback, sig, _ = plan_test_receipt_gate(self.valid_receipt, self.worker_sha, count, set())
            self.assertEqual(action, "RUN_REVIEWER")
            self.assertIsNone(feedback)
            self.assertIsNone(sig)


if __name__ == "__main__":
    unittest.main()
