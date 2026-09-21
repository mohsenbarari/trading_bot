#!/usr/bin/env python3
"""Tests for post-commit ref drift and advisory recovery boundary."""
from __future__ import annotations

import copy
import unittest

try:
    from scripts.two_site_codex_autopilot import (
        AutopilotError,
        RESULT_KEYS,
        recover_committed_advisory_result,
        validate_result,
    )
except ModuleNotFoundError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        AutopilotError,
        RESULT_KEYS,
        recover_committed_advisory_result,
        validate_result,
    )

VALID_COMMIT = "1" * 40
VALID_WORKER_RUN_ID = "run-gemini-worker-001"
VALID_REVIEWER_RUN_ID = "run-gemini-reviewer-001"
VALID_PROPOSAL_SHA256 = "a" * 64


class TestPostCommitAdvisoryRecoveryBoundary(unittest.TestCase):
    """Unit tests asserting recovery boundary behavior for committed advisory receipts."""

    def test_committed_continue_advisory_is_the_only_recoverable_shape(self):
        raw_receipt = {
            "schema_version": "1.0",
            "state": "CONTINUE",
            "disposition": "NONE",
            "category": "NONE",
            "summary": "Completed step 1 and committed changes.",
            "commit": VALID_COMMIT,
            "next_action": "Proceed with step 2.",
            "blockers": ["Non-blocking baseline advisory defect."],
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }
        self.assertEqual(set(raw_receipt), RESULT_KEYS)
        original_copy = copy.deepcopy(raw_receipt)

        with self.assertRaises(AutopilotError) as exc_info:
            validate_result(raw_receipt)
        self.assertEqual(str(exc_info.exception), "non-blocked state cannot report blockers")

        normalized, advisories = recover_committed_advisory_result(
            raw_receipt, exc_info.exception
        )

        self.assertEqual(raw_receipt, original_copy)
        self.assertEqual(raw_receipt["blockers"], ["Non-blocking baseline advisory defect."])
        self.assertEqual(advisories, ["Non-blocking baseline advisory defect."])
        self.assertIsNot(advisories, raw_receipt["blockers"])

        self.assertEqual(normalized["blockers"], [])
        self.assertEqual(normalized["state"], "CONTINUE")
        self.assertEqual(normalized["disposition"], "NONE")
        self.assertEqual(normalized["category"], "NONE")
        self.assertEqual(normalized["summary"], "Completed step 1 and committed changes.")
        self.assertEqual(normalized["commit"], VALID_COMMIT)
        self.assertEqual(normalized["next_action"], "Proceed with step 2.")
        self.assertEqual(normalized["gemini_worker_run_id"], VALID_WORKER_RUN_ID)
        self.assertEqual(normalized["gemini_reviewer_run_id"], VALID_REVIEWER_RUN_ID)
        self.assertEqual(normalized["proposal_sha256"], VALID_PROPOSAL_SHA256)
        self.assertEqual(validate_result(normalized), normalized)

    def test_committed_halt_receipt_is_rejected_before_advisory_normalization(self):
        halt_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "Encountered external blocker during execution.",
            "commit": VALID_COMMIT,
            "next_action": None,
            "blockers": ["External requirement missing."],
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }
        self.assertEqual(set(halt_receipt), RESULT_KEYS)
        original_copy = copy.deepcopy(halt_receipt)

        recoverable_error = AutopilotError("non-blocked state cannot report blockers")

        with self.assertRaises(AutopilotError) as exc_info:
            recover_committed_advisory_result(halt_receipt, recoverable_error)

        self.assertEqual(str(exc_info.exception), "recovery only applies to CONTINUE state")
        self.assertEqual(halt_receipt, original_copy)


if __name__ == "__main__":
    unittest.main()
