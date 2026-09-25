"""Hermetic unit tests for build_reviewer_correction_feedback."""
from __future__ import annotations

import copy
import json
import unittest

from scripts.two_site_quota_resume_remainder_feedback import (
    ProposalError,
    build_reviewer_correction_feedback,
)

try:
    from scripts.two_site_pre_review_receipt import (
        SandboxError,
        validate_test_receipt,
    )
except ImportError:
    try:
        from two_site_pre_review_receipt import (
            SandboxError,
            validate_test_receipt,
        )
    except ImportError:
        try:
            from scripts.two_site_agent_sandbox import SandboxError
            from scripts.two_site_pre_review_receipt import validate_test_receipt
        except ImportError:
            from two_site_agent_sandbox import SandboxError
            from two_site_pre_review_receipt import validate_test_receipt


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
            "unsupported_specs": [],
            "returncode": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "snapshot_files": 2,
            "snapshot_bytes": 1024,
            "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all",
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

    def test_unmocked_validator_accepts_valid_fixture(self) -> None:
        norm = validate_test_receipt(self.valid_receipt, self.worker_sha, require_passed=True)
        self.assertEqual(norm["state"], "PASSED")
        self.assertEqual(norm["proposal_sha256"], self.worker_sha)
        self.assertEqual(norm["selectors"], ["tests/test_foo.py", "tests/test_bar.py"])
        self.assertEqual(norm["unsupported_specs"], [])
        self.assertEqual(norm["returncode"], 0)
        self.assertEqual(norm["stdout_tail"], "")
        self.assertEqual(norm["stderr_tail"], "")
        self.assertEqual(norm["snapshot_files"], 2)
        self.assertEqual(norm["snapshot_bytes"], 1024)
        self.assertEqual(norm["isolation"], "bwrap:ro-source,empty-home,clearenv,unshare-all")

    def test_unmocked_validator_rejects_missing_and_extra_keys(self) -> None:
        for missing_key in self.valid_receipt:
            with self.subTest(missing_key=missing_key):
                bad = {k: v for k, v in self.valid_receipt.items() if k != missing_key}
                with self.assertRaises(SandboxError):
                    validate_test_receipt(bad, self.worker_sha, require_passed=True)
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        bad,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )
        extra = {**self.valid_receipt, "unexpected": "value"}
        with self.assertRaises(SandboxError):
            validate_test_receipt(extra, self.worker_sha, require_passed=True)
        with self.assertRaises(ProposalError):
            build_reviewer_correction_feedback(
                self.valid_reviewer_result,
                self.worker_sha,
                extra,
                self.receipt_sha,
                0,
                self.seen_norm_findings,
            )

    def test_unmocked_validator_rejects_wrong_returncode(self) -> None:
        bad_rcs = [1, -1, True, False, "0", None]
        for rc in bad_rcs:
            with self.subTest(rc=rc):
                bad = {**self.valid_receipt, "returncode": rc}
                with self.assertRaises(SandboxError):
                    validate_test_receipt(bad, self.worker_sha, require_passed=True)
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        bad,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_unmocked_validator_rejects_unsupported_specs(self) -> None:
        bad_specs = [
            "not-a-list",
            [""],
            [123],
            ["spec-a", "spec-a"],
            [f"spec_{i}" for i in range(33)],
        ]
        for specs in bad_specs:
            with self.subTest(specs=specs):
                bad = {**self.valid_receipt, "unsupported_specs": specs}
                with self.assertRaises(SandboxError):
                    validate_test_receipt(bad, self.worker_sha, require_passed=True)
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        bad,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_unmocked_validator_rejects_duplicate_and_invalid_selectors(self) -> None:
        bad_selectors = [
            [],
            "not-a-list",
            ["not_a_test_selector"],
            ["tests/test_foo.py", "tests/test_foo.py"],
            [""],
            [123],
        ]
        for sel in bad_selectors:
            with self.subTest(selectors=sel):
                bad = {**self.valid_receipt, "selectors": sel}
                with self.assertRaises(SandboxError):
                    validate_test_receipt(bad, self.worker_sha, require_passed=True)
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        bad,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_unmocked_validator_rejects_bad_snapshot_types(self) -> None:
        bad_values = [-1, True, False, "10", None]
        for key in ("snapshot_files", "snapshot_bytes"):
            for val in bad_values:
                with self.subTest(key=key, val=val):
                    bad = {**self.valid_receipt, key: val}
                    with self.assertRaises(SandboxError):
                        validate_test_receipt(bad, self.worker_sha, require_passed=True)
                    with self.assertRaises(ProposalError):
                        build_reviewer_correction_feedback(
                            self.valid_reviewer_result,
                            self.worker_sha,
                            bad,
                            self.receipt_sha,
                            0,
                            self.seen_norm_findings,
                        )

    def test_unmocked_validator_rejects_mismatch_sha(self) -> None:
        for bad_sha in ("c" * 64, "not-a-sha", "A" * 64, ""):
            with self.subTest(bad_sha=bad_sha):
                bad = {**self.valid_receipt, "proposal_sha256": bad_sha}
                with self.assertRaises(SandboxError):
                    validate_test_receipt(bad, self.worker_sha, require_passed=True)
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        bad,
                        self.receipt_sha,
                        0,
                        self.seen_norm_findings,
                    )

    def test_unmocked_validator_rejects_failed_state(self) -> None:
        cases = [
            {"state": "FAILED", "returncode": 1},
            {"state": "FAILED", "returncode": 0},
            {"state": "UNKNOWN", "returncode": 0},
            {"state": 123, "returncode": 0},
        ]
        for patch in cases:
            with self.subTest(patch=patch):
                bad = {**self.valid_receipt, **patch}
                with self.assertRaises(SandboxError):
                    validate_test_receipt(bad, self.worker_sha, require_passed=True)
                with self.assertRaises(ProposalError):
                    build_reviewer_correction_feedback(
                        self.valid_reviewer_result,
                        self.worker_sha,
                        bad,
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
