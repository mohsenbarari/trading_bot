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

    def test_expected_runtime_services_exclude_database_and_migration(self):
        self.assertNotIn("market-database", rehearsal.EXPECTED_RUNTIME_WEB)
        self.assertNotIn("market-migration", rehearsal.EXPECTED_RUNTIME_WEB)
        self.assertEqual(len(rehearsal.EXPECTED_RUNTIME_WEB), 5)
        self.assertEqual(len(rehearsal.EXPECTED_RUNTIME_BOT), 4)


if __name__ == "__main__":
    unittest.main()
