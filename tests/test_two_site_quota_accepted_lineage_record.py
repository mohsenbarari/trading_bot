"""Unit tests for two-site accepted-lineage record codec."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from scripts.two_site_quota_accepted_lineage_record import (
    MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
    PROTECTED_REF_NAMES,
    AcceptedLineageRecordError,
    build_accepted_lineage_record,
    validate_accepted_lineage_record,
)


def _make_fixtures() -> tuple[dict, dict, dict, str]:
    head_before = "a" * 40
    head_after = "b" * 40
    run_id = "cycle-0001-20260929T120000Z-123"
    orig_run_id = "cycle-0002-20260929T110000Z-124"
    assignment_id = "asgn-12345"
    assignment_sha = "1" * 64
    result_sha = "2" * 64
    proposal_sha = "e" * 64

    result = {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Step completed successfully",
        "commit": head_after,
        "next_action": "CONTINUE",
        "blockers": [],
        "gemini_worker_run_id": "worker-run-1",
        "gemini_reviewer_run_id": "reviewer-run-1",
        "proposal_sha256": proposal_sha,
        "head_before": head_before,
        "head_after": head_after,
        "head_changed": True,
    }

    protected_refs = {
        "refs/heads/main": "c" * 40,
        "refs/remotes/origin/main": "d" * 40,
        "refs/heads/candidate/wa-ir-standby-v1": "e" * 40,
    }

    context = {
        "controller_run_id": run_id,
        "protected_refs": protected_refs,
        "assignment_id": assignment_id,
        "assignment_sha256": assignment_sha,
        "current_head": head_after,
    }

    upstream = {
        "original_controller_run_id": orig_run_id,
        "continuation_controller_run_id": run_id,
        "original_request_id": "0" * 32,
        "resume_request_id": "1" * 32,
        "task_id": "task-test-1",
        "assignment_id": assignment_id,
        "source_head": head_before,
        "task_sha256": "3" * 64,
        "assignment_sha256": assignment_sha,
        "semantic_task_fingerprint": "4" * 64,
        "original_wait_sha256": "5" * 64,
        "claim_sha256": "6" * 64,
        "request_sha256": "7" * 64,
        "submission_intent_sha256": "8" * 64,
        "terminal_receipt_sha256": "9" * 64,
        "response_sha256": "0" * 64,
        "response_state": "COMPLETED",
        "exit_code": 0,
        "expected_lineage_snapshot_sha256": "a" * 64,
    }

    return result, context, upstream, result_sha


class TestAcceptedLineageRecord(unittest.TestCase):
    def test_positive_continue_record(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # Validate realistic nonliteral next_action description under CONTINUE
        res["next_action"] = "Proceed to next quota cycle execution step"
        record = build_accepted_lineage_record(res, ctx, ups, res_sha)

        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["accepted_state"], "CONTINUE")
        self.assertEqual(record["result_sha256"], res_sha)
        self.assertEqual(record["head_before"], res["head_before"])
        self.assertEqual(record["head_after"], res["head_after"])
        self.assertIs(record["head_changed"], True)
        self.assertEqual(record["controller_run_id"], ctx["controller_run_id"])
        self.assertEqual(record["assignment_id"], ctx["assignment_id"])
        self.assertEqual(record["assignment_sha256"], ctx["assignment_sha256"])
        self.assertEqual(record["protected_refs"], ctx["protected_refs"])
        self.assertEqual(record["upstream"], ups)

        validated = validate_accepted_lineage_record(
            record,
            expected_result=res,
            expected_context=ctx,
            expected_upstream=ups,
            expected_result_sha256=res_sha,
        )
        self.assertEqual(validated, record)

    def test_positive_complete_record_unchanged_and_changed_heads(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # Scenario 1: Unchanged heads scenario with COMPLETE and next_action=None
        res["state"] = "COMPLETE"
        res["head_changed"] = False
        res["head_after"] = res["head_before"]
        res["commit"] = None
        res["next_action"] = None
        res["gemini_worker_run_id"] = None
        res["gemini_reviewer_run_id"] = None
        res["proposal_sha256"] = None
        ctx["current_head"] = res["head_before"]

        rec = build_accepted_lineage_record(res, ctx, ups, res_sha)
        self.assertEqual(rec["accepted_state"], "COMPLETE")
        self.assertIs(rec["head_changed"], False)
        self.assertEqual(rec["head_before"], rec["head_after"])

        # Scenario 2: Changed heads scenario with COMPLETE and nonliteral next_action string
        res_changed, ctx_changed, ups_changed, sha_changed = _make_fixtures()
        res_changed["state"] = "COMPLETE"
        res_changed["next_action"] = "Archive branch and notify controller"
        rec_changed = build_accepted_lineage_record(
            res_changed, ctx_changed, ups_changed, sha_changed
        )
        self.assertEqual(rec_changed["accepted_state"], "COMPLETE")
        self.assertIs(rec_changed["head_changed"], True)

    def test_continue_state_requires_head_changed(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        res["head_changed"] = False
        res["head_after"] = res["head_before"]
        res["commit"] = None
        res["gemini_worker_run_id"] = None
        res["gemini_reviewer_run_id"] = None
        res["proposal_sha256"] = None
        ctx["current_head"] = res["head_before"]
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, ups, res_sha)

    def test_invalid_result_schema_and_evidence_constraints(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # Bad schema_version
        bad = copy.deepcopy(res)
        bad["schema_version"] = "2.0"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Empty summary
        bad = copy.deepcopy(res)
        bad["summary"] = ""
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Whitespace-only summary
        bad = copy.deepcopy(res)
        bad["summary"] = "   \t\n  "
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # CONTINUE with next_action None
        bad = copy.deepcopy(res)
        bad["state"] = "CONTINUE"
        bad["next_action"] = None
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Non-empty whitespace-only next_action
        bad = copy.deepcopy(res)
        bad["next_action"] = "   "
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Non-string next_action
        bad = copy.deepcopy(res)
        bad["next_action"] = 123
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # COMPLETE with whitespace-only next_action
        bad = copy.deepcopy(res)
        bad["state"] = "COMPLETE"
        bad["next_action"] = "  \t  "
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Bad disposition
        bad = copy.deepcopy(res)
        bad["disposition"] = "ERROR"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Bad category
        bad = copy.deepcopy(res)
        bad["category"] = "INFRA"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Non-empty blockers
        bad = copy.deepcopy(res)
        bad["blockers"] = ["blocker1"]
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Commit mismatch when changed
        bad = copy.deepcopy(res)
        bad["commit"] = "f" * 40
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Inconsistent head_changed and heads
        bad = copy.deepcopy(res)
        bad["head_after"] = bad["head_before"]
        bad["commit"] = bad["head_before"]
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Evidence field present when commit is None
        bad = copy.deepcopy(res)
        bad["state"] = "COMPLETE"
        bad["head_changed"] = False
        bad["head_after"] = bad["head_before"]
        bad["commit"] = None
        bad["gemini_worker_run_id"] = "worker-run-1"
        bad["gemini_reviewer_run_id"] = None
        bad["proposal_sha256"] = None
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["current_head"] = bad["head_before"]
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, bad_ctx, ups, res_sha)

        # Invalid worker run ID format when commit exists
        bad = copy.deepcopy(res)
        bad["gemini_worker_run_id"] = "_invalid_start_char"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Invalid proposal SHA format when commit exists
        bad = copy.deepcopy(res)
        bad["proposal_sha256"] = "not-64-hex"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

        # Missing result key
        bad = copy.deepcopy(res)
        del bad["summary"]
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad, ctx, ups, res_sha)

    def test_protected_refs_exact_names_and_adversarial_wrong_name(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # Exact 3 names validated successfully
        self.assertEqual(set(ctx["protected_refs"].keys()), PROTECTED_REF_NAMES)

        # Adversarial case: same count (3 keys), but wrong ref name
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["protected_refs"] = {
            "refs/heads/main": "c" * 40,
            "refs/remotes/origin/main": "d" * 40,
            "refs/heads/adversarial-branch": "e" * 40,
        }
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

        # Missing one ref (2 keys)
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["protected_refs"] = {
            "refs/heads/main": "c" * 40,
            "refs/remotes/origin/main": "d" * 40,
        }
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

        # Protected ref value not 40 lower hex
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["protected_refs"]["refs/heads/main"] = "INVALID_HEX"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

    def test_validator_requires_all_independent_arguments(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        record = build_accepted_lineage_record(res, ctx, ups, res_sha)

        # Signature call with missing arguments raises TypeError
        with self.assertRaises(TypeError):
            validate_accepted_lineage_record(record)  # type: ignore[call-arg]

        # Calling with None for any mandatory argument raises AcceptedLineageRecordError
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=None,
                expected_context=ctx,
                expected_upstream=ups,
                expected_result_sha256=res_sha,
            )
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=res,
                expected_context=None,
                expected_upstream=ups,
                expected_result_sha256=res_sha,
            )
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=res,
                expected_context=ctx,
                expected_upstream=None,
                expected_result_sha256=res_sha,
            )
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=res,
                expected_context=ctx,
                expected_upstream=ups,
                expected_result_sha256=None,
            )

    def test_upstream_validation_and_request_id_format(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # response_state != COMPLETED
        bad_ups = copy.deepcopy(ups)
        bad_ups["response_state"] = "FAILED"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # exit_code != 0
        bad_ups = copy.deepcopy(ups)
        bad_ups["exit_code"] = 1
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # original_request_id not 32 lower hex
        bad_ups = copy.deepcopy(ups)
        bad_ups["original_request_id"] = "req-orig-1"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # resume_request_id not 32 lower hex
        bad_ups = copy.deepcopy(ups)
        bad_ups["resume_request_id"] = "req-resume-1"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # semantic_task_fingerprint not 64 lower hex
        bad_ups = copy.deepcopy(ups)
        bad_ups["semantic_task_fingerprint"] = "not-64-hex"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # Invented controller run ID format rejected by CYCLE_RUN_RE
        bad_ups = copy.deepcopy(ups)
        bad_ups["original_controller_run_id"] = "run-invented-20260925-1"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # Missing upstream key
        bad_ups = copy.deepcopy(ups)
        del bad_ups["expected_lineage_snapshot_sha256"]
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

    def test_bool_vs_int_type_sensitivity(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # head_changed = 1 instead of True
        bad_res = copy.deepcopy(res)
        bad_res["head_changed"] = 1
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(bad_res, ctx, ups, res_sha)

        # exit_code = False instead of 0
        bad_ups = copy.deepcopy(ups)
        bad_ups["exit_code"] = False
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # Validator comparing record bool to expected int
        record = build_accepted_lineage_record(res, ctx, ups, res_sha)
        bad_expected_res = copy.deepcopy(res)
        bad_expected_res["head_changed"] = 1
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=bad_expected_res,
                expected_context=ctx,
                expected_upstream=ups,
                expected_result_sha256=res_sha,
            )

    def test_mismatches_cross_bindings(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        # Context current_head != result head_after
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["current_head"] = "f" * 40
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

        # Assignment ID mismatch
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["assignment_id"] = "mismatched-id"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

        # Assignment SHA mismatch
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["assignment_sha256"] = "f" * 64
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

        # Controller run ID mismatch with continuation_controller_run_id
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["controller_run_id"] = "cycle-9999-20260929T120000Z-999"
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, bad_ctx, ups, res_sha)

        # Upstream source_head != result head_before
        bad_ups = copy.deepcopy(ups)
        bad_ups["source_head"] = "f" * 40
        with self.assertRaises(AcceptedLineageRecordError):
            build_accepted_lineage_record(res, ctx, bad_ups, res_sha)

        # Result SHA mismatch on validator
        record = build_accepted_lineage_record(res, ctx, ups, res_sha)
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=res,
                expected_context=ctx,
                expected_upstream=ups,
                expected_result_sha256="f" * 64,
            )

    def test_input_detachment(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        record = build_accepted_lineage_record(res, ctx, ups, res_sha)

        # Mutate inputs
        ctx["protected_refs"]["refs/heads/main"] = "0" * 40
        ups["task_id"] = "mutated-task"
        self.assertNotEqual(
            record["protected_refs"]["refs/heads/main"], "0" * 40
        )
        self.assertNotEqual(record["upstream"]["task_id"], "mutated-task")

        # Mutate returned record and verify original result unaffected
        record["accepted_state"] = "COMPLETE"
        self.assertEqual(res["state"], "CONTINUE")

    def test_oversize_rejection(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        record = build_accepted_lineage_record(res, ctx, ups, res_sha)
        record["assignment_id"] = "x" * (MAX_ACCEPTED_LINEAGE_RECORD_BYTES + 100)
        # Also update upstream assignment_id to match record so length check is reached
        record["upstream"]["assignment_id"] = record["assignment_id"]
        with self.assertRaises(AcceptedLineageRecordError):
            validate_accepted_lineage_record(
                record,
                expected_result=res,
                expected_context=ctx,
                expected_upstream=ups,
                expected_result_sha256=res_sha,
            )

    def test_no_io_guarantee(self) -> None:
        res, ctx, ups, res_sha = _make_fixtures()
        with patch("builtins.open", side_effect=AssertionError("open called")), \
            patch("socket.socket", side_effect=AssertionError("socket called")):
            record = build_accepted_lineage_record(res, ctx, ups, res_sha)
            validate_accepted_lineage_record(
                record,
                expected_result=res,
                expected_context=ctx,
                expected_upstream=ups,
                expected_result_sha256=res_sha,
            )


if __name__ == "__main__":
    unittest.main()
