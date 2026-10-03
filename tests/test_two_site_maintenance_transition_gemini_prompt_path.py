"""Hermetic tests for AE03 reviewed GEMINI_TASK_PROMPT.md path admission."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from scripts.two_site_maintenance_transition_record import (
    ALLOWED_CONTROL_PATHS,
    ALLOWED_MAINTENANCE_CONTROL_PATHS,
    MaintenanceTransitionRecordError,
    build_maintenance_transition_record,
    parse_maintenance_transition_record,
    serialize_maintenance_transition_record,
    validate_maintenance_transition_record,
)

GEMINI_PROMPT_PATH = "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md"
UNCHANGED_CONTROL_PATH = "scripts/two_site_codex_autopilot.py"

EXPECTED_SEVEN_CONTROL_PATHS = frozenset({
    "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
    "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md",
    "scripts/two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.sh",
    "scripts/two_site_codex_autopilot.py",
    "scripts/two_site_cycle_metrics.py",
    "scripts/two_site_cycle_tools.py",
})


def _make_valid_independent_record() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "REVIEWED_MAINTENANCE_TRANSITION",
        "prior_checkpoint_sha256": "1" * 64,
        "prior_head": "2" * 40,
        "new_repository": {
            "assignment_scope": {
                "assignment_id": "assign-ae03-208",
                "stage_id": "stage-p1-00b",
                "sha256": "7" * 64,
                "expires_at": "2026-10-04T00:00:00Z",
                "allowed_paths": [GEMINI_PROMPT_PATH, UNCHANGED_CONTROL_PATH],
                "forbidden_paths": [],
            },
            "control_blobs": {
                GEMINI_PROMPT_PATH: "a" * 40,
                UNCHANGED_CONTROL_PATH: "b" * 40,
            },
            "head": "3" * 40,
            "protected_refs": {
                "refs/heads/candidate/wa-ir-standby-v1": "4" * 40,
                "refs/heads/main": "5" * 40,
                "refs/remotes/origin/main": "6" * 40,
            },
            "status": "",
        },
        "maintenance_commits": [
            {
                "head": "3" * 40,
                "parent": "2" * 40,
                "paths": [GEMINI_PROMPT_PATH],
                "proposal_sha256": "8" * 64,
                "reviewer_run_id": "reviewer-run-ae03",
                "test_evidence_sha256": "9" * 64,
                "worker_run_id": "worker-run-ae03",
            }
        ],
    }


def _canonical_bytes(obj: Any) -> bytes:
    return (
        json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


class TestGeminiPromptPathMaintenanceTransition(unittest.TestCase):
    """Test suite proving exact admission and strict boundary guards for GEMINI_TASK_PROMPT.md."""

    def test_exact_seven_control_paths_and_alias_identity(self) -> None:
        self.assertEqual(ALLOWED_MAINTENANCE_CONTROL_PATHS, EXPECTED_SEVEN_CONTROL_PATHS)
        self.assertEqual(ALLOWED_CONTROL_PATHS, EXPECTED_SEVEN_CONTROL_PATHS)
        self.assertIs(ALLOWED_CONTROL_PATHS, ALLOWED_MAINTENANCE_CONTROL_PATHS)
        self.assertIn(GEMINI_PROMPT_PATH, ALLOWED_MAINTENANCE_CONTROL_PATHS)

    def test_four_public_codec_apis_positive_roundtrip(self) -> None:
        base_record = _make_valid_independent_record()
        expected_record = copy.deepcopy(base_record)
        expected_bytes = _canonical_bytes(expected_record)

        record_input = copy.deepcopy(base_record)

        val_res = validate_maintenance_transition_record(record_input)
        self.assertEqual(val_res, expected_record)
        self.assertEqual(record_input, base_record)

        val_res["new_repository"]["status"] = "dirty"
        self.assertEqual(record_input, base_record)

        build_res = build_maintenance_transition_record(
            prior_checkpoint_sha256=record_input["prior_checkpoint_sha256"],
            prior_head=record_input["prior_head"],
            new_repository=record_input["new_repository"],
            maintenance_commits=record_input["maintenance_commits"],
        )
        self.assertEqual(build_res, expected_record)
        self.assertEqual(record_input, base_record)

        ser_res = serialize_maintenance_transition_record(record_input)
        self.assertEqual(ser_res, expected_bytes)
        self.assertEqual(record_input, base_record)

        parse_input_bytes = bytes(expected_bytes)
        parse_res = parse_maintenance_transition_record(parse_input_bytes)
        self.assertEqual(parse_res, expected_record)
        self.assertEqual(parse_input_bytes, expected_bytes)

        self.assertIn(GEMINI_PROMPT_PATH, val_res["new_repository"]["control_blobs"])
        self.assertIn(GEMINI_PROMPT_PATH, val_res["maintenance_commits"][0]["paths"])

    def _assert_negative_row(
        self,
        base_record: dict[str, Any],
        bad_record: dict[str, Any],
        expected_error: str,
    ) -> None:
        good_before = copy.deepcopy(base_record)
        good_val_first = validate_maintenance_transition_record(base_record)
        self.assertEqual(base_record, good_before)
        good_built_first = build_maintenance_transition_record(
            prior_checkpoint_sha256=base_record["prior_checkpoint_sha256"],
            prior_head=base_record["prior_head"],
            new_repository=base_record["new_repository"],
            maintenance_commits=base_record["maintenance_commits"],
        )
        self.assertEqual(good_built_first, good_val_first)
        self.assertEqual(base_record, good_before)
        good_raw_first = serialize_maintenance_transition_record(base_record)
        self.assertEqual(base_record, good_before)
        good_parsed_first = parse_maintenance_transition_record(good_raw_first)
        self.assertEqual(good_parsed_first, good_val_first)

        bad_input_frozen = copy.deepcopy(bad_record)
        bad_canonical_bytes = _canonical_bytes(bad_record)
        bad_raw_frozen = bytes(bad_canonical_bytes)

        with self.assertRaises(MaintenanceTransitionRecordError) as ctx_val:
            validate_maintenance_transition_record(bad_record)
        self.assertEqual(str(ctx_val.exception), expected_error)
        self.assertEqual(bad_record, bad_input_frozen)

        with self.assertRaises(MaintenanceTransitionRecordError) as ctx_bld:
            build_maintenance_transition_record(
                prior_checkpoint_sha256=bad_record["prior_checkpoint_sha256"],
                prior_head=bad_record["prior_head"],
                new_repository=bad_record["new_repository"],
                maintenance_commits=bad_record["maintenance_commits"],
            )
        self.assertEqual(str(ctx_bld.exception), expected_error)
        self.assertEqual(bad_record, bad_input_frozen)

        with self.assertRaises(MaintenanceTransitionRecordError) as ctx_ser:
            serialize_maintenance_transition_record(bad_record)
        self.assertEqual(str(ctx_ser.exception), expected_error)
        self.assertEqual(bad_record, bad_input_frozen)

        with self.assertRaises(MaintenanceTransitionRecordError) as ctx_prs:
            parse_maintenance_transition_record(bad_canonical_bytes)
        self.assertEqual(str(ctx_prs.exception), expected_error)
        self.assertEqual(bad_canonical_bytes, bad_raw_frozen)

        good_val_after = validate_maintenance_transition_record(base_record)
        self.assertEqual(good_val_after, good_val_first)
        self.assertEqual(base_record, good_before)
        good_built_after = build_maintenance_transition_record(
            prior_checkpoint_sha256=base_record["prior_checkpoint_sha256"],
            prior_head=base_record["prior_head"],
            new_repository=base_record["new_repository"],
            maintenance_commits=base_record["maintenance_commits"],
        )
        self.assertEqual(good_built_after, good_val_first)
        self.assertEqual(base_record, good_before)
        good_raw_after = serialize_maintenance_transition_record(base_record)
        self.assertEqual(good_raw_after, good_raw_first)
        self.assertEqual(base_record, good_before)
        good_parsed_after = parse_maintenance_transition_record(good_raw_after)
        self.assertEqual(good_parsed_after, good_val_first)

    def test_negative_matrix_one_path_mutations(self) -> None:
        base_record = _make_valid_independent_record()

        pattern_error = "commit path does not match allowed pattern"
        glob_error = "commit path must not contain glob characters"

        cases: list[tuple[str, str]] = [
            ("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.txt", pattern_error),
            ("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md.bak", pattern_error),
            ("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md2", pattern_error),
            ("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.MD", pattern_error),
            ("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT_EXTRA.md", pattern_error),
            ("docs/refactor/two-server/GEMINI_TASK_PROMPT.md", pattern_error),
            ("docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.*", glob_error),
        ]

        for mutated_path, expected_err in cases:
            bad_record = copy.deepcopy(base_record)
            bad_record["maintenance_commits"][0]["paths"] = [mutated_path]
            self._assert_negative_row(base_record, bad_record, expected_err)

    def test_unapproved_control_path_rejected(self) -> None:
        base_record = _make_valid_independent_record()
        unapproved = "scripts/two_site_unapproved_control.py"

        bad_record = copy.deepcopy(base_record)
        bad_record["new_repository"]["control_blobs"][unapproved] = "c" * 40
        bad_record["maintenance_commits"][0]["paths"] = [unapproved]

        self._assert_negative_row(
            base_record,
            bad_record,
            "commit path cannot be a control path",
        )


if __name__ == "__main__":
    unittest.main()
