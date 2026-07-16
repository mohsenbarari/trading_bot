import unittest
from unittest.mock import AsyncMock, patch

from core.telegram_delivery_queue_worker import (
    TelegramDeliveryQueueImplementationIncompleteError,
    run_telegram_delivery_queue_cycle,
    telegram_delivery_queue_loop,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


class TelegramDeliveryQueueWorkerSafetyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )

    async def test_cycle_without_authoritative_freshness_adapter_refuses_before_db_touch(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory, patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "authoritative_freshness_validator_not_installed",
            ):
                await run_telegram_delivery_queue_cycle(gateway_call=AsyncMock())

        session_factory.assert_not_called()

    async def test_cycle_refuses_before_db_when_queue_is_not_runtime_owner(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.LEGACY,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueImplementationIncompleteError,
                "queue_worker_is_not_runtime_owner",
            ):
                await run_telegram_delivery_queue_cycle(
                    freshness_validator=AsyncMock(),
                    gateway_call=AsyncMock(),
                )

        session_factory.assert_not_called()

    async def test_runtime_loop_fails_before_task_work_when_queue_cannot_own_execution(self):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            side_effect=TelegramDeliveryRuntimeConfigurationError(
                "queue_implementation_not_cutover_ready"
            ),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal"
        ) as session_factory:
            with self.assertRaisesRegex(
                TelegramDeliveryRuntimeConfigurationError,
                "queue_implementation_not_cutover_ready",
            ):
                await telegram_delivery_queue_loop()

        session_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
