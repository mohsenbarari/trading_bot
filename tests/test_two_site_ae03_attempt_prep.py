"""Hermetic tests for offline rescope attempt preparation helper."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    ensure_rescope_epoch_dir,
    prepare_rescope_attempt,
    publish_initial_rescope_receipt,
    publish_next_rescope_receipt,
    publish_rescope_intent,
    rescope_epoch_basename,
    serialize_rescope_receipt,
)


def _make_scope(assignment_id: str = "WA-P1-00B-AE03-ATTEMPT-PREP-20260924-019") -> AssignmentScope:
    return AssignmentScope(
        assignment_id=assignment_id,
        stage_id="P1-00b",
        sha256="a" * 64,
        expires_at="2026-10-01T00:00:00Z",
        allowed_paths=(
            "scripts/two_site_codex_autopilot.py",
            "tests/test_two_site_ae03_attempt_prep.py",
        ),
        forbidden_paths=(),
    )


def _make_repository(head: str = "0" * 40) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "1" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "2" * 40 for p in CONTROL_PATHS},
        "assignment_scope": _make_scope(),
    }


def _make_rescope_result(
    head: str = "0" * 40,
    summary: str = "rescope rejection",
    next_action: str = "replan unit",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": summary,
        "commit": None,
        "next_action": next_action,
        "blockers": ["unit rejected by reviewer"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": head,
        "head_after": head,
        "head_changed": False,
    }


class TestRescopeAttemptPreparation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test-ae03-prep-")
        self.state_root = Path(self.temp_dir.name).resolve()
        os.chmod(self.state_root, 0o700)
        self.repo = _make_repository()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fresh_epoch_yields_attempt_1_and_no_recovery(self) -> None:
        epoch_dir, next_attempt, recovery = prepare_rescope_attempt(self.state_root, self.repo)
        self.assertEqual(epoch_dir, self.state_root / rescope_epoch_basename(self.repo))
        self.assertTrue(epoch_dir.is_dir())
        self.assertIs(type(next_attempt), int)
        self.assertEqual(next_attempt, 1)
        self.assertIsNone(recovery)

    def test_receipt1_advances_to_attempt_2_with_exact_recovery(self) -> None:
        epoch_dir = ensure_rescope_epoch_dir(self.state_root, self.repo)
        res_1 = _make_rescope_result(summary="rejection 1", next_action="action 1")
        publish_initial_rescope_receipt(epoch_dir, res_1, self.repo)

        out_epoch, next_attempt, recovery = prepare_rescope_attempt(self.state_root, self.repo)
        self.assertEqual(out_epoch, epoch_dir)
        self.assertIs(type(next_attempt), int)
        self.assertEqual(next_attempt, 2)
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery["attempt"], 1)
        self.assertEqual(recovery["consecutive_rescopes"], 1)
        self.assertTrue(recovery["prior_unit_rejected"])
        self.assertEqual(recovery["next_action"], "action 1")
        self.assertFalse(recovery["defer_prior_unit"])
        self.assertNotIn("deferred_unit", recovery)

        # Confirm no mutable alias to internal receipt
        recovery["blockers"].append("mutated")
        _, _, recovery_fresh = prepare_rescope_attempt(self.state_root, self.repo)
        self.assertEqual(recovery_fresh["blockers"], ["unit rejected by reviewer"])

    def test_receipt2_advances_to_attempt_3_with_deferred_unit(self) -> None:
        epoch_dir = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(
            epoch_dir, _make_rescope_result(summary="r1", next_action="a1"), self.repo
        )
        publish_next_rescope_receipt(
            epoch_dir, _make_rescope_result(summary="r2", next_action="a2"), self.repo
        )

        out_epoch, next_attempt, recovery = prepare_rescope_attempt(self.state_root, self.repo)
        self.assertEqual(out_epoch, epoch_dir)
        self.assertIs(type(next_attempt), int)
        self.assertEqual(next_attempt, 3)
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery["attempt"], 2)
        self.assertEqual(recovery["consecutive_rescopes"], 2)
        self.assertTrue(recovery["defer_prior_unit"])
        self.assertEqual(
            recovery["deferred_unit"],
            {"next_action": "a2", "blockers": ["unit rejected by reviewer"]},
        )

    def test_receipt3_terminal_fails_closed_before_intent_or_attempt4(self) -> None:
        epoch_dir = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(
            epoch_dir, _make_rescope_result(summary="r1", next_action="a1"), self.repo
        )
        publish_next_rescope_receipt(
            epoch_dir, _make_rescope_result(summary="r2", next_action="a2"), self.repo
        )
        publish_next_rescope_receipt(
            epoch_dir, _make_rescope_result(summary="r3", next_action="a3"), self.repo
        )

        with patch("scripts.two_site_codex_autopilot.rescope_intent_exists") as mock_intent:
            with self.assertRaises(AutopilotError) as ctx:
                prepare_rescope_attempt(self.state_root, self.repo)
            mock_intent.assert_not_called()

        self.assertIn("terminal", str(ctx.exception).lower())

    def test_unresolved_intent_marker_for_next_attempt_blocks_with_ambiguous(self) -> None:
        # Check fresh epoch with intent 1
        epoch_dir = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_rescope_intent(epoch_dir, 1)
        with self.assertRaises(AutopilotError) as ctx1:
            prepare_rescope_attempt(self.state_root, self.repo)
        self.assertTrue(str(ctx1.exception).startswith("ambiguous:"))

        # Check receipt 1 epoch with intent 2 (intent 1 alone should not block attempt 2)
        with tempfile.TemporaryDirectory(prefix="test-ae03-prep-2-") as tmp2:
            root2 = Path(tmp2).resolve()
            os.chmod(root2, 0o700)
            epoch_dir2 = ensure_rescope_epoch_dir(root2, self.repo)
            publish_initial_rescope_receipt(epoch_dir2, _make_rescope_result(), self.repo)
            # Prior intent 1 remaining does not block attempt 2
            publish_rescope_intent(epoch_dir2, 1)
            _, next_attempt_2, _ = prepare_rescope_attempt(root2, self.repo)
            self.assertEqual(next_attempt_2, 2)
            # But intent 2 blocks attempt 2
            publish_rescope_intent(epoch_dir2, 2)
            with self.assertRaises(AutopilotError) as ctx2:
                prepare_rescope_attempt(root2, self.repo)
            self.assertTrue(str(ctx2.exception).startswith("ambiguous:"))

        # Check receipt 2 epoch with intent 3
        with tempfile.TemporaryDirectory(prefix="test-ae03-prep-3-") as tmp3:
            root3 = Path(tmp3).resolve()
            os.chmod(root3, 0o700)
            epoch_dir3 = ensure_rescope_epoch_dir(root3, self.repo)
            publish_initial_rescope_receipt(epoch_dir3, _make_rescope_result(), self.repo)
            publish_next_rescope_receipt(epoch_dir3, _make_rescope_result(), self.repo)
            publish_rescope_intent(epoch_dir3, 3)
            with self.assertRaises(AutopilotError) as ctx3:
                prepare_rescope_attempt(root3, self.repo)
            self.assertTrue(str(ctx3.exception).startswith("ambiguous:"))

    def test_noncontiguous_or_malformed_receipts_fail_closed(self) -> None:
        epoch_dir = ensure_rescope_epoch_dir(self.state_root, self.repo)
        # Noncontiguous: receipt 2 written directly without receipt 1
        payload_2 = serialize_rescope_receipt(_make_rescope_result(), self.repo, attempt=2)
        r2_path = epoch_dir / "rescope_receipt_2.json"
        r2_path.write_bytes(payload_2)
        os.chmod(r2_path, 0o600)

        with self.assertRaises(AutopilotError) as ctx:
            prepare_rescope_attempt(self.state_root, self.repo)
        self.assertIn("non-contiguous", str(ctx.exception).lower())

        # Malformed: replace receipt 2 with corrupted JSON
        r2_path.unlink()
        r1_path = epoch_dir / "rescope_receipt.json"
        r1_path.write_bytes(b'{"corrupt": true}')
        os.chmod(r1_path, 0o600)
        with self.assertRaises(AutopilotError):
            prepare_rescope_attempt(self.state_root, self.repo)

    def test_repository_change_yields_distinct_epoch_dir(self) -> None:
        epoch_dir_1 = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(epoch_dir_1, _make_rescope_result(), self.repo)

        repo_changed = _make_repository(head="f" * 40)
        epoch_dir_2, next_attempt_2, recovery_2 = prepare_rescope_attempt(
            self.state_root, repo_changed
        )
        self.assertNotEqual(epoch_dir_1, epoch_dir_2)
        self.assertEqual(next_attempt_2, 1)
        self.assertIsNone(recovery_2)

    def test_state_remains_intact_and_no_deletion(self) -> None:
        epoch_dir = ensure_rescope_epoch_dir(self.state_root, self.repo)
        publish_initial_rescope_receipt(epoch_dir, _make_rescope_result(), self.repo)
        before_entries = set(os.listdir(epoch_dir))

        prepare_rescope_attempt(self.state_root, self.repo)

        after_entries = set(os.listdir(epoch_dir))
        self.assertEqual(before_entries, after_entries)


if __name__ == "__main__":
    unittest.main()
