import json
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, patch

from core import cache


async def _scan_keys(*keys):
    for key in keys:
        yield key


class CoreCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_keys_build_expected_names(self):
        self.assertEqual(cache.CacheKeys.user_by_telegram_id(5), 'user:telegram_id:5')
        self.assertEqual(cache.CacheKeys.active_offer_count(2), 'user:2:active_offer_count')
        self.assertEqual(cache.CacheKeys.price_average(1, 'buy', '5-10'), 'price_avg:1:buy:5-10')
        self.assertEqual(cache.CacheKeys.COMMODITIES_ALL, 'cache:commodities:all')

    async def test_get_redis_handles_import_failures(self):
        with patch('core.redis.get_redis_client', side_effect=RuntimeError('down')):
            self.assertIsNone(await cache._get_redis())

    async def test_cache_get_and_set_handle_success_and_failures(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=json.dumps({'ok': True}))
        with patch('core.cache._get_redis', AsyncMock(return_value=redis_client)):
            self.assertEqual(await cache.cache_get('demo'), {'ok': True})
            self.assertTrue(await cache.cache_set('demo', {'ok': True, 'when': datetime(2025, 1, 1)}, ttl=9))
            redis_client.setex.assert_awaited_once()

        broken_client = AsyncMock()
        broken_client.get = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('core.cache._get_redis', AsyncMock(return_value=broken_client)):
            self.assertIsNone(await cache.cache_get('demo'))

        broken_set_client = AsyncMock()
        broken_set_client.setex = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('core.cache._get_redis', AsyncMock(return_value=broken_set_client)):
            self.assertFalse(await cache.cache_set('demo', {'ok': True}))

        with patch('core.cache._get_redis', AsyncMock(return_value=None)):
            self.assertIsNone(await cache.cache_get('demo'))
            self.assertFalse(await cache.cache_set('demo', {'ok': True}))

    async def test_cache_delete_incr_and_decr(self):
        redis_client = AsyncMock()
        redis_client.incr = AsyncMock(return_value=4)
        redis_client.decr = AsyncMock(return_value=-1)

        with patch('core.cache._get_redis', AsyncMock(return_value=redis_client)):
            self.assertTrue(await cache.cache_delete('demo'))
            self.assertEqual(await cache.cache_incr('counter', ttl=15), 4)
            self.assertEqual(await cache.cache_decr('counter', min_value=0), 0)

        redis_client.delete.assert_awaited_once_with('demo')
        redis_client.expire.assert_awaited_once_with('counter', 15)
        redis_client.set.assert_awaited_once_with('counter', 0)

        broken_client = AsyncMock()
        broken_client.delete = AsyncMock(side_effect=RuntimeError('boom'))
        broken_client.incr = AsyncMock(side_effect=RuntimeError('boom'))
        broken_client.decr = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('core.cache._get_redis', AsyncMock(return_value=broken_client)):
            self.assertFalse(await cache.cache_delete('demo'))
            self.assertIsNone(await cache.cache_incr('counter'))
            self.assertIsNone(await cache.cache_decr('counter'))

        with patch('core.cache._get_redis', AsyncMock(return_value=None)):
            self.assertFalse(await cache.cache_delete('demo'))
            self.assertIsNone(await cache.cache_incr('counter'))
            self.assertIsNone(await cache.cache_decr('counter'))

    async def test_cache_delete_pattern_and_high_level_wrappers(self):
        redis_client = AsyncMock()
        redis_client.scan_iter = lambda match: _scan_keys('price_avg:1:buy:a', 'price_avg:1:sell:b')
        with patch('core.cache._get_redis', AsyncMock(return_value=redis_client)):
            deleted = await cache.cache_delete_pattern('price_avg:*')
        self.assertEqual(deleted, 2)
        redis_client.delete.assert_awaited_once_with('price_avg:1:buy:a', 'price_avg:1:sell:b')

        empty_redis = AsyncMock()
        empty_redis.scan_iter = lambda match: _scan_keys()
        with patch('core.cache._get_redis', AsyncMock(return_value=empty_redis)):
            self.assertEqual(await cache.cache_delete_pattern('price_avg:*'), 0)

        broken_redis = AsyncMock()
        broken_redis.scan_iter = lambda match: _scan_keys('x')
        broken_redis.delete = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('core.cache._get_redis', AsyncMock(return_value=broken_redis)):
            self.assertEqual(await cache.cache_delete_pattern('price_avg:*'), 0)

        with patch('core.cache._get_redis', AsyncMock(return_value=None)):
            self.assertEqual(await cache.cache_delete_pattern('price_avg:*'), 0)

        with patch('core.cache.cache_get', AsyncMock(return_value={'id': 1})) as cache_get, patch(
            'core.cache.cache_set', AsyncMock(return_value=True)
        ) as cache_set, patch('core.cache.cache_delete', AsyncMock(return_value=True)) as cache_delete, patch(
            'core.cache.cache_incr', AsyncMock(return_value=3)
        ) as cache_incr, patch('core.cache.cache_decr', AsyncMock(return_value=2)) as cache_decr, patch(
            'core.cache.cache_delete_pattern', AsyncMock(return_value=4)
        ) as cache_delete_pattern:
            self.assertEqual(await cache.get_cached_user_by_telegram_id(77), {'id': 1})
            self.assertTrue(await cache.set_cached_user(77, {'id': 1}))
            self.assertTrue(await cache.invalidate_user_cache(77))
            self.assertEqual(await cache.get_active_offer_count(9), {'id': 1})
            self.assertTrue(await cache.set_active_offer_count(9, 5))
            self.assertEqual(await cache.incr_active_offer_count(9), 3)
            self.assertEqual(await cache.decr_active_offer_count(9), 2)
            self.assertEqual(await cache.get_price_average(1, 'buy', 'small'), {'id': 1})
            self.assertTrue(await cache.set_price_average(1, 'buy', 'small', 12.5))
            self.assertEqual(await cache.invalidate_price_averages(commodity_id=1), 4)
            self.assertEqual(await cache.invalidate_price_averages(), 4)
            self.assertEqual(await cache.get_cached_commodities(), {'id': 1})
            self.assertTrue(await cache.set_cached_commodities([{'id': 1}]))
            self.assertTrue(await cache.invalidate_commodities_cache())

        cache_get.assert_any_await('user:telegram_id:77')
        cache_set.assert_any_await('user:telegram_id:77', {'id': 1}, cache.CacheTTL.USER)
        cache_delete.assert_any_await('user:telegram_id:77')
        cache_incr.assert_awaited_once_with('user:9:active_offer_count', cache.CacheTTL.OFFER_COUNT)
        cache_decr.assert_awaited_once_with('user:9:active_offer_count', min_value=0)
        cache_delete_pattern.assert_any_await('price_avg:1:*')
        cache_delete_pattern.assert_any_await('price_avg:*')
        cache_get.assert_any_await(cache.CacheKeys.COMMODITIES_ALL)
        cache_set.assert_any_await(cache.CacheKeys.COMMODITIES_ALL, [{'id': 1}], cache.CacheTTL.COMMODITIES)
        cache_delete.assert_any_await(cache.CacheKeys.COMMODITIES_ALL)


if __name__ == '__main__':
    unittest.main()