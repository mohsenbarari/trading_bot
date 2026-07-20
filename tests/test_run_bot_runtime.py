import asyncio
import runpy
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import run_bot


async def _listener_forever(_bot):
    await asyncio.sleep(3600)


async def _worker_forever():
    await asyncio.sleep(3600)


class RunBotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_fails_closed_without_bot_token(self):
        with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
            run_bot.settings, 'trading_bot_service', 'bot'
        ), patch.object(run_bot.settings, 'bot_token', None), patch('run_bot.init_db', AsyncMock()) as init_db:
            with self.assertRaises(run_bot.BotRuntimeSurfaceError) as exc_info:
                await run_bot.main()

        init_db.assert_not_awaited()
        self.assertIn('BOT_TOKEN is required', str(exc_info.exception))

    async def test_main_fails_closed_on_iran_mode_even_with_bot_token(self):
        with patch.object(run_bot.settings, 'server_mode', 'iran'), patch.object(
            run_bot.settings, 'trading_bot_service', 'bot'
        ), patch.object(run_bot.settings, 'bot_token', 'token'), patch('run_bot.init_db', AsyncMock()) as init_db:
            with self.assertRaises(run_bot.BotRuntimeSurfaceError) as exc_info:
                await run_bot.main()

        init_db.assert_not_awaited()
        self.assertIn('SERVER_MODE must be foreign', str(exc_info.exception))

    async def test_main_fails_closed_without_explicit_bot_service_identity(self):
        with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
            run_bot.settings, 'trading_bot_service', 'app'
        ), patch.object(run_bot.settings, 'bot_token', 'token'), patch('run_bot.init_db', AsyncMock()) as init_db:
            with self.assertRaises(run_bot.BotRuntimeSurfaceError) as exc_info:
                await run_bot.main()

        init_db.assert_not_awaited()
        self.assertIn('TRADING_BOT_SERVICE must be bot', str(exc_info.exception))

    async def test_main_initializes_and_registers_all_routers(self):
        fake_bot = MagicMock()
        fake_bot.session.close = AsyncMock()
        fake_dp = MagicMock()
        fake_dp.include_router = MagicMock()
        fake_dp.start_polling = AsyncMock()
        fake_dp.update.outer_middleware = MagicMock()
        auth_middleware = object()
        callback_receipt_middleware = object()
        navigation_middleware = object()
        trade_gate_middleware = object()
        storage = MagicMock()
        event_isolation = object()
        storage.create_isolation.return_value = event_isolation

        with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
            run_bot.settings, 'trading_bot_service', 'bot'
        ), patch.object(run_bot.settings, 'bot_token', 'token'), patch.object(
            run_bot.settings, 'redis_url', 'redis://localhost:6379/0'
        ), patch('run_bot.init_db', AsyncMock()) as init_db, patch(
            'run_bot.setup_event_listeners'
        ) as setup_event_listeners, patch('run_bot.Bot', return_value=fake_bot), patch(
            'run_bot.RedisStorage.from_url', return_value=storage
        ) as storage_from_url, patch('run_bot.Dispatcher', return_value=fake_dp) as dispatcher_ctor, patch(
            'run_bot.AuthMiddleware', return_value=auth_middleware
        ) as auth_ctor, patch(
            'run_bot.CallbackReceiptMiddleware', return_value=callback_receipt_middleware
        ) as callback_receipt_ctor, patch(
            'run_bot.TradeContentionGateMiddleware', return_value=trade_gate_middleware
        ) as gate_ctor, patch(
            'run_bot.StaleNavigationHandoffMiddleware', return_value=navigation_middleware
        ) as navigation_ctor, patch('run_bot.listen_trade_suggestion_events', _listener_forever), patch(
            'run_bot.offer_telegram_publication_loop', _worker_forever
        ), patch(
            'run_bot.telegram_trade_delivery_loop', _worker_forever
        ), patch(
            'run_bot.telegram_admin_broadcast_delivery_loop', _worker_forever
        ), patch(
            'run_bot.telegram_notification_outbox_delivery_loop', _worker_forever
        ):
            await run_bot.main()

        init_db.assert_awaited_once()
        setup_event_listeners.assert_called_once_with()
        storage_from_url.assert_called_once_with('redis://localhost:6379/0')
        storage.create_isolation.assert_called_once_with(lock_kwargs={"timeout": 120})
        dispatcher_ctor.assert_called_once_with(
            storage=storage,
            events_isolation=event_isolation,
        )
        callback_receipt_ctor.assert_called_once_with()
        gate_ctor.assert_called_once_with()
        auth_ctor.assert_called_once_with(run_bot.AsyncSessionLocal)
        navigation_ctor.assert_called_once_with()
        self.assertEqual(fake_dp.update.outer_middleware.call_count, 5)
        self.assertIs(
            fake_dp.update.outer_middleware.call_args_list[0].args[0],
            callback_receipt_middleware,
        )
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[1].args[0], trade_gate_middleware)
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[2].args[0], auth_middleware)
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[4].args[0], navigation_middleware)
        self.assertEqual(fake_dp.include_router.call_count, 14)
        fake_dp.start_polling.assert_awaited_once_with(fake_bot)
        fake_bot.session.close.assert_awaited_once()

    async def test_main_logs_polling_errors_and_still_closes_bot(self):
        fake_bot = MagicMock()
        fake_bot.session.close = AsyncMock()
        fake_dp = MagicMock()
        fake_dp.include_router = MagicMock()
        fake_dp.start_polling = AsyncMock(side_effect=RuntimeError('boom'))
        fake_dp.update.outer_middleware = MagicMock()

        storage = MagicMock()
        storage.create_isolation.return_value = object()
        with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
            run_bot.settings, 'trading_bot_service', 'bot'
        ), patch.object(run_bot.settings, 'bot_token', 'token'), patch.object(
            run_bot.settings, 'redis_url', 'redis://localhost:6379/0'
        ), patch('run_bot.init_db', AsyncMock()), patch('run_bot.setup_event_listeners'), patch(
            'run_bot.Bot', return_value=fake_bot
        ), patch('run_bot.RedisStorage.from_url', return_value=storage), patch(
            'run_bot.Dispatcher', return_value=fake_dp
        ), patch('run_bot.AuthMiddleware', return_value=object()), patch(
            'run_bot.listen_trade_suggestion_events', _listener_forever
        ), patch('run_bot.offer_telegram_publication_loop', _worker_forever), patch(
            'run_bot.telegram_trade_delivery_loop', _worker_forever
        ), patch('run_bot.telegram_admin_broadcast_delivery_loop', _worker_forever), patch(
            'run_bot.telegram_notification_outbox_delivery_loop', _worker_forever
        ), patch.object(run_bot, 'logger') as logger:
            await run_bot.main()

        logger.error.assert_called_once()
        fake_bot.session.close.assert_awaited_once()

    async def test_main_module_logs_stop_message_on_keyboard_interrupt(self):
        fake_logger = MagicMock()

        def interrupting_run(coro):
            coro.close()
            raise KeyboardInterrupt

        with patch('asyncio.run', side_effect=interrupting_run), patch('logging.getLogger', return_value=fake_logger):
            runpy.run_module('run_bot', run_name='__main__')

        fake_logger.info.assert_called_with('Bot stopped!')


if __name__ == '__main__':
    unittest.main()
