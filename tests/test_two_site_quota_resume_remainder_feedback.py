"""Hermetic unit tests for build_reviewer_correction_feedback."""
from __future__ import annotations

import copy
import json
import unittest

from scripts.two_site_quota_resume_remainder_feedback import (
    ProposalError,
    build_reviewer_correction_feedback,
)


class TestTwoSiteQuotaResumeRemainderFeedback(unittest.TestCase):
    """Unit test suite validating reviewer correction feedback behavior."""

    def setUp(self) -> None:
        self.worker_sha = "a" * 64
        self.receipt_sha = "b" * 64
        self.valid_reviewer_result = {
            "status": "CHANGES_REQUIRED",
            "findings": [
                "scripts/foo.py:10 syntax error in function declaration",
                "scripts/bar.py:25 unhandled exception on missing file",
            ],
        }
        self.valid_receipt = {
            "state": "PASSED",
            "proposal_sha256": self.worker_sha,
            "selectors": ["tests/test_foo.py", "tests/test_bar.py"],
            "returncode": 0,
        }
        self.seen_norm_findings = {"prior issue already fixed"}

    def test_valid_feedback_correction_counts_0_and_1(self) -> None:
        for count in (0, 1):
            with self.subTest(correction_count=count):
                payload, new_seen = build_reviewer_correction_feedback(
                    self.valid_reviewer_result,
                    self.worker_sha,
                    self.valid_receipt,
                    self.receipt_sha,
                    count,
                    self.seen_norm_findings,
                )
                self.assertEqual(payload["reviewer"], "gemini")
                self.assertEqual(payload["reviewed_proposal_sha256"], self.worker_sha)
                self.assertEqual(payload["findings"], self.valid_reviewer_result["findings"])
                expected_evidence = (
                    f"receipt_sha256={self.receipt_sha} proposal_sha256={self.worker_sha} "
                    f"selector_count=2"
                )
                self.assertEqual(payload["test_evidence"], [expected_evidence])
                self.assertIn("scripts/foo.py:10 syntax error in function declaration", new_seen)
                self.assertIn("scripts/bar.py:25 unhandled exception on missing file", new_seen)
                self.assertIn("prior issue already fixed", new_seen)
                self.assertEqual(len(new_seen), 3)

    def test_correction_exhaustion_at_count_2_and_above(self) -> None:
        for count in (2, 3):
            with self.subTest(count=count):
                with self.assertRaises(ProposalError) as cm:
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        self.valid_receipt,
                        self.receipt_sha,
                        count,
                        self.seen_norm_findings,
                    )
                self.assertIn("correction exhaustion", str(cm.exception))

    def test_invalid_correction_count_values_and_types(self) -> None:
        for invalid_count in (-1, True, False, "0", None, 1.0):
            with self.subTest(invalid_count=invalid_count):
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        self.valid_receipt,
                        self.receipt_sha,
                        invalid_count,
                        self.seen_norm_findings,
                    )

    def test_duplicate_normalized_findings_in_current_result(self) -> None:
        bad_result = {
            "status": "CHANGES_REQUIRED",
            "findings": [
                "Missing docstring on function foo",
                "  missing   docstring  on   function  foo  ",
            ],
        }
        with self.assertRaises(ProposalError) as cm:
            build_reviewer_correction_feedback(
                bad_result,
                self.worker_sha,
                self.valid_receipt,
                self.receipt_sha,
                0,
                self.seen_norm_findings,
            )
        self.assertIn("duplicate normalized finding", str(cm.exception))

    def test_recurring_normalized_findings_against_prior_seen(self) -> None:
        seen = {"missing docstring on function foo"}
        bad_result = {
            "status": "CHANGES_REQUIRED",
            "findings": ["  Missing   Docstring  On Function Foo  "],
        }
        with self.assertRaises(ProposalError) as cm:
            build_reviewer_correction_feedback(
                bad_result,
                self.worker_sha,
                self.valid_receipt,
                self.receipt_sha,
                0,
                seen,
            )
        self.assertIn("recurring normalized findings", str(cm.exception))

    def test_invalid_reviewer_status(self) -> None:
        for status in ("VERIFIED", "BLOCKED", "TASK_BLOCKED", "PROPOSAL_READY", None, 123):
            with self.subTest(status=status):
                result = {"status": status, "findings": ["issue"]}
                with self.assertRaises(ProposalError) as cm:
                    build_reviewer_correction_feedback(
                        result,
                        self.worker_sha,
                        self.valid_receipt,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )
                self.assertIn("CHANGES_REQUIRED", str(cm.exception))

    def test_invalid_reviewer_findings_envelope(self) -> None:
        cases = [
            {"status": "CHANGES_REQUIRED"},  # missing
            {"status": "CHANGES_REQUIRED", "findings": []},  # empty
            {"status": "CHANGES_REQUIRED", "findings": [""]},  # empty item
            {"status": "CHANGES_REQUIRED", "findings": ["   "]},  # whitespace item
            {"status": "CHANGES_REQUIRED", "findings": [123]},  # non-string item
            {"status": "CHANGES_REQUIRED", "findings": "not-a-list"},  # non-list
            {"status": "CHANGES_REQUIRED", "findings": [f"issue {i}" for i in range(33)]},  # >32
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        case,
                        self.worker_sha,
                        self.valid_receipt,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_invalid_shas(self) -> None:
        invalid_shas = ["A" * 64, "a" * 63, "a" * 65, "not-hex" * 8, 12345, None]
        for bad_sha in invalid_shas:
            with self.subTest(bad_worker_sha=bad_sha):
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        bad_sha,
                        self.valid_receipt,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )
            with self.subTest(bad_receipt_sha=bad_sha):
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        self.valid_receipt,
                        bad_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_invalid_receipt_state_and_selectors(self) -> None:
        cases = [
            {"state": "FAILED", "proposal_sha256": self.worker_sha, "selectors": ["test"]},
            {"state": "PASSED", "proposal_sha256": "f" * 64, "selectors": ["test"]},
            {"state": "PASSED", "proposal_sha256": self.worker_sha, "selectors": []},
            {"state": "PASSED", "proposal_sha256": self.worker_sha, "selectors": "not-list"},
            {"state": "PASSED", "proposal_sha256": self.worker_sha, "selectors": [""]},
            {"state": "PASSED", "proposal_sha256": self.worker_sha, "selectors": ["   "]},
            {"state": "PASSED", "proposal_sha256": self.worker_sha, "selectors": [123]},
        ]
        for receipt in cases:
            with self.subTest(receipt=receipt):
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        receipt,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_input_immutability_and_detachment(self) -> None:
        reviewer_copy = copy.deepcopy(self.valid_reviewer_result)
        receipt_copy = copy.deepcopy(self.valid_receipt)
        seen_copy = set(self.seen_norm_findings)

        payload, new_seen = build_reviewer_correction_feedback(
            self.valid_reviewer_result,
            self.worker_sha,
            self.valid_receipt,
            self.receipt_sha,
            0,
            self.seen_norm_findings,
        )

        self.assertEqual(self.valid_reviewer_result, reviewer_copy)
        self.assertEqual(self.valid_receipt, receipt_copy)
        self.assertEqual(self.seen_norm_findings, seen_copy)

        # Modify returned objects to verify detachment
        payload["findings"].append("mutated finding")
        new_seen.add("mutated seen")
        self.assertEqual(self.valid_reviewer_result, reviewer_copy)
        self.assertEqual(self.seen_norm_findings, seen_copy)


if __name__ == "__main__":
    unittest.main()
