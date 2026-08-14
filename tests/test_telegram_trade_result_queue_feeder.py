import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_trade_result_queue_feeder as feeder
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TelegramTradeResultQueueFeederTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime(*, queue_owner=True):
        return SimpleNamespace(
            mode=(
                TelegramDeliveryRuntimeMode.QUEUE_V1
                if queue_owner
                else TelegramDeliveryRuntimeMode.LEGACY
            ),
            queue_worker_enabled=queue_owner,
            legacy_workers_enabled=not queue_owner,
        )

    async def test_cycle_refuses_non_queue_owner_before_database_touch(self):
        with patch(
            "core.telegram_trade_result_queue_feeder.assert_background_job_authority"
        ), patch(
            "core.telegram_trade_result_queue_feeder.configured_telegram_delivery_runtime",
            return_value=self._runtime(queue_owner=False),
        ), patch(
            "core.telegram_trade_result_queue_feeder.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                feeder.TelegramTradeResultQueueFeederOwnershipError,
                "telegram_trade_result_feeder_is_not_runtime_owner",
            ):
                await feeder.run_telegram_trade_result_queue_handoff_cycle()

        session_factory.assert_not_called()

    async def test_cycle_commits_each_handoff_and_stops_on_empty_queue(self):
        sessions = [
            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
        ]
        results = [
            SimpleNamespace(disposition="handed_off"),
            SimpleNamespace(disposition="skipped"),
            None,
        ]
        with patch(
            "core.telegram_trade_result_queue_feeder.assert_background_job_authority"
        ), patch(
            "core.telegram_trade_result_queue_feeder.configured_telegram_delivery_runtime",
            return_value=self._runtime(),
        ), patch(
            "core.telegram_trade_result_queue_feeder.AsyncSessionLocal",
            side_effect=[_SessionContext(session) for session in sessions],
        ), patch(
            "core.telegram_trade_result_queue_feeder.handoff_next_due_trade_result_receipt",
            new=AsyncMock(side_effect=results),
        ) as handoff:
            report = await feeder.run_telegram_trade_result_queue_handoff_cycle(
                limit=5
            )

        self.assertEqual(report.processed_count, 2)
        self.assertEqual(
            report.disposition_counts,
            {"handed_off": 1, "skipped": 1},
        )
        self.assertEqual(handoff.await_count, 3)
        sessions[0].commit.assert_awaited_once()
        sessions[1].commit.assert_awaited_once()
        sessions[2].rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
