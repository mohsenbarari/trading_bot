import asyncio
from datetime import datetime, timedelta, timezone
import runpy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

import run_bot
from aiogram.methods import GetUpdates
from core.application_writer_term import ApplicationWriterTermError
from core.application_writer_term import ValidatedWriterTerm
from core.external_effect_execution_gate import ExternalEffectExecutionGateError


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

    async def test_main_refuses_before_database_or_worker_start_when_external_effect_gate_is_invalid(self):
        with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
            run_bot.settings, 'trading_bot_service', 'bot'
        ), patch.object(run_bot.settings, 'bot_token', 'token'), patch(
            'run_bot.require_external_effect_execution_authorization',
            side_effect=ExternalEffectExecutionGateError('authorization expired'),
        ), patch('run_bot.init_db', AsyncMock()) as init_db:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, 'expired'):
                await run_bot.main()

        init_db.assert_not_awaited()

    async def test_main_initializes_and_registers_all_routers(self):
        fake_bot = MagicMock()
        fake_bot.session.close = AsyncMock()
        fake_dp = MagicMock()
        fake_dp.include_router = MagicMock()
        fake_dp.start_polling = AsyncMock()
        fake_dp.update.outer_middleware = MagicMock()
        auth_middleware = object()
        navigation_middleware = object()
        trade_gate_middleware = object()
        writer_term_middleware = object()
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
            'run_bot.TradeContentionGateMiddleware', return_value=trade_gate_middleware
        ) as gate_ctor, patch(
            'run_bot.WriterTermMiddleware', return_value=writer_term_middleware
        ) as writer_term_ctor, patch(
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
        gate_ctor.assert_called_once_with()
        auth_ctor.assert_called_once_with(run_bot.AsyncSessionLocal)
        navigation_ctor.assert_called_once_with()
        writer_term_ctor.assert_called_once_with()
        self.assertEqual(fake_dp.update.outer_middleware.call_count, 5)
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[0].args[0], writer_term_middleware)
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[1].args[0], trade_gate_middleware)
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[2].args[0], auth_middleware)
        self.assertIs(fake_dp.update.outer_middleware.call_args_list[4].args[0], navigation_middleware)
        self.assertEqual(fake_dp.include_router.call_count, 14)
        fake_dp.start_polling.assert_awaited_once_with(fake_bot)
        fake_bot.session.close.assert_awaited_once()

    async def test_main_registers_bot_effect_fence_when_only_writer_term_is_enforced(self):
        fake_bot = MagicMock()
        fake_bot.session.close = AsyncMock()
        fake_dp = MagicMock()
        fake_dp.include_router = MagicMock()
        fake_dp.start_polling = AsyncMock()
        fake_dp.update.outer_middleware = MagicMock()
        storage = MagicMock()
        storage.create_isolation.return_value = object()

        with patch.object(run_bot.settings, "server_mode", "foreign"), patch.object(
            run_bot.settings, "trading_bot_service", "bot"
        ), patch.object(run_bot.settings, "bot_token", "token"), patch.object(
            run_bot.settings, "redis_url", "redis://localhost:6379/0"
        ), patch.object(run_bot.settings, "external_effect_execution_gate_enforced", False), patch.object(
            run_bot.settings, "application_writer_term_enforced", True
        ), patch("run_bot.require_external_effect_execution_authorization"), patch(
            "run_bot.init_db", AsyncMock()
        ), patch("run_bot.setup_event_listeners"), patch("run_bot.Bot", return_value=fake_bot), patch(
            "run_bot.RedisStorage.from_url", return_value=storage
        ), patch("run_bot.Dispatcher", return_value=fake_dp), patch(
            "run_bot.AuthMiddleware", return_value=object()
        ), patch("run_bot.listen_trade_suggestion_events", _listener_forever), patch(
            "run_bot.offer_telegram_publication_loop", _worker_forever
        ), patch("run_bot.telegram_trade_delivery_loop", _worker_forever), patch(
            "run_bot.telegram_admin_broadcast_delivery_loop", _worker_forever
        ), patch("run_bot.telegram_notification_outbox_delivery_loop", _worker_forever), patch(
            "run_bot._watch_active_writer_term", _worker_forever
        ):
            await run_bot.main()

        fake_bot.session.middleware.register.assert_called_once()
        effect_middleware = fake_bot.session.middleware.register.call_args.args[0]
        next_request = AsyncMock(return_value="ok")
        with patch("run_bot.require_external_effect_execution_authorization") as authorize:
            result = await effect_middleware(
                next_request,
                fake_bot,
                SimpleNamespace(__api_method__="sendMessage"),
            )

        self.assertEqual("ok", result)
        authorize.assert_called_once_with(
            run_bot.EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT
        )
        next_request.assert_awaited_once()

    async def test_bot_effect_middleware_fences_join_request_and_invite_methods(self):
        middleware = run_bot._external_effect_request_middleware()
        for method_name in (
            "approveChatJoinRequest",
            "declineChatJoinRequest",
            "createChatInviteLink",
        ):
            with self.subTest(method_name=method_name):
                next_request = AsyncMock(return_value="ok")
                with patch("run_bot.require_external_effect_execution_authorization") as authorize:
                    result = await middleware(
                        next_request,
                        MagicMock(),
                        SimpleNamespace(__api_method__=method_name),
                    )

                self.assertEqual("ok", result)
                authorize.assert_called_once_with(
                    run_bot.EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT
                )
                next_request.assert_awaited_once()

    async def test_writer_term_watchdog_stops_the_runtime_after_term_loss(self):
        with patch.object(run_bot.settings, 'application_writer_term_safety_margin_seconds', 5), patch(
            'run_bot.require_application_writer_term',
            side_effect=ApplicationWriterTermError('writer term is expired'),
        ), patch('run_bot.asyncio.sleep', new=AsyncMock()):
            with self.assertRaisesRegex(ApplicationWriterTermError, 'expired'):
                await run_bot._watch_active_writer_term()

    async def test_external_effect_watchdog_stops_after_authorization_loss(self):
        with patch(
            'run_bot.require_external_effect_execution_authorization',
            side_effect=ExternalEffectExecutionGateError('authorization expired'),
        ), patch('run_bot.asyncio.sleep', new=AsyncMock()):
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, 'expired'):
                await run_bot._watch_external_effect_execution_authorization()

    async def test_main_propagates_polling_errors_and_still_closes_bot(self):
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
            with self.assertRaisesRegex(RuntimeError, 'boom'):
                await run_bot.main()

        logger.exception.assert_called_once_with('Bot polling failed')
        fake_bot.session.close.assert_awaited_once()

    async def test_readiness_middleware_marks_only_after_a_successful_get_updates_response(self):
        marker = Path('/tmp/bot-readiness-marker-test.json')
        middleware = run_bot._writer_readiness_request_middleware(marker)
        next_request = AsyncMock(return_value=[])

        with patch('run_bot.write_writer_ready_marker') as write_marker:
            result = await middleware(next_request, MagicMock(), GetUpdates())

        self.assertEqual([], result)
        write_marker.assert_called_once_with(marker)

    async def test_readiness_middleware_never_marks_before_a_failed_get_updates_request(self):
        marker = Path('/tmp/bot-readiness-marker-test.json')
        middleware = run_bot._writer_readiness_request_middleware(marker)
        next_request = AsyncMock(side_effect=RuntimeError('telegram unavailable'))

        with patch('run_bot.write_writer_ready_marker') as write_marker:
            with self.assertRaisesRegex(RuntimeError, 'telegram unavailable'):
                await middleware(next_request, MagicMock(), GetUpdates())

        write_marker.assert_not_called()

    async def test_marker_configuration_requires_a_live_term_and_is_cleared_on_shutdown(self):
        fake_bot = MagicMock()
        fake_bot.session.close = AsyncMock()
        fake_dp = MagicMock()
        fake_dp.include_router = MagicMock()
        fake_dp.start_polling = AsyncMock()
        fake_dp.update.outer_middleware = MagicMock()
        storage = MagicMock()
        storage.create_isolation.return_value = object()
        now = datetime.now(timezone.utc)
        term = ValidatedWriterTerm(
            holder_site='webapp_fi',
            writer_epoch=4,
            lease_id='lease-4',
            issued_at=now - timedelta(seconds=5),
            expires_at=now + timedelta(seconds=55),
            witness_transition_id='transition-4',
        )
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ready.json'
            with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
                run_bot.settings, 'trading_bot_service', 'bot'
            ), patch.object(run_bot.settings, 'bot_token', 'token'), patch.object(
                run_bot.settings, 'redis_url', 'redis://localhost:6379/0'
            ), patch.object(run_bot.settings, 'bot_writer_ready_marker_path', marker), patch(
                'run_bot.require_application_writer_term', return_value=term
            ), patch('run_bot.clear_writer_ready_marker') as clear_marker, patch(
                'run_bot.init_db', AsyncMock()
            ), patch('run_bot.setup_event_listeners'), patch('run_bot.Bot', return_value=fake_bot), patch(
                'run_bot.RedisStorage.from_url', return_value=storage
            ), patch('run_bot.Dispatcher', return_value=fake_dp), patch(
                'run_bot.AuthMiddleware', return_value=object()
            ), patch('run_bot.listen_trade_suggestion_events', _listener_forever), patch(
                'run_bot.offer_telegram_publication_loop', _worker_forever
            ), patch('run_bot.telegram_trade_delivery_loop', _worker_forever), patch(
                'run_bot.telegram_admin_broadcast_delivery_loop', _worker_forever
            ), patch('run_bot.telegram_notification_outbox_delivery_loop', _worker_forever):
                await run_bot.main()

        self.assertEqual([call(marker), call(marker)], clear_marker.call_args_list)
        fake_bot.session.middleware.register.assert_called_once()

    async def test_marker_configuration_refuses_startup_when_the_term_policy_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ready.json'
            with patch.object(run_bot.settings, 'server_mode', 'foreign'), patch.object(
                run_bot.settings, 'trading_bot_service', 'bot'
            ), patch.object(run_bot.settings, 'bot_token', 'token'), patch.object(
                run_bot.settings, 'bot_writer_ready_marker_path', marker), patch(
                'run_bot.require_application_writer_term', return_value=None
            ), patch('run_bot.clear_writer_ready_marker') as clear_marker, patch(
                'run_bot.init_db', AsyncMock()
            ) as init_db:
                with self.assertRaisesRegex(run_bot.BotWriterReadinessError, 'requires enabled'):
                    await run_bot.main()

        clear_marker.assert_called_once_with(marker)
        init_db.assert_not_awaited()

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
