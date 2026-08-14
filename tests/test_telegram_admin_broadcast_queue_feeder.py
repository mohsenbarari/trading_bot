import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_admin_broadcast_queue_feeder as feeder
from core import telegram_admin_broadcast_worker as legacy_worker
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TelegramAdminBroadcastQueueFeederTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_cycle_refuses_legacy_owner_before_database_touch(self):
        with patch(
            "core.telegram_admin_broadcast_queue_feeder.assert_background_job_authority"
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder.configured_telegram_delivery_runtime",
            return_value=self._runtime(queue_owner=False),
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                feeder.TelegramAdminBroadcastQueueFeederOwnershipError,
                "telegram_admin_broadcast_feeder_is_not_runtime_owner",
            ):
                await feeder.run_telegram_admin_broadcast_queue_handoff_cycle()
        session_factory.assert_not_called()

    async def test_legacy_cycle_refuses_queue_owner_before_database_touch(self):
        with patch(
            "core.telegram_admin_broadcast_worker.configured_telegram_delivery_runtime",
            return_value=self._runtime(queue_owner=True),
        ), patch(
            "core.telegram_admin_broadcast_worker.assert_background_job_authority"
        ) as authority, patch(
            "core.telegram_admin_broadcast_worker.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                RuntimeError,
                "legacy_admin_broadcast_worker_is_not_runtime_owner",
            ):
                await legacy_worker.run_telegram_admin_broadcast_delivery_cycle()
        authority.assert_not_called()
        session_factory.assert_not_called()

    async def test_cycle_terminalizes_invalid_rows_and_fills_available_slots(self):
        sessions = [
            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
            SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
        ]
        results = [
            SimpleNamespace(disposition="skipped"),
            SimpleNamespace(disposition="handed_off"),
            None,
        ]
        with patch(
            "core.telegram_admin_broadcast_queue_feeder.assert_background_job_authority"
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder.configured_telegram_delivery_runtime",
            return_value=self._runtime(),
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder.AsyncSessionLocal",
            side_effect=[_SessionContext(session) for session in sessions],
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder."
            "handoff_next_due_telegram_admin_broadcast_receipt",
            new=AsyncMock(side_effect=results),
        ) as handoff:
            report = await feeder.run_telegram_admin_broadcast_queue_handoff_cycle(
                limit=5
            )

        self.assertEqual(report.processed_count, 2)
        self.assertEqual(report.disposition_counts, {"skipped": 1, "handed_off": 1})
        self.assertEqual(report.active_handoff_count, 1)
        self.assertEqual(handoff.await_count, 3)
        for session in sessions[:2]:
            session.commit.assert_awaited_once()
            session.rollback.assert_not_awaited()
        sessions[2].rollback.assert_awaited_once()
        sessions[2].commit.assert_not_awaited()

    async def test_cycle_rolls_back_when_active_slot_blocks_handoff(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch(
            "core.telegram_admin_broadcast_queue_feeder.assert_background_job_authority"
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder.configured_telegram_delivery_runtime",
            return_value=self._runtime(),
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder.AsyncSessionLocal",
            return_value=_SessionContext(session),
        ), patch(
            "core.telegram_admin_broadcast_queue_feeder."
            "handoff_next_due_telegram_admin_broadcast_receipt",
            new=AsyncMock(return_value=None),
        ):
            report = await feeder.run_telegram_admin_broadcast_queue_handoff_cycle()

        self.assertEqual(report.processed_count, 0)
        self.assertEqual(report.active_handoff_count, 0)
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
