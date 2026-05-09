import unittest
from unittest.mock import AsyncMock, patch

from bot.utils import redis_helpers


class _FrozenDateTime:
    @classmethod
    def now(cls):
        from datetime import datetime

        return datetime(2026, 5, 8, 12, 0, 0)


class BotRedisHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        redis_helpers._memory_fallback['rate_tracker'].clear()
        redis_helpers._memory_fallback['daily_tracker'].clear()
        redis_helpers._memory_fallback['confirmations'].clear()
        redis_helpers._memory_fallback['cache'].clear()
        redis_helpers._memory_fallback['deleted_telegram_users'].clear()

    async def test_track_expire_rate_supports_redis_and_fallback(self):
        redis_client = AsyncMock()
        redis_client.zcard = AsyncMock(return_value=3)
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=redis_client)):
            self.assertEqual(await redis_helpers.track_expire_rate(7), 3)

        redis_client.zadd.assert_awaited_once()
        redis_client.aclose.assert_awaited()

        redis_helpers._memory_fallback['rate_tracker'][7] = [30.0, 90.0]
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.time.time', return_value=100.0
        ):
            self.assertEqual(await redis_helpers.track_expire_rate(7, window_seconds=15), 2)

        broken_redis = AsyncMock()
        broken_redis.zadd = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=broken_redis)), patch(
            'bot.utils.redis_helpers.time.time', return_value=150.0
        ):
            self.assertEqual(await redis_helpers.track_expire_rate(8, window_seconds=60), 1)

    async def test_track_daily_expire_supports_redis_and_fallback(self):
        redis_client = AsyncMock()
        redis_client.incr = AsyncMock(return_value=2)
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=redis_client)):
            result = await redis_helpers.track_daily_expire(1, total_offers=4)
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['rate'], 50.0)

        redis_helpers._memory_fallback['daily_tracker'][1] = {'date': '2026-05-07', 'count': 9}
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.datetime', _FrozenDateTime
        ):
            result = await redis_helpers.track_daily_expire(1, total_offers=5)
        self.assertEqual(result['date'], '2026-05-08')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['rate'], 20.0)

        broken_redis = AsyncMock()
        broken_redis.incr = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=broken_redis)), patch(
            'bot.utils.redis_helpers.datetime', _FrozenDateTime
        ):
            result = await redis_helpers.track_daily_expire(2, total_offers=0)
        self.assertEqual(result['rate'], 0)

    async def test_check_double_click_works_in_redis_and_fallback_modes(self):
        redis_client = AsyncMock()
        redis_client.exists = AsyncMock(side_effect=[0, 1])
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=redis_client)):
            self.assertFalse(await redis_helpers.check_double_click(1, 2, 3, timeout=0.4))
            self.assertTrue(await redis_helpers.check_double_click(1, 2, 3, timeout=0.4))

        redis_client.setex.assert_awaited_once_with('confirm:1:2:3', 1, 'pending')
        redis_client.delete.assert_awaited_once_with('confirm:1:2:3')

        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.time.time', return_value=100.0
        ):
            self.assertFalse(await redis_helpers.check_double_click(1, 2, 3))
            self.assertTrue(await redis_helpers.check_double_click(1, 2, 3))

        broken_redis = AsyncMock()
        broken_redis.exists = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=broken_redis)), patch(
            'bot.utils.redis_helpers.time.time', return_value=200.0
        ):
            self.assertFalse(await redis_helpers.check_double_click(9, 8, 7, timeout=1))

    async def test_cached_commodities_support_redis_fallback_and_expiry(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value='[{"id": 1}]')
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=redis_client)):
            self.assertEqual(await redis_helpers.get_cached_commodities(), [{'id': 1}])
            await redis_helpers.set_cached_commodities([{'id': 2}], ttl=30)
            await redis_helpers.invalidate_commodity_cache()

        redis_client.setex.assert_awaited_once()
        redis_client.delete.assert_awaited_once_with('cache:commodities:all')

        commodities = [{'id': 1, 'name': 'Gold'}]
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.time.time', return_value=100.0
        ):
            await redis_helpers.set_cached_commodities(commodities, ttl=300)

        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.time.time', return_value=120.0
        ):
            self.assertEqual(await redis_helpers.get_cached_commodities(), commodities)

        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)):
            await redis_helpers.invalidate_commodity_cache()

        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.time.time', return_value=120.0
        ):
            self.assertIsNone(await redis_helpers.get_cached_commodities())

        redis_helpers._memory_fallback['cache']['cache:commodities:all'] = {
            'data': commodities,
            'expires': 50.0,
        }
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)), patch(
            'bot.utils.redis_helpers.time.time', return_value=120.0
        ):
            self.assertIsNone(await redis_helpers.get_cached_commodities())

        broken_redis = AsyncMock()
        broken_redis.get = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=broken_redis)):
            self.assertIsNone(await redis_helpers.get_cached_commodities())

    async def test_deleted_telegram_user_tracking_supports_redis_fallback_and_errors(self):
        redis_client = AsyncMock()
        redis_client.exists = AsyncMock(return_value=1)
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=redis_client)):
            await redis_helpers.mark_deleted_telegram_user(11)
            self.assertTrue(await redis_helpers.is_deleted_telegram_user(11))

        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=None)):
            await redis_helpers.mark_deleted_telegram_user(55)
            self.assertTrue(await redis_helpers.is_deleted_telegram_user(55))
            self.assertFalse(await redis_helpers.is_deleted_telegram_user(56))

        broken_redis = AsyncMock()
        broken_redis.set = AsyncMock(side_effect=RuntimeError('redis down'))
        broken_redis.exists = AsyncMock(side_effect=RuntimeError('redis down'))
        with patch('bot.utils.redis_helpers.get_redis_client', AsyncMock(return_value=broken_redis)):
            await redis_helpers.mark_deleted_telegram_user(77)
            self.assertTrue(await redis_helpers.is_deleted_telegram_user(77))


if __name__ == '__main__':
    unittest.main()