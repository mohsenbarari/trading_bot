"""Smoke tests for plan_test_receipt_gate."""
from __future__ import annotations

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


class TestTwoSiteQuotaResumeTestGateSmoke(unittest.TestCase):
    """Smoke test suite validating plan_test_receipt_gate behavior."""

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

    def test_passed_receipt_at_correction_count_2(self) -> None:
        action, feedback, sig, seen = plan_test_receipt_gate(
            self.valid_receipt,
            self.worker_sha,
            2,
            set(),
        )
        self.assertEqual(action, "RUN_REVIEWER")
        self.assertIsNone(feedback)
        self.assertIsNone(sig)
        self.assertEqual(seen, set())

    def test_failed_receipt_at_correction_count_0_and_repeat_rejection(self) -> None:
        action, feedback, sig, seen = plan_test_receipt_gate(
            self.failed_receipt,
            self.worker_sha,
            0,
            set(),
        )
        self.assertEqual(action, "CORRECTION_REQUIRED")
        self.assertIsNotNone(feedback)
        self.assertIsNotNone(sig)
        self.assertIn(sig, seen)

        with self.assertRaises(ProposalError) as cm:
            plan_test_receipt_gate(
                self.failed_receipt,
                self.worker_sha,
                1,
                seen,
            )
        self.assertIn("repeated", str(cm.exception).lower())

    def test_failed_receipt_exhaustion_at_correction_count_2(self) -> None:
        with self.assertRaises(ProposalError) as cm:
            plan_test_receipt_gate(
                self.failed_receipt,
                self.worker_sha,
                2,
                set(),
            )
        self.assertIn("correction exhaustion", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
