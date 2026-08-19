"""Safety contracts for the staging Queue-v1 smoke harness."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.telegram_delivery_queue_contract import TelegramDeliveryState
from scripts import smoke_telegram_delivery_queue_staging as smoke


class PrivateProbeClassificationTests(unittest.TestCase):
    def test_sent_probe_is_the_only_provider_delivery_claim(self):
        report = smoke._private_probe_terminal_report(
            SimpleNamespace(status="sent"),
            [
                SimpleNamespace(
                    state=TelegramDeliveryState.SENT,
                    outcome_reason="telegram_sent",
                    provider_attempt_count=1,
                )
            ],
        )

        self.assertEqual(report["queue_terminal_outcome"], "sent")
        self.assertTrue(report["provider_delivery_proven"])

    def test_expected_synthetic_quarantine_is_not_reported_as_delivery(self):
        report = smoke._private_probe_terminal_report(
            SimpleNamespace(status="pending"),
            [
                SimpleNamespace(
                    state=TelegramDeliveryState.QUARANTINED,
                    outcome_reason="telegram_unknown_client_error",
                    provider_attempt_count=1,
                )
            ],
        )

        self.assertEqual(
            report["queue_terminal_outcome"],
            "synthetic_recipient_quarantined",
        )
        self.assertFalse(report["provider_delivery_proven"])
        self.assertEqual(report["outbox_status"], "pending")

    def test_unexpected_terminal_outcome_fails_closed(self):
        with self.assertRaisesRegex(
            smoke.StagingSmokeError,
            "private_notification_unexpected_terminal",
        ):
            smoke._private_probe_terminal_report(
                SimpleNamespace(status="pending"),
                [
                    SimpleNamespace(
                        state=TelegramDeliveryState.TERMINAL_FAILED,
                        outcome_reason="unexpected",
                        provider_attempt_count=1,
                    )
                ],
            )


class SmokeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_scenario_still_cleans_and_writes_blocked_receipt(self):
        cleanup = AsyncMock(return_value={"status": "ok", "deleted_users": 3})
        with tempfile.TemporaryDirectory() as directory, patch.object(
            smoke,
            "_validate_smoke_preconditions",
        ), patch.object(
            smoke,
            "_run_smoke_scenario",
            new=AsyncMock(side_effect=smoke.StagingSmokeError("synthetic_failure")),
        ), patch.object(
            smoke,
            "cleanup_prefix",
            new=cleanup,
        ):
            with self.assertRaisesRegex(smoke.StagingSmokeError, "synthetic_failure"):
                await smoke.run_smoke(
                    confirm=smoke.CONFIRM,
                    artifact_dir=Path(directory),
                )

            cleanup.assert_awaited_once()
            artifact_paths = list(Path(directory).glob("telegram-queue-smoke-*.json"))
            self.assertEqual(len(artifact_paths), 1)
            payload = artifact_paths[0].read_text()
            self.assertIn('"status": "blocked"', payload)
            self.assertIn('"error_code": "synthetic_failure"', payload)
            self.assertIn('"cleanup"', payload)


if __name__ == "__main__":
    unittest.main()
