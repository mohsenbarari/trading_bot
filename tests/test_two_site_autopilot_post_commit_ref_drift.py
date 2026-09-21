#!/usr/bin/env python3
"""Tests for post-commit ref drift and advisory recovery boundary."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
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

    def test_unchanged_head_null_commit_blocked_rescope_transition_is_not_a_continuation_hint(self):
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
        self.assertEqual(blocked_receipt["next_action"], expected_next_action)
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
        self.assertEqual(transition["next_action"], expected_next_action)

        hint = autopilot_module.continuation_hint(transition, integration_head)
        self.assertIsNone(hint)
        self.assertEqual(blocked_receipt["next_action"], expected_next_action)
        self.assertEqual(transition["next_action"], expected_next_action)

    def test_changed_head_null_commit_blocked_receipt_rejects_protected_ref_drift_before_commit_chain_processing(self):
        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        before_head = "5" * 40
        after_head = "6" * 40
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
        self.assertIsNone(blocked_receipt["gemini_worker_run_id"])
        self.assertIsNone(blocked_receipt["gemini_reviewer_run_id"])
        self.assertIsNone(blocked_receipt["proposal_sha256"])

        before_repo = {
            "head": before_head,
            "assignment_scope": scope,
            "protected_refs": before_protected_refs,
            "control_blobs": control_blobs,
        }
        after_repo = {
            "head": after_head,
            "assignment_scope": scope,
            "protected_refs": after_protected_refs,
            "control_blobs": control_blobs,
        }

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo), \
                patch.object(autopilot_module, "git") as mock_git, \
                patch.object(autopilot_module, "validate_scoped_gemini_chain") as mock_validate_scoped_gemini_chain:
            with self.assertRaises(AutopilotError) as exc_info:
                validate_transition(Path("/fake/repo/root"), before_repo, blocked_receipt)

        self.assertEqual(
            str(exc_info.exception),
            "a protected main/origin/candidate ref changed during the cycle",
        )
        mock_git.assert_not_called()
        mock_validate_scoped_gemini_chain.assert_not_called()

    def test_committed_halt_with_verified_commit_and_actual_protected_ref_drift_is_accepted(self):
        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        before_head = "5" * 40
        after_head = "6" * 40
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
        before_control_blobs = {"automation.json": "blob-control-hash"}
        after_control_blobs = {"automation.json": "blob-control-hash"}

        expected_blockers = ["External verification gate pending."]
        halt_receipt = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "External gate halt encountered during cycle.",
            "commit": after_head,
            "next_action": None,
            "blockers": expected_blockers,
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }
        self.assertEqual(set(halt_receipt), RESULT_KEYS)

        before_repo = {
            "head": before_head,
            "assignment_scope": scope,
            "protected_refs": before_protected_refs,
            "control_blobs": before_control_blobs,
        }
        after_repo = {
            "head": after_head,
            "assignment_scope": scope,
            "protected_refs": after_protected_refs,
            "control_blobs": after_control_blobs,
        }

        mock_proposal = {"changes": [{"path": "change.py", "content": "NEW\n"}]}
        fake_root = Path("/fake/repo/root")

        with patch.object(autopilot_module, "validate_repository", return_value=after_repo), \
                patch.object(autopilot_module, "git", return_value="") as mock_git, \
                patch.object(autopilot_module, "validate_scoped_gemini_chain", return_value=mock_proposal) as mock_gemini_chain, \
                patch.object(autopilot_module, "validate_scoped_committed_proposal") as mock_committed_proposal, \
                patch.object(autopilot_module, "validate_control_changes_are_proposed") as mock_control_changes:
            transition = validate_transition(fake_root, before_repo, halt_receipt)

        mock_git.assert_called_once_with(fake_root, "merge-base", "--is-ancestor", before_head, after_head)
        mock_gemini_chain.assert_called_once_with(fake_root, before_head, halt_receipt, scope)
        mock_committed_proposal.assert_called_once_with(fake_root, before_head, after_head, mock_proposal, scope)
        mock_control_changes.assert_called_once_with(before_control_blobs, after_control_blobs, mock_proposal)

        self.assertEqual(transition["head_before"], before_head)
        self.assertEqual(transition["head_after"], after_head)
        self.assertIs(transition["head_changed"], True)
        self.assertEqual(transition["state"], "BLOCKED")
        self.assertEqual(transition["disposition"], "HALT")
        self.assertEqual(transition["category"], "EXTERNAL_GATE")
        self.assertEqual(transition["commit"], after_head)
        self.assertEqual(transition["blockers"], expected_blockers)
        self.assertIsNone(transition["next_action"])
        self.assertEqual(transition["gemini_worker_run_id"], VALID_WORKER_RUN_ID)
        self.assertEqual(transition["gemini_reviewer_run_id"], VALID_REVIEWER_RUN_ID)
        self.assertEqual(transition["proposal_sha256"], VALID_PROPOSAL_SHA256)

        hint = autopilot_module.continuation_hint(transition, after_head)
        self.assertIsNone(hint)

    def test_run_loop_publishes_committed_halt_transition_metadata(self):
        before_head = "5" * 40
        after_head = "6" * 40
        expected_blockers = ["External verification gate pending."]
        transition_result = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "Committed halt waiting for external gate.",
            "commit": after_head,
            "next_action": None,
            "blockers": expected_blockers,
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
            "head_before": before_head,
            "head_after": after_head,
            "head_changed": True,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            state_root = root / ".local/two-site-refactor/autopilot"
            fake_codex = root / "fake_codex"
            fake_run_dir = state_root / "runs" / "cycle-0001-20260921T180000Z-12345"

            with patch.object(autopilot_module, "git", return_value=before_head), \
                    patch.object(autopilot_module, "ref_snapshot", return_value={}), \
                    patch.object(autopilot_module, "run_cycle", return_value=(fake_run_dir, transition_result)) as mock_run_cycle, \
                    patch.object(autopilot_module, "prune_runs") as mock_prune_runs, \
                    patch.object(autopilot_module, "write_supervisor_state") as mock_write_supervisor_state:
                ret = autopilot_module.run_loop(root, fake_codex, max_cycles=10, timeout=300, once=False)

        self.assertEqual(ret, 2)
        mock_run_cycle.assert_called_once()
        mock_prune_runs.assert_called_once_with(state_root)

        self.assertEqual(mock_write_supervisor_state.call_count, 2)
        final_args, final_kwargs = mock_write_supervisor_state.call_args
        self.assertEqual(final_args, (state_root, "BLOCKED"))
        self.assertEqual(final_kwargs["cycle"], 1)
        self.assertEqual(final_kwargs["evidence"], str(fake_run_dir))
        self.assertEqual(final_kwargs["head"], after_head)
        self.assertEqual(final_kwargs["head_after"], after_head)
        self.assertIs(final_kwargs["head_changed"], True)
        self.assertEqual(final_kwargs["commit"], after_head)
        self.assertEqual(final_kwargs["summary"], transition_result["summary"])
        self.assertIsNone(final_kwargs["next_action"])
        self.assertEqual(final_kwargs["blockers"], expected_blockers)
        self.assertEqual(final_kwargs["gemini_worker_run_id"], VALID_WORKER_RUN_ID)
        self.assertEqual(final_kwargs["gemini_reviewer_run_id"], VALID_REVIEWER_RUN_ID)
        self.assertEqual(final_kwargs["proposal_sha256"], VALID_PROPOSAL_SHA256)

    def test_persisted_blocked_terminal_statuses_are_never_continuation_hints(self):
        persisted_continuation = autopilot_module.persisted_continuation
        current_head = VALID_COMMIT

        committed_halt_status = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "Committed halt waiting for external gate.",
            "head": current_head,
            "head_before": "4" * 40,
            "head_after": current_head,
            "head_changed": True,
            "commit": current_head,
            "next_action": None,
            "blockers": ["External verification gate pending."],
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }
        null_commit_halt_status = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "HALT",
            "category": "EXTERNAL_GATE",
            "summary": "External gate halt encountered during cycle.",
            "head": current_head,
            "head_before": current_head,
            "head_after": current_head,
            "head_changed": False,
            "commit": None,
            "next_action": None,
            "blockers": ["External verification gate pending."],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }
        null_commit_rescope_status = {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "RESCOPE",
            "category": "INTERNAL_REJECTION",
            "summary": "Internal rejection requiring assignment scope adjustment.",
            "head": current_head,
            "head_before": current_head,
            "head_after": current_head,
            "head_changed": False,
            "commit": None,
            "next_action": "Rescope assignment boundaries and retry.",
            "blockers": ["Scope too broad for single cycle."],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            state_root = root / ".local/two-site-refactor/autopilot"
            state_root.mkdir(parents=True, exist_ok=True)
            status_path = state_root / "status.json"

            for status_payload in (
                committed_halt_status,
                null_commit_halt_status,
                null_commit_rescope_status,
            ):
                status_path.write_text(
                    json.dumps(status_payload, indent=2), encoding="utf-8"
                )
                self.assertEqual(status_payload.get("head"), current_head)
                self.assertEqual(status_payload.get("head_after"), current_head)
                hint = persisted_continuation(root, state_root, current_head)
                self.assertIsNone(hint)

    def test_persisted_committed_continue_checkpoint_reused_only_at_current_head(self):
        persisted_continuation = autopilot_module.persisted_continuation
        prompt_for_cycle = autopilot_module.prompt_for_cycle
        current_head = VALID_COMMIT
        stale_head = "2" * 40

        scope = AssignmentScope(
            assignment_id="WA-P1-00B-AE03-POSTCOMMIT-REF-DRIFT-ORDER-20260921-038",
            stage_id="P1-00b",
            allowed_paths=["tests/test_two_site_autopilot_post_commit_ref_drift.py"],
            forbidden_paths=[],
            expires_at="2026-09-22T00:00:00Z",
            sha256="c" * 64,
        )
        before_repo = {
            "head": current_head,
            "assignment_scope": scope,
            "protected_refs": {
                "refs/heads/main": "1" * 40,
                "refs/remotes/origin/main": "2" * 40,
                "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
            },
        }

        expected_hint = {
            "head_after": current_head,
            "commit": current_head,
            "summary": "Completed step 1 and committed changes.",
            "next_action": "Proceed with step 2.",
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }

        valid_status = {
            "state": "CONTINUE",
            "head_changed": True,
            "head_after": current_head,
            "commit": current_head,
            "blockers": [],
            "summary": "Completed step 1 and committed changes.",
            "next_action": "Proceed with step 2.",
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }

        stale_status = {
            "state": "CONTINUE",
            "head_changed": True,
            "head_after": stale_head,
            "commit": stale_head,
            "blockers": [],
            "summary": "Completed step 1 on prior head.",
            "next_action": "Proceed with step 2.",
            "gemini_worker_run_id": VALID_WORKER_RUN_ID,
            "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
            "proposal_sha256": VALID_PROPOSAL_SHA256,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            state_root = root / ".local/two-site-refactor/autopilot"
            state_root.mkdir(parents=True, exist_ok=True)
            status_path = state_root / "status.json"

            prompt_path = root / autopilot_module.PROMPT_REL
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text("AUTOPILOT PROMPT TEMPLATE", encoding="utf-8")

            status_path.write_text(json.dumps(valid_status, indent=2), encoding="utf-8")
            recovered_candidate = persisted_continuation(root, state_root, current_head)
            self.assertIsNotNone(recovered_candidate)

            prompt_text = prompt_for_cycle(
                root, 1, before_repo, continuation=recovered_candidate
            )
            _, context_json = prompt_text.split("\n\nSUPERVISOR_CONTEXT_JSON:\n", 1)
            context = json.loads(context_json)
            self.assertEqual(context.get("validated_previous_cycle_hint"), expected_hint)
            self.assertEqual(
                set(context["validated_previous_cycle_hint"]),
                {
                    "head_after",
                    "commit",
                    "summary",
                    "next_action",
                    "gemini_worker_run_id",
                    "gemini_reviewer_run_id",
                    "proposal_sha256",
                },
            )

            status_path.write_text(json.dumps(stale_status, indent=2), encoding="utf-8")
            stale_candidate = persisted_continuation(root, state_root, current_head)
            self.assertIsNone(stale_candidate)

            stale_prompt_text = prompt_for_cycle(
                root, 1, before_repo, continuation=stale_candidate
            )
            _, stale_context_json = stale_prompt_text.split(
                "\n\nSUPERVISOR_CONTEXT_JSON:\n", 1
            )
            stale_context = json.loads(stale_context_json)
            self.assertNotIn("validated_previous_cycle_hint", stale_context)

            status_path.write_text("{ malformed json", encoding="utf-8")
            malformed_candidate = persisted_continuation(root, state_root, current_head)
            self.assertIsNone(malformed_candidate)

            malformed_prompt_text = prompt_for_cycle(
                root, 1, before_repo, continuation=malformed_candidate
            )
            _, malformed_context_json = malformed_prompt_text.split(
                "\n\nSUPERVISOR_CONTEXT_JSON:\n", 1
            )
            malformed_context = json.loads(malformed_context_json)
            self.assertNotIn("validated_previous_cycle_hint", malformed_context)


if __name__ == "__main__":
    unittest.main()
