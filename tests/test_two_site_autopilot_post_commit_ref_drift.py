#!/usr/bin/env python3
"""Tests for post-commit ref drift and advisory recovery boundary."""
from __future__ import annotations

import copy
from pathlib import Path
import unittest
from unittest.mock import patch

try:
    from scripts import two_site_codex_autopilot as autopilot_module
    from scripts.two_site_assignment_scope import AssignmentScope
    from scripts.two_site_codex_autopilot import (
        AutopilotError,
        RESULT_KEYS,
        recover_committed_advisory_result,
        validate_result,
        validate_transition,
    )
except ModuleNotFoundError:
    import two_site_codex_autopilot as autopilot_module  # type: ignore[no-redef]
    from two_site_assignment_scope import AssignmentScope  # type: ignore[no-redef]
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        AutopilotError,
        RESULT_KEYS,
        recover_committed_advisory_result,
        validate_result,
        validate_transition,
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

    def test_unchanged_head_null_commit_blocked_receipt_rejects_exact_protected_ref_drift(self):
        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        integration_head = "5" * 40
        control_blobs = {"automation.json": "blob-control-hash"}
        before_protected_refs = {
            "refs/heads/main": "1" * 40,
            "refs/remotes/origin/main": "2" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        }
        after_protected_refs = {
            "refs/heads/main": "4" * 40,
            "refs/remotes/origin/main": "2" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        }

        blocked_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "External gate halt encountered during cycle.",
            "commit": None,
            "next_action": None,
            "blockers": ["External verification gate pending."],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }

        self.assertEqual(validate_result(blocked_receipt), blocked_receipt)
        self.assertIsNone(blocked_receipt["commit"])

        before_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": before_protected_refs,
            "control_blobs": control_blobs,
        }
        after_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": after_protected_refs,
            "control_blobs": control_blobs,
        }

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo):
            with self.assertRaises(AutopilotError) as exc_info:
                validate_transition(Path("/fake/repo/root"), before_repo, blocked_receipt)

        self.assertEqual(
            str(exc_info.exception),
            "a protected main/origin/candidate ref changed during the cycle",
        )

    def test_unchanged_head_null_commit_blocked_receipt_rejects_exact_control_blob_drift(self):
        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        integration_head = "5" * 40
        protected_refs = {
            "refs/heads/main": "1" * 40,
            "refs/remotes/origin/main": "2" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        }
        before_control_blobs = {"automation.json": "blob-control-hash-before"}
        after_control_blobs = {"automation.json": "blob-control-hash-after"}

        blocked_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "External gate halt encountered during cycle.",
            "commit": None,
            "next_action": None,
            "blockers": ["External verification gate pending."],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }

        self.assertEqual(validate_result(blocked_receipt), blocked_receipt)
        self.assertIsNone(blocked_receipt["commit"])

        before_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": protected_refs,
            "control_blobs": before_control_blobs,
        }
        after_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": protected_refs,
            "control_blobs": after_control_blobs,
        }

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo):
            with self.assertRaises(AutopilotError) as exc_info:
                validate_transition(Path("/fake/repo/root"), before_repo, blocked_receipt)

        self.assertEqual(
            str(exc_info.exception),
            "a protected automation control changed without a bound commit",
        )

    def test_unchanged_head_null_commit_blocked_receipt_rejects_exact_assignment_scope_drift(self):
        scope_before = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        scope_after = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="d" * 64,
        )
        integration_head = "5" * 40
        protected_refs = {
            "refs/heads/main": "1" * 40,
            "refs/remotes/origin/main": "2" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        }
        control_blobs = {"automation.json": "blob-control-hash"}

        blocked_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "External gate halt encountered during cycle.",
            "commit": None,
            "next_action": None,
            "blockers": ["External verification gate pending."],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }

        self.assertEqual(validate_result(blocked_receipt), blocked_receipt)
        self.assertIsNone(blocked_receipt["commit"])

        before_repo = {
            "head": integration_head,
            "assignment_scope": scope_before,
            "protected_refs": protected_refs,
            "control_blobs": control_blobs,
        }
        after_repo = {
            "head": integration_head,
            "assignment_scope": scope_after,
            "protected_refs": protected_refs,
            "control_blobs": control_blobs,
        }

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo):
            with self.assertRaises(AutopilotError) as exc_info:
                validate_transition(Path("/fake/repo/root"), before_repo, blocked_receipt)

        self.assertEqual(
            str(exc_info.exception),
            "active assignment scope changed during cycle",
        )

    def test_unchanged_head_null_commit_blocked_receipt_returns_stable_transition_metadata(self):
        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        integration_head = "5" * 40
        protected_refs = {
            "refs/heads/main": "1" * 40,
            "refs/remotes/origin/main": "2" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        }
        control_blobs = {"automation.json": "blob-control-hash"}

        blocked_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "External gate halt encountered during cycle.",
            "commit": None,
            "next_action": None,
            "blockers": ["External verification gate pending."],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }

        self.assertEqual(validate_result(blocked_receipt), blocked_receipt)
        self.assertIsNone(blocked_receipt["commit"])
        self.assertIsNone(blocked_receipt["gemini_worker_run_id"])
        self.assertIsNone(blocked_receipt["gemini_reviewer_run_id"])
        self.assertIsNone(blocked_receipt["proposal_sha256"])

        before_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": protected_refs,
            "control_blobs": control_blobs,
        }
        after_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": protected_refs,
            "control_blobs": control_blobs,
        }

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo):
            transition = validate_transition(Path("/fake/repo/root"), before_repo, blocked_receipt)

        self.assertEqual(transition["head_before"], integration_head)
        self.assertEqual(transition["head_after"], integration_head)
        self.assertIs(transition["head_changed"], False)
        self.assertEqual(transition["state"], "BLOCKED")
        self.assertEqual(transition["disposition"], "HALT")
        self.assertEqual(transition["category"], "EXTERNAL_GATE")
        self.assertIsNone(transition["commit"])
        self.assertEqual(transition["blockers"], ["External verification gate pending."])

    def test_unchanged_head_null_commit_blocked_rescope_receipt_returns_stable_transition_metadata(self):
        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        integration_head = "5" * 40
        protected_refs = {
            "refs/heads/main": "1" * 40,
            "refs/remotes/origin/main": "2" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        }
        control_blobs = {"automation.json": "blob-control-hash"}

        expected_next_action = "Rescope assignment boundaries and retry."
        expected_blockers = ["Scope too broad for single cycle."]
        blocked_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "RESCOPE",
            "category": "INTERNAL_REJECTION",
            "summary": "Internal rejection requiring assignment scope adjustment.",
            "commit": None,
            "next_action": expected_next_action,
            "blockers": expected_blockers,
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }

        self.assertEqual(validate_result(blocked_receipt), blocked_receipt)
        self.assertIsNone(blocked_receipt["commit"])
        self.assertIsNone(blocked_receipt["gemini_worker_run_id"])
        self.assertIsNone(blocked_receipt["gemini_reviewer_run_id"])
        self.assertIsNone(blocked_receipt["proposal_sha256"])

        before_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": protected_refs,
            "control_blobs": control_blobs,
        }
        after_repo = {
            "head": integration_head,
            "assignment_scope": scope,
            "protected_refs": protected_refs,
            "control_blobs": control_blobs,
        }

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo):
            transition = validate_transition(Path("/fake/repo/root"), before_repo, blocked_receipt)

        self.assertEqual(transition["head_before"], integration_head)
        self.assertEqual(transition["head_after"], integration_head)
        self.assertIs(transition["head_changed"], False)
        self.assertEqual(transition["state"], "BLOCKED")
        self.assertEqual(transition["disposition"], "RESCOPE")
        self.assertEqual(transition["category"], "INTERNAL_REJECTION")
        self.assertIsNone(transition["commit"])
        self.assertEqual(transition["blockers"], expected_blockers)
        self.assertEqual(transition["next_action"], expected_next_action)


if __name__ == "__main__":
    unittest.main()
