from __future__ import annotations

from datetime import datetime, timezone
import subprocess
import unittest
from unittest.mock import patch

from scripts import rehearse_market_capture_stage4 as rehearsal


class MarketCaptureStage4RehearsalTests(unittest.TestCase):
    def test_rehearsal_requires_clean_worktree(self):
        dirty = subprocess.CompletedProcess(
            ["git"], 0, stdout=" M owned.py\n", stderr=""
        )
        with patch.object(rehearsal, "command", return_value=dirty):
            with self.assertRaisesRegex(
                rehearsal.Stage4RehearsalError, "worktree_must_be_clean"
            ):
                rehearsal.git_release_sha()

    def test_fixture_matrix_covers_both_accounts_and_duplicates(self):
        documents = rehearsal.fixture_documents(
            datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(len(documents["account1"]), 8)
        self.assertEqual(len({row["event_id"] for row in documents["account1"]}), 7)
        self.assertEqual(len(documents["account2"]), 7)
        self.assertEqual(len({row["event_id"] for row in documents["account2"]}), 6)
        self.assertIn(
            "message_deleted", {row["event_type"] for row in documents["account2"]}
        )
        self.assertIn(
            "message_edited", {row["event_type"] for row in documents["account1"]}
        )

    def test_command_reports_only_label_and_return_code(self):
        failed = subprocess.CompletedProcess(
            ["docker"], 9, stdout="raw fixture", stderr="secret fixture"
        )
        with patch.object(rehearsal.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(
                rehearsal.Stage4RehearsalError, "safe_label_failed_rc_9"
            ) as raised:
                rehearsal.command(["docker"], label="safe_label")
        self.assertNotIn("raw fixture", str(raised.exception))
        self.assertNotIn("secret fixture", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
