import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import trade_delivery_worker as worker


class FakeSession:
    def __init__(self):
        self.commit = AsyncMock()


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TradeDeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_webapp_cycle_recovers_expired_leases_and_processes_until_empty(self):
        sessions = [FakeSession(), FakeSession(), FakeSession()]
        result_sent = SimpleNamespace(status="sent")
        result_empty = SimpleNamespace(status=worker.WEBAPP_DELIVERY_STATUS_NO_RECEIPT)

        with patch("core.trade_delivery_worker.assert_background_job_authority") as authority_mock, patch(
            "core.trade_delivery_worker.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
        ), patch(
            "core.trade_delivery_worker.AsyncSessionLocal",
            side_effect=[FakeSessionContext(session) for session in sessions],
        ), patch(
            "core.trade_delivery_worker.recover_expired_local_leases",
            new=AsyncMock(return_value=[object()]),
        ) as recover_mock, patch(
            "core.trade_delivery_worker.claim_and_deliver_next_webapp_receipt",
            new=AsyncMock(side_effect=[result_sent, result_empty]),
        ) as claim_mock, patch(
            "core.trade_delivery_worker.current_server",
            return_value="iran",
        ):
            report = await worker.run_webapp_trade_delivery_cycle(limit=5)

        authority_mock.assert_called_once_with("trade_webapp_delivery")
        recover_mock.assert_awaited_once()
        sessions[0].commit.assert_awaited_once()
        self.assertEqual(claim_mock.await_count, 2)
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.recovered_lease_count, 1)
        self.assertEqual(report.status_counts["sent"], 1)
        self.assertEqual(report.status_counts[worker.WEBAPP_DELIVERY_STATUS_NO_RECEIPT], 1)

    async def test_telegram_cycle_stops_when_no_receipt_is_due(self):
        sessions = [FakeSession(), FakeSession()]
        result_empty = SimpleNamespace(status=worker.TELEGRAM_DELIVERY_STATUS_NO_RECEIPT)

        with patch("core.trade_delivery_worker.assert_background_job_authority") as authority_mock, patch(
            "core.trade_delivery_worker.AsyncSessionLocal",
            side_effect=[FakeSessionContext(session) for session in sessions],
        ), patch(
            "core.trade_delivery_worker.recover_expired_local_leases",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.trade_delivery_worker.claim_and_deliver_next_telegram_receipt",
            new=AsyncMock(return_value=result_empty),
        ) as claim_mock, patch(
            "core.trade_delivery_worker.current_server",
            return_value="foreign",
        ):
            report = await worker.run_telegram_trade_delivery_cycle(limit=5)

        authority_mock.assert_called_once_with("trade_telegram_delivery")
        sessions[0].commit.assert_not_awaited()
        claim_mock.assert_awaited_once()
        self.assertEqual(report.processed_count, 0)
        self.assertEqual(report.recovered_lease_count, 0)
        self.assertEqual(report.status_counts[worker.TELEGRAM_DELIVERY_STATUS_NO_RECEIPT], 1)

    async def test_telegram_cycle_refuses_queue_ownership_before_database_touch(self):
        with patch(
            "core.trade_delivery_worker.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            ),
        ), patch(
            "core.trade_delivery_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.trade_delivery_worker.assert_background_job_authority"
        ) as authority_mock:
            with self.assertRaisesRegex(
                worker.TelegramDeliveryRuntimeConfigurationError,
                "legacy_telegram_worker_is_not_runtime_owner",
            ):
                await worker.run_telegram_trade_delivery_cycle(limit=1)

        session_factory.assert_not_called()
        authority_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
