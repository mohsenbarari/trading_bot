import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.services.telegram_publisher_dispatch_service import (
    TelegramPublisherDispatchCycleReport,
)
from run_bot import (
    configured_publisher_dispatch_worker_factory,
    publisher_b2b_dispatch_cycle_sleep_seconds,
)


class PublisherB2BDispatchCadenceTests(unittest.TestCase):
    def test_partial_batch_accounts_for_network_time_inside_cadence(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=1,
                elapsed_seconds=0.3,
                batch_limit=8,
            ),
            0.2,
        )


class CoLocatedPublisherDispatchRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_acknowledges_durable_handoff_without_gateway_io(self):
        publisher_lanes = {
            f"publisher_{index}": SimpleNamespace(expected_bot_id=1000 + index)
            for index in range(1, 6)
        }
        settings = SimpleNamespace(
            telegram_multi_publisher_enabled=True,
            telegram_b2b_dispatch_enabled=True,
            telegram_delivery_queue_worker_lease_seconds=30.0,
            telegram_b2b_dispatch_interval_seconds=0.5,
        )
        report = TelegramPublisherDispatchCycleReport(
            claimed_count=1,
            sent_count=1,
            retry_due_count=0,
        )

        with (
            patch(
                "run_bot.build_configured_telegram_delivery_runtime",
                return_value=SimpleNamespace(
                    credential_registry=SimpleNamespace(
                        publisher_lanes=publisher_lanes,
                    )
                ),
            ),
            patch(
                "run_bot.run_co_located_telegram_publisher_dispatch_cycle",
                new=AsyncMock(return_value=report),
            ) as cycle,
            patch("run_bot.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            factory = configured_publisher_dispatch_worker_factory(
                settings,
                publisher_bot_ids={
                    identity: lane.expected_bot_id
                    for identity, lane in publisher_lanes.items()
                },
            )
            self.assertIsNotNone(factory)
            with self.assertRaises(asyncio.CancelledError):
                await factory()

        cycle.assert_awaited_once()
        self.assertNotIn("gateway_call", cycle.await_args.kwargs)
        self.assertEqual(cycle.await_args.kwargs["limit"], 8)


class PublisherB2BDispatchBatchCadenceTests(unittest.TestCase):
    def test_slow_claimed_cycle_does_not_add_another_full_interval(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=1,
                elapsed_seconds=0.8,
                batch_limit=8,
            ),
            0.0,
        )

    def test_idle_cycle_keeps_the_same_cadence(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=0,
                elapsed_seconds=0.3,
                batch_limit=8,
            ),
            0.2,
        )

    def test_full_batch_drains_without_waiting_for_the_idle_interval(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=8,
                elapsed_seconds=0.1,
                batch_limit=8,
            ),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
