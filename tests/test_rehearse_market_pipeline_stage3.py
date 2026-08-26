import unittest
from unittest.mock import MagicMock, patch

from scripts import rehearse_market_pipeline_stage3 as rehearsal


class MarketPipelineStage3RehearsalTests(unittest.TestCase):
    def test_free_port_is_loopback_ephemeral(self):
        listener = MagicMock()
        listener.getsockname.return_value = ("127.0.0.1", 32123)
        with patch.object(rehearsal.socket, "socket", return_value=listener):
            self.assertEqual(rehearsal.free_port(), 32123)
        listener.bind.assert_called_once_with(("127.0.0.1", 0))
        listener.close.assert_called_once_with()

    def test_rehearsal_refuses_dirty_source_label(self):
        completed = rehearsal.subprocess.CompletedProcess(
            ["git"], 0, stdout=" M owned.py\n", stderr=""
        )
        with patch.object(rehearsal, "command", return_value=completed):
            with self.assertRaisesRegex(
                rehearsal.RehearsalError, "worktree_must_be_clean"
            ):
                rehearsal.git_release_sha()

    def test_source_epoch_requires_positive_commit_timestamp(self):
        invalid = rehearsal.subprocess.CompletedProcess(
            ["git"], 0, stdout="not-a-timestamp\n", stderr=""
        )
        with patch.object(rehearsal, "command", return_value=invalid):
            with self.assertRaisesRegex(
                rehearsal.RehearsalError, "source_epoch_invalid"
            ):
                rehearsal.git_source_epoch()

    def test_expected_runtime_services_exclude_database_and_migration(self):
        self.assertNotIn("market-database", rehearsal.EXPECTED_RUNTIME_WEB)
        self.assertNotIn("market-migration", rehearsal.EXPECTED_RUNTIME_WEB)
        self.assertEqual(len(rehearsal.EXPECTED_RUNTIME_WEB), 6)
        self.assertEqual(len(rehearsal.EXPECTED_RUNTIME_BOT), 4)

    def test_compose_state_summary_never_reads_container_logs(self):
        absent = rehearsal.subprocess.CompletedProcess(
            ["docker"], 0, stdout="", stderr=""
        )
        with patch.object(rehearsal, "command", return_value=absent) as runner:
            summary = rehearsal.compose_state_summary(
                "bot", "fixture-project", {}
            )
        self.assertIn("market-fact-receiver:absent", summary)
        self.assertTrue(
            all("logs" not in call.args[0] for call in runner.call_args_list)
        )

    def test_image_secret_scan_fails_closed_on_invalid_scanner_output(self):
        invalid = rehearsal.subprocess.CompletedProcess(
            ["docker"], 0, stdout="not-json", stderr=""
        )
        with patch.object(rehearsal, "command", return_value=invalid):
            with self.assertRaisesRegex(
                rehearsal.RehearsalError, "scan_output_invalid"
            ):
                rehearsal.image_secret_scan("fixture-image")

    def test_image_secret_scan_accepts_clean_filesystem_and_history(self):
        responses = [
            rehearsal.subprocess.CompletedProcess(
                ["docker"],
                0,
                stdout='{"bad_name_count": 0, "bad_content_count": 0}\n',
                stderr="",
            ),
            rehearsal.subprocess.CompletedProcess(
                ["docker"], 0, stdout="COPY fixture /app\n", stderr=""
            ),
            rehearsal.subprocess.CompletedProcess(
                ["docker"], 0, stdout="Python 3.11.14\n", stderr=""
            ),
        ]
        with patch.object(rehearsal, "command", side_effect=responses):
            result = rehearsal.image_secret_scan("fixture-image")
        self.assertEqual(result["filesystem_secret_scan"], "pass")
        self.assertEqual(result["history_secret_scan"], "pass")
        self.assertEqual(result["runtime_version"], "Python 3.11.14")


if __name__ == "__main__":
    unittest.main()
