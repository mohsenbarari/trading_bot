"""Unit tests for file-bound accepted lineage candidate builder."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_quota_accepted_lineage_fileproof import (
    AcceptedLineageFileproofError,
    build_file_bound_accepted_lineage_candidate,
)


def _create_cycle_dir(root: Path, run_id: str) -> Path:
    target = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / run_id
    curr = root
    for part in (".local", "two-site-refactor", "autopilot", "runs", run_id):
        curr = curr / part
        curr.mkdir(mode=0o700, exist_ok=True)
        os.chmod(curr, 0o700)
    return target


def _make_fixtures(
    run_id: str = "cycle-0001-20260929T120000Z-123",
) -> tuple[dict, dict, dict, dict]:
    head_before = "a" * 40
    head_after = "b" * 40
    orig_run_id = "cycle-0002-20260929T110000Z-124"
    assignment_id = "asgn-12345"
    assignment_sha = "1" * 64
    proposal_sha = "e" * 64

    continue_expected = {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Cycle progressed to step 2",
        "commit": head_after,
        "next_action": "Proceed to next step",
        "blockers": [],
        "gemini_worker_run_id": "worker-run-1",
        "gemini_reviewer_run_id": "reviewer-run-1",
        "proposal_sha256": proposal_sha,
        "head_before": head_before,
        "head_after": head_after,
        "head_changed": True,
    }

    continue_raw = {
        k: continue_expected[k]
        for k in continue_expected
        if k not in {"head_before", "head_after", "head_changed"}
    }

    context = {
        "controller_run_id": run_id,
        "protected_refs": {
            "refs/heads/main": "c" * 40,
            "refs/remotes/origin/main": "d" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "e" * 40,
        },
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

    return continue_expected, continue_raw, context, upstream


class TestFileBoundAcceptedLineageFileproof(unittest.TestCase):
    def test_positive_continue_and_complete_with_whitespace_and_raw_sha(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)

            # Deliberate whitespace in result.json formatting
            raw_text = (
                "{\n"
                '  "schema_version":   "1.0",\n'
                '  "state": "CONTINUE",\n'
                '  "disposition": "NONE",\n'
                '  "category": "NONE",\n'
                '  "summary": "Cycle progressed to step 2",\n'
                f'  "commit": "{continue_exp["commit"]}",\n'
                '  "next_action": "Proceed to next step",\n'
                '  "blockers": [  ],\n'
                '  "gemini_worker_run_id": "worker-run-1",\n'
                '  "gemini_reviewer_run_id": "reviewer-run-1",\n'
                f'  "proposal_sha256": "{continue_exp["proposal_sha256"]}"\n'
                "}\n\n"
            )
            result_path = cycle_dir / "result.json"
            result_path.write_text(raw_text, encoding="utf-8")
            os.chmod(result_path, 0o644)

            raw_bytes = raw_text.encode("utf-8")
            exact_raw_sha = hashlib.sha256(raw_bytes).hexdigest()
            canonical_sha = hashlib.sha256(
                json.dumps(continue_raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

            record = build_file_bound_accepted_lineage_candidate(
                root, run_id, continue_exp, context, upstream
            )

            self.assertEqual(record["accepted_state"], "CONTINUE")
            self.assertEqual(record["result_sha256"], exact_raw_sha)
            self.assertNotEqual(record["result_sha256"], canonical_sha)
            self.assertEqual(record["head_before"], continue_exp["head_before"])
            self.assertEqual(record["head_after"], continue_exp["head_after"])
            self.assertIs(record["head_changed"], True)

            # Scenario 2: COMPLETE with unchanged head
            complete_exp = copy.deepcopy(continue_exp)
            complete_exp["state"] = "COMPLETE"
            complete_exp["head_after"] = complete_exp["head_before"]
            complete_exp["head_changed"] = False
            complete_exp["commit"] = None
            complete_exp["next_action"] = None
            complete_exp["gemini_worker_run_id"] = None
            complete_exp["gemini_reviewer_run_id"] = None
            complete_exp["proposal_sha256"] = None

            complete_raw = {
                k: complete_exp[k]
                for k in complete_exp
                if k not in {"head_before", "head_after", "head_changed"}
            }
            complete_ctx = copy.deepcopy(context)
            complete_ctx["current_head"] = complete_exp["head_before"]

            complete_raw_text = (
                "{\t\n"
                '  "schema_version": "1.0",\n'
                '  "state": "COMPLETE",\n'
                '  "disposition": "NONE",\n'
                '  "category": "NONE",\n'
                '  "summary": "Cycle progressed to step 2",\n'
                '  "commit": null,\n'
                '  "next_action": null,\n'
                '  "blockers": [],\n'
                '  "gemini_worker_run_id": null,\n'
                '  "gemini_reviewer_run_id": null,\n'
                '  "proposal_sha256": null\n'
                "}\n"
            )
            result_path.write_text(complete_raw_text, encoding="utf-8")
            os.chmod(result_path, 0o644)

            complete_record = build_file_bound_accepted_lineage_candidate(
                root, run_id, complete_exp, complete_ctx, upstream
            )
            self.assertEqual(complete_record["accepted_state"], "COMPLETE")
            self.assertIs(complete_record["head_changed"], False)
            self.assertEqual(complete_record["head_before"], complete_record["head_after"])
            self.assertEqual(
                complete_record["result_sha256"],
                hashlib.sha256(complete_raw_text.encode("utf-8")).hexdigest(),
            )

    def test_pre_io_validations(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        exp, _, ctx, ups = _make_fixtures(run_id)
        root = Path("/fake/nonexistent/root")

        # Non-Path root
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(
                "/fake/root", run_id, exp, ctx, ups  # type: ignore[arg-type]
            )

        # Non-str run ID
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(
                root, 12345, exp, ctx, ups  # type: ignore[arg-type]
            )

        # Invalid run ID regex
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(
                root, "run-invalid-format", exp, ctx, ups
            )

        # Context controller_run_id mismatch with argument
        bad_ctx = copy.deepcopy(ctx)
        bad_ctx["controller_run_id"] = "cycle-9999-20260929T120000Z-999"
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(root, run_id, exp, bad_ctx, ups)

        # Upstream continuation_controller_run_id mismatch
        bad_ups = copy.deepcopy(ups)
        bad_ups["continuation_controller_run_id"] = "cycle-9999-20260929T120000Z-999"
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(root, run_id, exp, ctx, bad_ups)

        # Non-dict expected_transition_result / context / upstream
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(root, run_id, "bad", ctx, ups)  # type: ignore[arg-type]
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(root, run_id, exp, "bad", ups)  # type: ignore[arg-type]
        with self.assertRaises(AcceptedLineageFileproofError):
            build_file_bound_accepted_lineage_candidate(root, run_id, exp, ctx, "bad")  # type: ignore[arg-type]

    def test_raw_and_expected_result_mutations_fail_closed(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        exp, raw, ctx, ups = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"

            def _write_and_test(bad_raw: dict, expected_dict: dict = exp) -> None:
                result_path.write_text(json.dumps(bad_raw), encoding="utf-8")
                os.chmod(result_path, 0o644)
                with self.assertRaises(AcceptedLineageFileproofError):
                    build_file_bound_accepted_lineage_candidate(
                        root, run_id, expected_dict, ctx, ups
                    )

            # 1. Missing raw key
            bad = copy.deepcopy(raw)
            del bad["summary"]
            _write_and_test(bad)

            # 2. Extra raw key
            bad = copy.deepcopy(raw)
            bad["extra_key"] = "forbidden"
            _write_and_test(bad)

            # 3. State mismatch between raw and expected
            bad = copy.deepcopy(raw)
            bad["state"] = "COMPLETE"
            _write_and_test(bad)

            # 4. Commit mismatch
            bad = copy.deepcopy(raw)
            bad["commit"] = "0" * 40
            _write_and_test(bad)

            # 5. Gemini evidence mismatch
            bad = copy.deepcopy(raw)
            bad["gemini_worker_run_id"] = "other-worker"
            _write_and_test(bad)

            # 6. Next action mismatch
            bad = copy.deepcopy(raw)
            bad["next_action"] = "Differing next action"
            _write_and_test(bad)

            # 7. Non-empty blockers
            bad = copy.deepcopy(raw)
            bad["blockers"] = ["some blocker"]
            _write_and_test(bad)

            # 8. Bool vs int type sensitivity in raw result
            bad = copy.deepcopy(raw)
            bad["summary"] = 12345
            _write_and_test(bad)

            # 9. Expected transition result missing 14 keys
            bad_exp = copy.deepcopy(exp)
            del bad_exp["head_changed"]
            _write_and_test(raw, bad_exp)

            # 10. Expected transition result bool-vs-int (head_changed=1)
            bad_exp = copy.deepcopy(exp)
            bad_exp["head_changed"] = 1
            _write_and_test(raw, bad_exp)

    def test_duplicate_key_symlink_hardlink_and_no_writes(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        exp, raw, ctx, ups = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"

            # 1. Duplicate JSON key in result.json
            dup_key_text = (
                "{\n"
                '  "schema_version": "1.0",\n'
                '  "schema_version": "1.0",\n'
                '  "state": "CONTINUE",\n'
                '  "disposition": "NONE",\n'
                '  "category": "NONE",\n'
                '  "summary": "Cycle progressed to step 2",\n'
                f'  "commit": "{exp["commit"]}",\n'
                '  "next_action": "Proceed to next step",\n'
                '  "blockers": [],\n'
                '  "gemini_worker_run_id": "worker-run-1",\n'
                '  "gemini_reviewer_run_id": "reviewer-run-1",\n'
                f'  "proposal_sha256": "{exp["proposal_sha256"]}"\n'
                "}\n"
            )
            result_path.write_text(dup_key_text, encoding="utf-8")
            os.chmod(result_path, 0o644)
            with self.assertRaises(AcceptedLineageFileproofError):
                build_file_bound_accepted_lineage_candidate(root, run_id, exp, ctx, ups)

            # 2. Symlink rejection
            result_path.unlink()
            real_target = cycle_dir / "target_result.json"
            real_target.write_text(json.dumps(raw), encoding="utf-8")
            os.chmod(real_target, 0o644)
            os.symlink(real_target, result_path)
            with self.assertRaises(AcceptedLineageFileproofError):
                build_file_bound_accepted_lineage_candidate(root, run_id, exp, ctx, ups)

            # 3. Hardlink rejection (st_nlink > 1)
            result_path.unlink()
            real_target.rename(result_path)
            hardlink_path = cycle_dir / "hardlink_result.json"
            os.link(result_path, hardlink_path)
            try:
                with self.assertRaises(AcceptedLineageFileproofError):
                    build_file_bound_accepted_lineage_candidate(root, run_id, exp, ctx, ups)
            finally:
                hardlink_path.unlink()

            # 4. Demonstrate fail-closed without any file creation / write side effects
            entries_before = sorted(os.listdir(cycle_dir))
            root_entries_before = sorted(os.listdir(root))
            with self.assertRaises(AcceptedLineageFileproofError):
                bad_ctx = copy.deepcopy(ctx)
                bad_ctx["current_head"] = "f" * 40
                build_file_bound_accepted_lineage_candidate(root, run_id, exp, bad_ctx, ups)

            self.assertEqual(sorted(os.listdir(cycle_dir)), entries_before)
            self.assertEqual(sorted(os.listdir(root)), root_entries_before)

    def test_mock_live_dispatch_fail_on_call(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        exp, raw, ctx, ups = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"
            result_path.write_text(json.dumps(raw), encoding="utf-8")
            os.chmod(result_path, 0o644)

            # Mock subprocess / socket to guarantee zero live authority
            with patch("subprocess.run", side_effect=AssertionError("subprocess.run forbidden")), \
                 patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen forbidden")), \
                 patch("socket.socket", side_effect=AssertionError("socket forbidden")):
                rec = build_file_bound_accepted_lineage_candidate(root, run_id, exp, ctx, ups)
                self.assertEqual(rec["accepted_state"], "CONTINUE")


if __name__ == "__main__":
    unittest.main()
