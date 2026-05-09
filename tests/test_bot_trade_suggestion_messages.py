import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.utils import trade_suggestion_messages as suggestion_messages


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRedisClient:
    def __init__(self):
        self.hset = AsyncMock()
        self.expire = AsyncMock()
        self.hdel = AsyncMock()
        self.hgetall = AsyncMock(return_value={})
        self.aclose = AsyncMock()
        self.pubsub_instance = None

    def pubsub(self):
        return self.pubsub_instance


class _FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribe = AsyncMock()
        self.unsubscribe = AsyncMock()
        self.close = AsyncMock()

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._messages:
            message = self._messages.pop(0)
            if isinstance(message, BaseException):
                raise message
            return message
        raise asyncio.CancelledError()


class BotTradeSuggestionMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        suggestion_messages._memory_suggestions.clear()

    async def test_build_amount_buttons_and_offer_buttons(self):
        markup = suggestion_messages.build_trade_amount_buttons(5, [0, 4, 4, 2], pending_amount=4)
        self.assertEqual([button.text for button in markup.inline_keyboard[0]], ['تایید 4 عدد؟', '2 عدد'])

        with patch(
            'bot.utils.trade_suggestion_messages.get_available_trade_amounts', return_value=[9, 3]
        ) as get_available_trade_amounts:
            offer_markup = suggestion_messages.build_offer_trade_buttons(
                7,
                quantity=20,
                remaining=10,
                is_wholesale=False,
                lot_sizes=[1, 8, 3],
                pending_amount=3,
            )

        get_available_trade_amounts.assert_called_once_with(
            quantity=20,
            remaining_quantity=10,
            is_wholesale=False,
            lot_sizes=[8, 3, 1],
        )
        self.assertEqual(offer_markup.inline_keyboard[0][1].text, 'تایید 3 عدد؟')
        self.assertEqual(suggestion_messages._record_field(1, 2), '1:2')
        self.assertEqual(suggestion_messages._record_key(3), 'trade_suggestion:offer:3')

    async def test_upsert_remove_and_get_records_support_redis_and_fallback(self):
        redis_client = _FakeRedisClient()
        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=redis_client), patch(
            'bot.utils.trade_suggestion_messages.time.time', return_value=100.0
        ):
            await suggestion_messages.upsert_trade_suggestion_record(4, 10, 20, 7, ttl_seconds=15)

        redis_client.hset.assert_awaited_once()
        redis_client.expire.assert_awaited_once_with('trade_suggestion:offer:4', 75)
        self.assertEqual(suggestion_messages._memory_suggestions, {})

        broken_redis = _FakeRedisClient()
        broken_redis.hset = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=broken_redis), patch(
            'bot.utils.trade_suggestion_messages.time.time', return_value=200.0
        ):
            await suggestion_messages.upsert_trade_suggestion_record(4, 11, 21, 9, ttl_seconds=15)
        self.assertIn('11:21', suggestion_messages._memory_suggestions[4])

        redis_records = {
            '10:20': json.dumps({'chat_id': 10, 'message_id': 20, 'requested_amount': 7, 'expires_at': 300.0}),
            '11:21': '{bad json',
            '12:22': json.dumps({'chat_id': 12, 'message_id': 22, 'requested_amount': 5, 'expires_at': 50.0}),
        }
        redis_client = _FakeRedisClient()
        redis_client.hgetall = AsyncMock(return_value=redis_records)
        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=redis_client), patch(
            'bot.utils.trade_suggestion_messages.time.time', return_value=100.0
        ):
            records = await suggestion_messages.get_trade_suggestion_records(5)
        self.assertEqual(records, [{'chat_id': 10, 'message_id': 20, 'requested_amount': 7, 'expires_at': 300.0}])
        self.assertEqual(redis_client.hdel.await_count, 2)

        suggestion_messages._memory_suggestions[6] = {
            '1:1': {'chat_id': 1, 'message_id': 1, 'requested_amount': 2, 'expires_at': 300.0},
            '2:2': {'chat_id': 2, 'message_id': 2, 'requested_amount': 3, 'expires_at': 10.0},
        }
        broken_redis = _FakeRedisClient()
        broken_redis.hgetall = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=broken_redis), patch(
            'bot.utils.trade_suggestion_messages.time.time', return_value=100.0
        ):
            records = await suggestion_messages.get_trade_suggestion_records(6)
        self.assertEqual(records, [{'chat_id': 1, 'message_id': 1, 'requested_amount': 2, 'expires_at': 300.0}])
        self.assertNotIn('2:2', suggestion_messages._memory_suggestions[6])

        redis_client = _FakeRedisClient()
        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=redis_client):
            await suggestion_messages.remove_trade_suggestion_record(6, 1, 1)
        redis_client.hdel.assert_awaited_once_with('trade_suggestion:offer:6', '1:1')

        broken_redis = _FakeRedisClient()
        broken_redis.hdel = AsyncMock(side_effect=RuntimeError('redis down'))
        suggestion_messages._memory_suggestions[6] = {'3:3': {'chat_id': 3, 'message_id': 3, 'expires_at': 999.0}}
        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=broken_redis):
            await suggestion_messages.remove_trade_suggestion_record(6, 3, 3)
        self.assertNotIn(6, suggestion_messages._memory_suggestions)

    async def test_clear_markup_and_sync_for_inactive_offer(self):
        bot = AsyncMock()
        bot.edit_message_reply_markup = AsyncMock(side_effect=Exception('message is not modified'))
        await suggestion_messages._clear_suggestion_markup(bot, 1, 2)

        bot.edit_message_reply_markup = AsyncMock(side_effect=Exception('boom'))
        with patch.object(suggestion_messages, 'logger') as logger:
            await suggestion_messages._clear_suggestion_markup(bot, 1, 2)
        logger.debug.assert_called_once()

        records = [
            {'chat_id': 1, 'message_id': 2, 'requested_amount': 7},
            {'chat_id': 3, 'message_id': 4, 'requested_amount': 9},
        ]
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        with patch('bot.utils.trade_suggestion_messages.get_trade_suggestion_records', AsyncMock(return_value=records)), patch(
            'bot.utils.trade_suggestion_messages.AsyncSessionLocal', return_value=_AsyncSessionContext(session)
        ), patch('bot.utils.trade_suggestion_messages._clear_suggestion_markup', AsyncMock()) as clear_markup, patch(
            'bot.utils.trade_suggestion_messages.remove_trade_suggestion_record', AsyncMock()
        ) as remove_record:
            await suggestion_messages.sync_trade_suggestions_for_offer(bot, 9)

        self.assertEqual(clear_markup.await_count, 2)
        self.assertEqual(remove_record.await_count, 2)

    async def test_sync_for_active_offer_updates_messages_and_removes_when_empty(self):
        records = [
            {'chat_id': 1, 'message_id': 2, 'requested_amount': 7},
            {'chat_id': 3, 'message_id': 4, 'requested_amount': 9},
        ]
        offer = SimpleNamespace(
            id=5,
            status=suggestion_messages.OfferStatus.ACTIVE,
            quantity=20,
            remaining_quantity=10,
            is_wholesale=False,
            lot_sizes=[3, 7],
            offer_type='sell',
            price=100,
            commodity=SimpleNamespace(name='Gold'),
        )
        session = AsyncMock()
        session.get = AsyncMock(return_value=offer)
        session.refresh = AsyncMock()
        bot = AsyncMock()
        bot.edit_message_text = AsyncMock(side_effect=[Exception('message is not modified'), None])

        with patch('bot.utils.trade_suggestion_messages.get_trade_suggestion_records', AsyncMock(return_value=records)), patch(
            'bot.utils.trade_suggestion_messages.AsyncSessionLocal', return_value=_AsyncSessionContext(session)
        ), patch(
            'bot.utils.trade_suggestion_messages.get_available_trade_amounts', return_value=[7, 3]
        ), patch(
            'bot.utils.trade_suggestion_messages.build_lot_unavailable_suggestion_payload',
            side_effect=lambda **kwargs: {'message': f"offer {kwargs['requested_amount']}"},
        ), patch('bot.utils.trade_suggestion_messages.remove_trade_suggestion_record', AsyncMock()) as remove_record:
            await suggestion_messages.sync_trade_suggestions_for_offer(bot, 5)

        self.assertEqual(bot.edit_message_text.await_count, 2)
        remove_record.assert_not_awaited()

        bot = AsyncMock()
        session = AsyncMock()
        session.get = AsyncMock(return_value=offer)
        session.refresh = AsyncMock()
        with patch('bot.utils.trade_suggestion_messages.get_trade_suggestion_records', AsyncMock(return_value=records)), patch(
            'bot.utils.trade_suggestion_messages.AsyncSessionLocal', return_value=_AsyncSessionContext(session)
        ), patch(
            'bot.utils.trade_suggestion_messages.get_available_trade_amounts', return_value=[]
        ), patch(
            'bot.utils.trade_suggestion_messages.build_lot_unavailable_suggestion_payload', return_value={'message': 'empty'}
        ), patch('bot.utils.trade_suggestion_messages.remove_trade_suggestion_record', AsyncMock()) as remove_record:
            await suggestion_messages.sync_trade_suggestions_for_offer(bot, 5)

        self.assertEqual(remove_record.await_count, 2)

    async def test_schedule_cleanup_and_pending_reset(self):
        created = []

        def capture_task(coro):
            created.append(coro)
            return MagicMock()

        with patch('bot.utils.trade_suggestion_messages.asyncio.create_task', side_effect=capture_task), patch(
            'bot.utils.trade_suggestion_messages.asyncio.sleep', AsyncMock()
        ), patch(
            'bot.utils.trade_suggestion_messages.get_trade_suggestion_records', AsyncMock(return_value=[
                {'chat_id': 10, 'message_id': 20, 'expires_at': 5.0},
            ])
        ), patch('bot.utils.trade_suggestion_messages.time.time', return_value=10.0), patch(
            'bot.utils.trade_suggestion_messages._clear_suggestion_markup', AsyncMock()
        ) as clear_markup, patch(
            'bot.utils.trade_suggestion_messages.remove_trade_suggestion_record', AsyncMock()
        ) as remove_record:
            suggestion_messages.schedule_trade_suggestion_cleanup(MagicMock(), 1, 10, 20)
            await created.pop(0)

        clear_markup.assert_awaited_once()
        remove_record.assert_awaited_once_with(1, 10, 20)

        created = []
        with patch('bot.utils.trade_suggestion_messages.asyncio.create_task', side_effect=capture_task), patch(
            'bot.utils.trade_suggestion_messages.asyncio.sleep', AsyncMock()
        ), patch('bot.utils.trade_suggestion_messages.sync_trade_suggestions_for_offer', AsyncMock()) as sync_offer:
            suggestion_messages.schedule_trade_suggestion_pending_reset(MagicMock(), 4)
            await created.pop(0)
        sync_offer.assert_awaited_once()

    async def test_listen_trade_suggestion_events_processes_valid_messages_and_cleans_up(self):
        pubsub = _FakePubSub(
            [
                {'type': 'message', 'data': 'not-json'},
                {'type': 'message', 'data': json.dumps({'offer_id': 0})},
                {'type': 'message', 'data': json.dumps({'offer_id': 6})},
            ]
        )
        redis_client = _FakeRedisClient()
        redis_client.pubsub_instance = pubsub

        with patch('bot.utils.trade_suggestion_messages.redis.Redis', return_value=redis_client), patch(
            'bot.utils.trade_suggestion_messages.sync_trade_suggestions_for_offer', AsyncMock(side_effect=[RuntimeError('boom'), None])
        ) as sync_offer, patch('bot.utils.trade_suggestion_messages.asyncio.sleep', AsyncMock()), patch.object(
            suggestion_messages, 'logger'
        ) as logger:
            with self.assertRaises(asyncio.CancelledError):
                await suggestion_messages.listen_trade_suggestion_events(MagicMock())

        pubsub.subscribe.assert_awaited_once()
        pubsub.unsubscribe.assert_awaited_once()
        pubsub.close.assert_awaited_once()
        redis_client.aclose.assert_awaited_once()
        sync_offer.assert_awaited_once_with(unittest.mock.ANY, 6)
        logger.debug.assert_called_once()


if __name__ == '__main__':
    unittest.main()