"""Central poller lease stays independent from the queue owner and is monitored."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from core.telegram_central_poller_owner import (
    TELEGRAM_CENTRAL_POLLER_LOCK_KEY,
    TelegramCentralPollerAlreadyOwnedError,
    TelegramCentralPollerLease,
    TelegramCentralPollerLeaseLostError,
    acquire_telegram_central_poller_owner,
    telegram_central_poller_owner_monitor_loop,
)
from core.telegram_delivery_queue_owner import TelegramDeliveryQueueOwnerLease
from core.telegram_delivery_queue_owner import TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
import run_bot


class _FakeLease:
    def __init__(self):
        self.closed = 0
        self.held_checks = 0

    async def close(self):
        self.closed += 1

    async def assert_held(self):
        self.held_checks += 1


def _queue_runtime():
    return TelegramDeliveryRuntimeDecision(
        mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
        legacy_workers_enabled=False,
        queue_worker_enabled=True,
    )


class TelegramCentralPollerOwnerTests(unittest.TestCase):
    def test_lock_keys_stay_independent(self):
        self.assertEqual(TELEGRAM_CENTRAL_POLLER_LOCK_KEY, 0x54474250524D5259)
        self.assertEqual(TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY, 0x5447515545554531)
        self.assertNotEqual(
            TELEGRAM_CENTRAL_POLLER_LOCK_KEY,
            TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,
        )

    def test_second_acquire_is_rejected_and_connection_is_closed(self):
        async def scenario():
            engine = MagicMock()
            connection = AsyncMock()
            result = MagicMock()
            result.one.return_value = (False, 901)
            connection.execute.return_value = result
            engine.connect = AsyncMock(return_value=connection)
            with self.assertRaisesRegex(
                TelegramCentralPollerAlreadyOwnedError, "already_active"
            ):
                await acquire_telegram_central_poller_owner(engine)
            connection.close.assert_awaited_once()

        asyncio.run(scenario())

    def test_pid_change_and_lock_loss_fail_closed(self):
        async def scenario():
            connection = AsyncMock()
            lease = TelegramCentralPollerLease(connection=connection, backend_pid=11)
            changed = MagicMock()
            changed.one.return_value = (99, True)
            connection.execute.return_value = changed
            with self.assertRaisesRegex(
                TelegramCentralPollerLeaseLostError, "session_changed"
            ):
                await lease.assert_held()
            lost = MagicMock()
            lost.one.return_value = (11, False)
            connection.execute.return_value = lost
            with self.assertRaisesRegex(
                TelegramCentralPollerLeaseLostError, "lock_lost"
            ):
                await lease.assert_held()
            connection.execute.side_effect = RuntimeError("session-gone")
            with self.assertRaisesRegex(
                TelegramCentralPollerLeaseLostError, "unavailable"
            ):
                await lease.assert_held()

        asyncio.run(scenario())

    def test_monitor_stops_after_lease_loss(self):
        async def scenario():
            connection = AsyncMock()
            lease = TelegramCentralPollerLease(connection=connection, backend_pid=3)
            await lease.close()
            with self.assertRaisesRegex(
                TelegramCentralPollerLeaseLostError, "lease_closed"
            ):
                await telegram_central_poller_owner_monitor_loop(
                    lease, interval_seconds=0.01
                )

        asyncio.run(scenario())

    def test_close_is_idempotent(self):
        async def scenario():
            connection = AsyncMock()
            lease = TelegramCentralPollerLease(connection=connection, backend_pid=4)
            await lease.close()
            await lease.close()
            connection.close.assert_awaited_once()

        asyncio.run(scenario())

    def test_executor_does_not_acquire_central_poller(self):
        async def scenario():
            acquire = AsyncMock()
            queue_lease = _FakeLease()
            with ExitStack() as stack:
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
                stack.enter_context(
                    patch("run_bot.acquire_telegram_delivery_queue_owner", AsyncMock(return_value=queue_lease))
                )
                stack.enter_context(patch("run_bot.acquire_telegram_central_poller_owner", acquire))
                stack.enter_context(
                    patch("run_bot.configured_publisher_b2b_pollers", return_value=((), {}, ()))
                )
                with self.assertRaises(run_bot.BotRuntimeSurfaceError):
                    await run_bot.main()
            acquire.assert_not_awaited()

        asyncio.run(scenario())

    def test_primary_does_not_acquire_queue_owner(self):
        async def scenario():
            queue_acquire = AsyncMock()
            poller_lease = _FakeLease()
            fake_bot = MagicMock()
            fake_bot.session.close = AsyncMock()
            fake_dp = MagicMock()
            fake_dp.start_polling = AsyncMock()
            fake_dp.include_router = MagicMock()
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
                stack.enter_context(patch("run_bot.acquire_telegram_delivery_queue_owner", queue_acquire))
                stack.enter_context(
                    patch("run_bot.acquire_telegram_central_poller_owner", AsyncMock(return_value=poller_lease))
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
            queue_acquire.assert_not_awaited()
            self.assertEqual(poller_lease.closed, 1)

        asyncio.run(scenario())

    def test_second_primary_is_rejected_before_polling(self):
        async def scenario():
            bot_ctor = MagicMock()
            pollers = MagicMock()
            with ExitStack() as stack:
                stack.enter_context(patch.object(run_bot.settings, "server_mode", "foreign"))
                stack.enter_context(patch.object(run_bot.settings, "trading_bot_service", "bot"))
                stack.enter_context(patch.object(run_bot.settings, "bot_token", "token"))
                stack.enter_context(patch.object(run_bot.settings, "telegram_bot_runtime_role", "primary"))
                stack.enter_context(patch.object(run_bot.settings, "telegram_bot_split_enabled", True))
                stack.enter_context(
                    patch("run_bot.configured_telegram_delivery_runtime", return_value=_queue_runtime())
                )
                stack.enter_context(patch("run_bot.init_db", AsyncMock()))
                stack.enter_context(patch("run_bot.setup_event_listeners"))
                stack.enter_context(patch("run_bot.acquire_telegram_delivery_queue_owner", AsyncMock()))
                stack.enter_context(
                    patch(
                        "run_bot.acquire_telegram_central_poller_owner",
                        AsyncMock(
                            side_effect=TelegramCentralPollerAlreadyOwnedError(
                                "telegram_central_poller_already_active"
                            )
                        ),
                    )
                )
                stack.enter_context(patch("run_bot.Bot", bot_ctor))
                stack.enter_context(patch("run_bot.configured_publisher_b2b_pollers", pollers))
                stack.enter_context(patch("run_bot.aclose_telegram_http_client", AsyncMock()))
                with self.assertRaisesRegex(run_bot.BotRuntimeSurfaceError, "already_active"):
                    await run_bot.main()
            bot_ctor.assert_not_called()
            pollers.assert_not_called()

        asyncio.run(scenario())

    def test_all_acquires_both_owners(self):
        async def scenario():
            queue_lease = _FakeLease()
            poller_lease = _FakeLease()
            queue_acquire = AsyncMock(return_value=queue_lease)
            poller_acquire = AsyncMock(return_value=poller_lease)
            fake_bot = MagicMock()
            fake_bot.session.close = AsyncMock()
            fake_dp = MagicMock()
            fake_dp.start_polling = AsyncMock()
            fake_dp.include_router = MagicMock()
            fake_dp.update.outer_middleware = MagicMock()
            storage = MagicMock()
            storage.create_isolation.return_value = object()
            with ExitStack() as stack:
                stack.enter_context(patch.object(run_bot.settings, "server_mode", "foreign"))
                stack.enter_context(patch.object(run_bot.settings, "trading_bot_service", "bot"))
                stack.enter_context(patch.object(run_bot.settings, "bot_token", "token"))
                stack.enter_context(patch.object(run_bot.settings, "redis_url", "redis://localhost:6379/0"))
                stack.enter_context(patch.object(run_bot.settings, "telegram_bot_runtime_role", "all"))
                stack.enter_context(patch.object(run_bot.settings, "telegram_bot_split_enabled", False))
                stack.enter_context(
                    patch("run_bot.configured_telegram_delivery_runtime", return_value=_queue_runtime())
                )
                stack.enter_context(patch("run_bot.init_db", AsyncMock()))
                stack.enter_context(patch("run_bot.setup_event_listeners"))
                stack.enter_context(patch("run_bot.acquire_telegram_delivery_queue_owner", queue_acquire))
                stack.enter_context(patch("run_bot.acquire_telegram_central_poller_owner", poller_acquire))
                stack.enter_context(patch("run_bot.Bot", return_value=fake_bot))
                stack.enter_context(patch("run_bot.RedisStorage.from_url", return_value=storage))
                stack.enter_context(patch("run_bot.Dispatcher", return_value=fake_dp))
                stack.enter_context(patch("run_bot.AuthMiddleware", return_value=object()))
                stack.enter_context(patch("run_bot.listen_trade_suggestion_events", AsyncMock()))
                stack.enter_context(
                    patch("run_bot.configured_publisher_b2b_pollers", return_value=((), {}, ()))
                )
                stack.enter_context(patch("run_bot.telegram_execution_worker_factories", return_value=()))
                stack.enter_context(patch("run_bot.supervise_bot_runtime", AsyncMock()))
                stack.enter_context(patch("run_bot.aclose_telegram_http_client", AsyncMock()))
                stack.enter_context(patch("run_bot.configure_interactive_bot_command_menu", AsyncMock()))
                await run_bot.main()
            queue_acquire.assert_awaited_once()
            poller_acquire.assert_awaited_once()
            self.assertEqual(poller_lease.closed, 1)

        asyncio.run(scenario())

    def test_central_lease_loss_does_not_close_queue_owner(self):
        async def scenario():
            queue_connection = AsyncMock()
            central_connection = AsyncMock()
            queue_ok = MagicMock()
            queue_ok.one.return_value = (7, True)
            queue_connection.execute.return_value = queue_ok
            queue_lease = TelegramDeliveryQueueOwnerLease(
                connection=queue_connection, backend_pid=7
            )
            central_lease = TelegramCentralPollerLease(
                connection=central_connection, backend_pid=8
            )
            await central_lease.close()
            with self.assertRaises(TelegramCentralPollerLeaseLostError):
                await central_lease.assert_held()
            await queue_lease.assert_held()
            queue_connection.close.assert_not_awaited()

        asyncio.run(scenario())

    def test_monitor_failure_fails_supervisor(self):
        async def scenario():
            connection = AsyncMock()
            lease = TelegramCentralPollerLease(connection=connection, backend_pid=5)
            await lease.close()

            async def polling():
                await asyncio.Event().wait()

            with self.assertRaises(run_bot.BotRuntimeTaskError):
                await run_bot.supervise_bot_runtime(
                    polling_factory=polling,
                    child_factories=[
                        lambda: telegram_central_poller_owner_monitor_loop(
                            lease, interval_seconds=0.01
                        )
                    ],
                )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
