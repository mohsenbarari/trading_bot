"""Prove the global queue owner stays one lock and is taken before polling."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from core.telegram_delivery_queue_owner import (
    TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,
    TelegramDeliveryQueueAlreadyOwnedError,
    acquire_telegram_delivery_queue_owner,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
import run_bot


class _FakeLease:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


def _queue_runtime():
    return TelegramDeliveryRuntimeDecision(
        mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
        legacy_workers_enabled=False,
        queue_worker_enabled=True,
    )


class TelegramSplitRuntimeOwnerGuardTests(unittest.TestCase):
    def test_lock_key_is_still_the_global_queue_owner(self):
        self.assertEqual(TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY, 0x5447515545554531)

    def test_second_acquire_is_rejected_and_connection_is_closed(self):
        async def scenario():
            engine = MagicMock()
            connection = AsyncMock()
            result = MagicMock()
            result.one.return_value = (False, 801)
            connection.execute.return_value = result
            engine.connect = AsyncMock(return_value=connection)
            with self.assertRaisesRegex(
                TelegramDeliveryQueueAlreadyOwnedError, "already_active"
            ):
                await acquire_telegram_delivery_queue_owner(engine)
            connection.close.assert_awaited_once()

        asyncio.run(scenario())

    def _executor_settings(self, stack: ExitStack) -> None:
        stack.enter_context(patch.object(run_bot.settings, "server_mode", "foreign"))
        stack.enter_context(patch.object(run_bot.settings, "trading_bot_service", "bot"))
        stack.enter_context(patch.object(run_bot.settings, "bot_token", "token"))
        stack.enter_context(patch.object(run_bot.settings, "telegram_bot_runtime_role", "executor"))
        stack.enter_context(patch.object(run_bot.settings, "telegram_bot_split_enabled", True))
        stack.enter_context(patch.object(run_bot.settings, "telegram_multi_publisher_enabled", True))
        stack.enter_context(patch.object(run_bot.settings, "telegram_b2b_dispatch_enabled", True))
        stack.enter_context(
            patch("run_bot.configured_telegram_delivery_runtime", return_value=_queue_runtime())
        )
        stack.enter_context(patch("run_bot.init_db", AsyncMock()))
        stack.enter_context(patch("run_bot.setup_event_listeners"))

    def test_executor_startup_acquires_owner_before_publisher_pollers(self):
        async def scenario():
            order: list[str] = []
            lease = _FakeLease()

            async def acquire():
                order.append("acquire")
                return lease

            def pollers(_settings):
                order.append("pollers")
                return ((), {}, ())

            with ExitStack() as stack:
                self._executor_settings(stack)
                stack.enter_context(
                    patch("run_bot.acquire_telegram_delivery_queue_owner", side_effect=acquire)
                )
                stack.enter_context(
                    patch("run_bot.configured_publisher_b2b_pollers", side_effect=pollers)
                )
                stack.enter_context(patch("run_bot.configured_publisher_b2b_lane_ids", return_value={}))
                stack.enter_context(patch("run_bot.telegram_execution_worker_factories", return_value=()))
                stack.enter_context(patch("run_bot.supervise_bot_runtime", AsyncMock()))
                stack.enter_context(patch("run_bot.aclose_telegram_http_client", AsyncMock()))
                with self.assertRaises(run_bot.BotRuntimeSurfaceError):
                    await run_bot.main()
            self.assertEqual(order, ["acquire", "pollers"])
            self.assertEqual(lease.closed, 1)

        asyncio.run(scenario())

    def test_second_executor_fails_before_pollers_and_closes_nothing_extra(self):
        async def scenario():
            pollers = MagicMock(return_value=((), {}, ()))
            factories = MagicMock()
            with ExitStack() as stack:
                self._executor_settings(stack)
                stack.enter_context(
                    patch(
                        "run_bot.acquire_telegram_delivery_queue_owner",
                        AsyncMock(
                            side_effect=TelegramDeliveryQueueAlreadyOwnedError("already_active")
                        ),
                    )
                )
                stack.enter_context(patch("run_bot.configured_publisher_b2b_pollers", pollers))
                stack.enter_context(patch("run_bot.telegram_execution_worker_factories", factories))
                with self.assertRaises(run_bot.BotRuntimeSurfaceError) as exc:
                    await run_bot.main()
            self.assertIn("already_active", str(exc.exception))
            pollers.assert_not_called()
            factories.assert_not_called()

        asyncio.run(scenario())

    def test_primary_never_calls_queue_owner_acquire(self):
        async def scenario():
            acquire = AsyncMock()
            poller_lease = _FakeLease()
            fake_bot = MagicMock()
            fake_bot.session.close = AsyncMock()
            fake_bot.set_my_commands = AsyncMock(return_value=True)
            fake_bot.set_chat_menu_button = AsyncMock(return_value=True)
            fake_dp = MagicMock()
            fake_dp.include_router = MagicMock()
            fake_dp.start_polling = AsyncMock()
            fake_dp.update.outer_middleware = MagicMock()
            storage = MagicMock()
            storage.create_isolation.return_value = object()
            with ExitStack() as stack:
                stack.enter_context(patch.object(run_bot.settings, "server_mode", "foreign"))
                stack.enter_context(patch.object(run_bot.settings, "trading_bot_service", "bot"))
                stack.enter_context(patch.object(run_bot.settings, "bot_token", "token"))
                stack.enter_context(patch.object(run_bot.settings, "redis_url", "redis://localhost:6379/0"))
                stack.enter_context(patch.object(run_bot.settings, "telegram_bot_runtime_role", "primary"))
                stack.enter_context(patch.object(run_bot.settings, "telegram_bot_split_enabled", True))
                stack.enter_context(
                    patch("run_bot.configured_telegram_delivery_runtime", return_value=_queue_runtime())
                )
                stack.enter_context(patch("run_bot.init_db", AsyncMock()))
                stack.enter_context(patch("run_bot.setup_event_listeners"))
                stack.enter_context(patch("run_bot.acquire_telegram_delivery_queue_owner", acquire))
                stack.enter_context(
                    patch(
                        "run_bot.acquire_telegram_central_poller_owner",
                        AsyncMock(return_value=poller_lease),
                    )
                )
                stack.enter_context(
                    patch("run_bot.telegram_delivery_queue_owner_is_held", AsyncMock(return_value=True))
                )
                stack.enter_context(patch("run_bot.Bot", return_value=fake_bot))
                stack.enter_context(patch("run_bot.RedisStorage.from_url", return_value=storage))
                stack.enter_context(patch("run_bot.Dispatcher", return_value=fake_dp))
                stack.enter_context(patch("run_bot.AuthMiddleware", return_value=object()))
                stack.enter_context(patch("run_bot.listen_trade_suggestion_events", AsyncMock()))
                stack.enter_context(patch("run_bot.supervise_bot_runtime", AsyncMock()))
                stack.enter_context(patch("run_bot.aclose_telegram_http_client", AsyncMock()))
                stack.enter_context(patch("run_bot.configure_interactive_bot_command_menu", AsyncMock()))
                await run_bot.main()
            acquire.assert_not_awaited()
            self.assertEqual(poller_lease.closed, 1)

        asyncio.run(scenario())

    def test_startup_failure_after_acquire_closes_lease_once(self):
        async def scenario():
            lease = _FakeLease()
            with ExitStack() as stack:
                self._executor_settings(stack)
                stack.enter_context(
                    patch("run_bot.acquire_telegram_delivery_queue_owner", AsyncMock(return_value=lease))
                )
                stack.enter_context(
                    patch(
                        "run_bot.configured_publisher_b2b_pollers",
                        side_effect=RuntimeError("startup-failed"),
                    )
                )
                with self.assertRaisesRegex(RuntimeError, "startup-failed"):
                    await run_bot.main()
            self.assertEqual(lease.closed, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
