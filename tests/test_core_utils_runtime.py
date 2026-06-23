import asyncio
import importlib
import typing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest

from core import utils
from core.enums import NotificationCategory, NotificationLevel


class _RedisContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _HttpResponse:
    def __init__(self, status_code=200, text='ok'):
        self.status_code = status_code
        self.text = text


class _HttpClientContext:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.post = AsyncMock(side_effect=self._post)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def _post(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


class CoreUtilsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_timezone_and_jalali_helpers(self):
        now = utils.utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.tzinfo, timezone.utc)

        iran_now = utils.get_iran_time()
        self.assertIn('Tehran', str(iran_now.tzinfo))
        self.assertIsNone(utils.to_iran_time(None))

        naive_utc = datetime(2025, 1, 2, 12, 0)
        iran_dt = utils.to_iran_time(naive_utc)
        self.assertIsNotNone(iran_dt.tzinfo)
        self.assertEqual(utils.format_iran_datetime(naive_utc), iran_dt.strftime('%Y-%m-%d %H:%M'))
        self.assertEqual(utils.format_iran_datetime(naive_utc, include_time=False), iran_dt.strftime('%Y-%m-%d'))
        self.assertEqual(utils.format_iran_datetime(None), '---')

        jalali = utils.to_jalali_str(naive_utc)
        self.assertIsInstance(jalali, str)
        self.assertIsNone(utils.to_jalali_str(None))
        parsed = utils.parse_jalali_str(jalali)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertIsNone(utils.parse_jalali_str(''))
        self.assertIsNone(utils.parse_jalali_str('broken'))

    async def test_utils_reload_with_type_checking_enabled_executes_type_only_import(self):
        original = typing.TYPE_CHECKING
        try:
            typing.TYPE_CHECKING = True
            reloaded = importlib.reload(utils)
            self.assertIn('User', reloaded.__dict__)
        finally:
            typing.TYPE_CHECKING = original
            importlib.reload(utils)

    async def test_normalization_helpers(self):
        self.assertEqual(utils.normalize_persian_numerals('۱۲٣abc'), '123abc')
        self.assertEqual(utils.normalize_account_name('آزمایشی۱۲'), 'آزمایشی12')
        self.assertEqual(utils.normalize_account_name(''), '')
        self.assertEqual(utils.unique_user_ids([5, '6', 5, 0, None, 'bad', 7]), [5, 6, 7])

    async def test_send_deletable_message_success_and_failure(self):
        bot = AsyncMock()
        sent_message = AsyncMock()
        bot.send_message = AsyncMock(return_value=sent_message)

        with patch('core.utils.asyncio.create_task') as create_task:
            create_task.side_effect = lambda coro: coro.close()
            await utils.send_deletable_message(bot, 1, 'hello', delay_seconds=5, parse_mode='HTML')

        bot.send_message.assert_awaited_once_with(1, 'hello', parse_mode='HTML')
        create_task.assert_called_once()

        bot.send_message = AsyncMock(side_effect=TelegramBadRequest(method='sendMessage', message='denied'))
        with patch.object(utils, 'logger') as logger:
            await utils.send_deletable_message(bot, 1, 'hello')
        logger.warning.assert_called_once()

    async def test_send_deletable_message_delete_task_swallows_delete_errors(self):
        bot = AsyncMock()
        sent_message = AsyncMock()
        sent_message.delete = AsyncMock(side_effect=TelegramBadRequest(method='deleteMessage', message='denied'))
        bot.send_message = AsyncMock(return_value=sent_message)
        created = {}

        def capture_task(coro):
            created['coro'] = coro
            return None

        with patch('core.utils.asyncio.create_task', side_effect=capture_task), patch(
            'core.utils.asyncio.sleep', new=AsyncMock()
        ):
            await utils.send_deletable_message(bot, 1, 'hello')
            await created['coro']

        sent_message.delete.assert_awaited_once()

    async def test_send_telegram_notification_paths(self):
        with patch('core.utils.os.getenv', return_value=None), patch.object(utils, 'logger') as logger:
            self.assertFalse(await utils.send_telegram_notification(1, 'x'))
        logger.warning.assert_called_once()

        ok_client = _HttpClientContext(response=_HttpResponse(200, 'ok'))
        with patch('core.utils.os.getenv', return_value='token'), patch('core.telegram_gateway.httpx.AsyncClient', return_value=ok_client):
            self.assertTrue(await utils.send_telegram_notification(9, 'hello'))

        bad_client = _HttpClientContext(response=_HttpResponse(500, 'err'))
        with patch('core.utils.os.getenv', return_value='token'), patch('core.telegram_gateway.httpx.AsyncClient', return_value=bad_client):
            self.assertFalse(await utils.send_telegram_notification(9, 'hello'))

        err_client = _HttpClientContext(error=RuntimeError('network'))
        with patch('core.utils.os.getenv', return_value='token'), patch('core.telegram_gateway.httpx.AsyncClient', return_value=err_client):
            self.assertFalse(await utils.send_telegram_notification(9, 'hello'))

    async def test_create_user_notification_and_publish_user_event(self):
        db = MagicMock()
        db.commit = AsyncMock()

        async def refresh_side_effect(notification):
            notification.id = 7
            notification.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)

        db.refresh = AsyncMock(side_effect=refresh_side_effect)
        db.refresh.side_effect = refresh_side_effect
        redis_client = AsyncMock()

        with patch('core.utils.redis.Redis', return_value=_RedisContext(redis_client)), patch(
            'core.utils.publish_user_event', AsyncMock()
        ) as publish_user_event:
            notification = await utils.create_user_notification(
                db,
                user_id=2,
                message='msg',
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.USER,
                extra_payload={'route': '/users/19'},
                dedupe_key='trade_completed:webapp:10025:2',
            )

        self.assertEqual(notification.id, 7)
        self.assertEqual(notification.dedupe_key, 'trade_completed:webapp:10025:2')
        self.assertEqual(notification.extra_payload, {'route': '/users/19'})
        db.add.assert_called_once()
        db.commit.assert_awaited_once()
        redis_client.incr.assert_awaited_once_with('user:2:unread_count')
        publish_user_event.assert_awaited_once()
        self.assertEqual(publish_user_event.await_args.args[2]['route'], '/users/19')
        self.assertEqual(publish_user_event.await_args.args[2]['extra_payload'], {'route': '/users/19'})

        redis_error_client = AsyncMock()
        redis_error_client.incr = AsyncMock(side_effect=RuntimeError('redis down'))
        db = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock(side_effect=refresh_side_effect)
        with patch('core.utils.redis.Redis', return_value=_RedisContext(redis_error_client)), patch(
            'core.utils.publish_user_event', AsyncMock()
        ), patch.object(utils, 'logger') as logger:
            await utils.create_user_notification(db, user_id=3, message='msg')
        logger.warning.assert_called_once()

        publish_client = AsyncMock()
        with patch('core.utils.redis.Redis', return_value=_RedisContext(publish_client)):
            await utils.publish_user_event(5, 'chat:typing', {'ok': True})
        publish_client.publish.assert_awaited_once()

        failing_publish_client = AsyncMock()
        failing_publish_client.publish = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('core.utils.redis.Redis', return_value=_RedisContext(failing_publish_client)), patch.object(
            utils, 'logger'
        ) as logger:
            await utils.publish_user_event(5, 'chat:typing', {'ok': True})
        logger.warning.assert_called_once()

    async def test_check_user_limits(self):
        unlimited_user = SimpleNamespace(limitations_expire_at=None)
        self.assertEqual(utils.check_user_limits(unlimited_user, 'trade'), (True, None))

        expired_user = SimpleNamespace(limitations_expire_at=datetime.utcnow() - timedelta(days=1))
        self.assertEqual(utils.check_user_limits(expired_user, 'channel_message'), (True, None))

        trade_limited = SimpleNamespace(
            limitations_expire_at=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=1,
            trades_count=1,
            max_active_commodities=5,
            commodities_traded_count=1,
            max_daily_requests=3,
            channel_messages_count=0,
        )
        allowed, message = utils.check_user_limits(trade_limited, 'trade', quantity=2)
        self.assertFalse(allowed)
        self.assertIn('حداکثر تعداد معاملات', message)

        commodity_limited = SimpleNamespace(
            limitations_expire_at=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=None,
            trades_count=0,
            max_active_commodities=2,
            commodities_traded_count=2,
            max_daily_requests=None,
            channel_messages_count=0,
        )
        allowed, message = utils.check_user_limits(commodity_limited, 'trade', quantity=1)
        self.assertFalse(allowed)
        self.assertIn('باقی\u200cمانده', message)

        channel_limited = SimpleNamespace(
            limitations_expire_at=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=None,
            trades_count=0,
            max_active_commodities=None,
            commodities_traded_count=0,
            max_daily_requests=1,
            channel_messages_count=1,
        )
        allowed, message = utils.check_user_limits(channel_limited, 'channel_message')
        self.assertFalse(allowed)
        self.assertIn('حداکثر تعداد ارسال لفظ', message)

        active_but_unrestricted = SimpleNamespace(
            limitations_expire_at=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=None,
            trades_count=0,
            max_active_commodities=None,
            commodities_traded_count=0,
            max_daily_requests=None,
            channel_messages_count=0,
        )
        self.assertEqual(utils.check_user_limits(active_but_unrestricted, 'other'), (True, None))

    async def test_increment_and_reset_user_counter(self):
        session = AsyncMock()
        user = SimpleNamespace(id=4)
        trade_counter = SimpleNamespace(
            trades_count=1,
            commodities_traded_count=2,
            channel_messages_count=0,
        )
        channel_counter = SimpleNamespace(
            trades_count=0,
            commodities_traded_count=0,
            channel_messages_count=5,
        )
        session.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: trade_counter),
                SimpleNamespace(scalar_one_or_none=lambda: channel_counter),
            ]
        )

        await utils.increment_user_counter(session, user, 'trade', quantity=3)
        await utils.increment_user_counter(session, user, 'channel_message')
        self.assertEqual(session.execute.await_count, 2)
        self.assertEqual(session.commit.await_count, 2)
        self.assertEqual(trade_counter.trades_count, 2)
        self.assertEqual(trade_counter.commodities_traded_count, 5)
        self.assertEqual(channel_counter.channel_messages_count, 6)

        failing_session = AsyncMock()
        failing_session.execute = AsyncMock(side_effect=RuntimeError('db'))
        await utils.increment_user_counter(failing_session, user, 'trade', quantity=1)
        failing_session.rollback.assert_awaited_once()

        reset_session = AsyncMock()
        user_with_counts = SimpleNamespace(id=9)
        reset_counter = SimpleNamespace(
            trades_count=8,
            commodities_traded_count=12,
            channel_messages_count=4,
        )
        reset_session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: reset_counter)
        )
        await utils.reset_user_counters(reset_session, user_with_counts)
        reset_session.execute.assert_awaited_once()
        reset_session.commit.assert_awaited_once()
        reset_session.refresh.assert_awaited_once_with(user_with_counts)
        self.assertEqual(reset_counter.trades_count, 0)
        self.assertEqual(reset_counter.commodities_traded_count, 0)
        self.assertEqual(reset_counter.channel_messages_count, 0)


if __name__ == '__main__':
    unittest.main()
