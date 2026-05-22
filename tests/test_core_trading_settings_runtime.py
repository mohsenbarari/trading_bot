import asyncio
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core import trading_settings


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class CoreTradingSettingsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        trading_settings._fallback_cache = None
        trading_settings._fallback_timestamp = 0

    async def test_trading_settings_properties(self):
        settings = trading_settings.TradingSettings(invitation_expiry_days=3, offer_min_quantity=7)
        self.assertEqual(settings.invitation_expiry_minutes, 4320)
        self.assertEqual(settings.lot_min_size, 7)
        self.assertEqual(settings.lot_max_count, 3)

    async def test_trading_settings_schedule_fields_are_json_serializable(self):
        settings = trading_settings.TradingSettings(
            market_schedule_enabled=True,
            market_open_time_local='08:30',
            market_close_time_local='17:15',
            market_closed_weekdays=[4, 5],
        )

        dumped = settings.model_dump()
        json.dumps(dumped)

        self.assertTrue(dumped['market_schedule_enabled'])
        self.assertEqual(dumped['market_timezone'], 'Asia/Tehran')
        self.assertEqual(dumped['market_closed_weekdays'], [4, 5])

    async def test_load_from_json_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / 'settings.json'
            file_path.write_text(json.dumps({'offer_max_quantity': 88}), encoding='utf-8')
            with patch.object(trading_settings, 'SETTINGS_FILE', file_path):
                self.assertEqual(trading_settings._load_from_json()['offer_max_quantity'], 88)

            broken_path = Path(tmpdir) / 'broken.json'
            broken_path.write_text('{', encoding='utf-8')
            with patch.object(trading_settings, 'SETTINGS_FILE', broken_path), patch.object(
                trading_settings, 'logger'
            ) as logger:
                self.assertEqual(trading_settings._load_from_json(), {})
            logger.warning.assert_called_once()

    async def test_load_from_db_async_parses_json_and_plain_values(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_ExecuteResult([('a', '1'), ('b', json.dumps({'x': 2})), ('c', '{'), ('d', None)]))

        with patch('core.db.AsyncSessionLocal', return_value=_AsyncSessionContext(session)):
            data = await trading_settings._load_from_db_async()

        self.assertEqual(data, {'a': 1, 'b': {'x': 2}, 'c': '{', 'd': None})

        with patch('core.db.AsyncSessionLocal', side_effect=RuntimeError('db down')), patch.object(
            trading_settings, 'logger'
        ) as logger:
            self.assertEqual(await trading_settings._load_from_db_async(), {})
        logger.warning.assert_called_once()

    async def test_get_and_set_redis_cache(self):
        redis_client = AsyncMock()
        redis_client.get = AsyncMock(return_value=json.dumps({'offer_min_quantity': 11}))

        with patch('core.redis.get_redis_client', return_value=redis_client):
            cached = await trading_settings._get_from_redis_cache()
            self.assertEqual(cached.offer_min_quantity, 11)

            await trading_settings._set_redis_cache(cached)
        redis_client.setex.assert_awaited_once()

        invalid_redis = AsyncMock()
        invalid_redis.get = AsyncMock(return_value='{')
        with patch('core.redis.get_redis_client', return_value=invalid_redis), patch.object(
            trading_settings, 'logger'
        ) as logger:
            self.assertIsNone(await trading_settings._get_from_redis_cache())
        logger.debug.assert_called_once()

        failing_redis = AsyncMock()
        failing_redis.setex = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('core.redis.get_redis_client', return_value=failing_redis), patch.object(
            trading_settings, 'logger'
        ) as logger:
            await trading_settings._set_redis_cache(trading_settings.TradingSettings())
        logger.debug.assert_called_once()

    async def test_load_and_get_settings_paths(self):
        with patch('core.trading_settings._load_from_db_async', AsyncMock(return_value={'offer_min_quantity': 9})), patch(
            'core.trading_settings._load_from_json', return_value={'offer_min_quantity': 4}
        ):
            loaded = await trading_settings.load_trading_settings_async()
        self.assertEqual(loaded.offer_min_quantity, 9)

        with patch('core.trading_settings._load_from_db_async', AsyncMock(return_value={})), patch(
            'core.trading_settings._load_from_json', return_value={'offer_min_quantity': 4}
        ):
            loaded = await trading_settings.load_trading_settings_async()
        self.assertEqual(loaded.offer_min_quantity, 4)

        with patch(
            'core.trading_settings.load_trading_settings_async',
            AsyncMock(return_value=trading_settings.TradingSettings(offer_min_quantity=6))
        ), patch('core.trading_settings._load_from_json', return_value={'offer_min_quantity': 99}):
            sync_loaded = trading_settings.load_trading_settings()
        self.assertEqual(sync_loaded.offer_min_quantity, 6)

        with patch('core.trading_settings._load_from_db_async', AsyncMock(return_value={})), patch(
            'core.trading_settings._load_from_json', return_value={}
        ):
            loaded = await trading_settings.load_trading_settings_async()
        self.assertIsInstance(loaded, trading_settings.TradingSettings)

        with patch(
            'core.trading_settings.load_trading_settings_async',
            AsyncMock(return_value=trading_settings.TradingSettings())
        ), patch('core.trading_settings._load_from_json', return_value={}):
            sync_loaded = trading_settings.load_trading_settings()
        self.assertIsInstance(sync_loaded, trading_settings.TradingSettings)

        with patch(
            'core.trading_settings.load_trading_settings_async',
            AsyncMock(side_effect=RuntimeError('db bridge failed'))
        ), patch('core.trading_settings._load_from_json', return_value={'offer_min_quantity': 4}), patch.object(
            trading_settings, 'logger'
        ) as logger:
            sync_loaded = trading_settings.load_trading_settings()
        self.assertEqual(sync_loaded.offer_min_quantity, 4)
        logger.warning.assert_called()

        with patch('core.trading_settings._get_from_redis_cache', AsyncMock(return_value=trading_settings.TradingSettings())), patch(
            'core.trading_settings.load_trading_settings_async', AsyncMock()
        ) as load_async:
            cached = await trading_settings.get_trading_settings_async()
        self.assertIsInstance(cached, trading_settings.TradingSettings)
        load_async.assert_not_awaited()

        fresh_settings = trading_settings.TradingSettings(offer_max_quantity=70)
        with patch('core.trading_settings._get_from_redis_cache', AsyncMock(return_value=None)), patch(
            'core.trading_settings.load_trading_settings_async', AsyncMock(return_value=fresh_settings)
        ) as load_async, patch('core.trading_settings._set_redis_cache', AsyncMock()) as set_redis_cache:
            cached = await trading_settings.get_trading_settings_async()
        self.assertIs(cached, fresh_settings)
        load_async.assert_awaited_once()
        set_redis_cache.assert_awaited_once_with(fresh_settings)

    async def test_sync_fallback_cache_and_refresh(self):
        load_calls = [
            trading_settings.TradingSettings(offer_min_quantity=8),
            RuntimeError('reload failed'),
        ]

        def fake_load():
            value = load_calls.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        with patch('core.trading_settings.load_trading_settings', side_effect=fake_load), patch(
            'core.trading_settings.time.time', side_effect=[0, 20, 100]
        ):
            first = trading_settings.get_trading_settings()
            second = trading_settings.get_trading_settings()
            third = trading_settings.get_trading_settings()

        self.assertEqual(first.offer_min_quantity, 8)
        self.assertIs(second, first)
        self.assertIs(third, first)

        trading_settings._fallback_cache = None
        trading_settings._fallback_timestamp = 0
        with patch('core.trading_settings.load_trading_settings', side_effect=RuntimeError('reload failed')), patch(
            'core.trading_settings.time.time', return_value=100
        ):
            fallback_loaded = trading_settings.get_trading_settings()
        self.assertIsInstance(fallback_loaded, trading_settings.TradingSettings)

        with patch('core.trading_settings.load_trading_settings', return_value=trading_settings.TradingSettings(offer_min_quantity=12)), patch(
            'core.trading_settings.time.time', return_value=50
        ):
            trading_settings.refresh_settings_cache()
        self.assertEqual(trading_settings._fallback_cache.offer_min_quantity, 12)
        self.assertEqual(trading_settings._fallback_timestamp, 50)

    async def test_sync_loader_bridge_and_shared_cache_failure_fallbacks(self):
        async def async_loader():
            return trading_settings.TradingSettings(offer_min_quantity=17)

        loaded = await asyncio.to_thread(trading_settings._run_async_settings_loader_sync, async_loader)
        self.assertEqual(loaded.offer_min_quantity, 17)

        with patch(
            'core.trading_settings._run_async_settings_loader_sync',
            side_effect=RuntimeError('redis bridge failed'),
        ), patch('core.trading_settings.load_trading_settings', return_value=trading_settings.TradingSettings(offer_min_quantity=13)), patch(
            'core.trading_settings.time.time', return_value=30
        ), patch.object(trading_settings, 'logger') as logger:
            loaded = trading_settings.get_trading_settings()

        self.assertEqual(loaded.offer_min_quantity, 13)
        logger.debug.assert_called_once()

        with patch('core.trading_settings._run_async_settings_loader_sync', side_effect=RuntimeError('db bridge failed')), patch(
            'core.trading_settings._load_from_json', return_value={}
        ), patch.object(trading_settings, 'logger'):
            loaded = trading_settings.load_trading_settings()
        self.assertIsInstance(loaded, trading_settings.TradingSettings)

    async def test_sync_getter_prefers_shared_redis_cache_over_stale_fallback(self):
        stale = trading_settings.TradingSettings(max_active_offers=4)
        fresh = trading_settings.TradingSettings(max_active_offers=10)
        trading_settings._fallback_cache = stale
        trading_settings._fallback_timestamp = 10

        with patch(
            'core.trading_settings._run_async_settings_loader_sync',
            return_value=fresh,
        ) as sync_loader, patch('core.trading_settings.time.time', return_value=20):
            loaded = trading_settings.get_trading_settings()

        sync_loader.assert_called_once_with(trading_settings._get_from_redis_cache)
        self.assertIs(loaded, fresh)
        self.assertIs(trading_settings._fallback_cache, fresh)
        self.assertEqual(trading_settings._fallback_timestamp, 20)

    async def test_refresh_settings_cache_async(self):
        settings = trading_settings.TradingSettings(offer_max_quantity=90)
        with patch('core.trading_settings.load_trading_settings_async', AsyncMock(return_value=settings)), patch(
            'core.trading_settings._set_redis_cache', AsyncMock()
        ) as set_redis_cache, patch('core.trading_settings.time.time', return_value=77):
            await trading_settings.refresh_settings_cache_async()

        set_redis_cache.assert_awaited_once_with(settings)
        self.assertIs(trading_settings._fallback_cache, settings)
        self.assertEqual(trading_settings._fallback_timestamp, 77)

    async def test_save_get_and_update_setting(self):
        existing = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(side_effect=lambda model, key: existing if key == 'offer_min_quantity' else None)
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        with patch('core.db.AsyncSessionLocal', return_value=_AsyncSessionContext(session)), patch(
            'core.trading_settings.refresh_settings_cache_async', AsyncMock()
        ) as refresh_cache:
            ok = await trading_settings.save_trading_settings_async(
                {
                    'offer_min_quantity': 9,
                    'offer_max_quantity': 80,
                    '_private': 1,
                    'callable': lambda: None,
                }
            )

        self.assertTrue(ok)
        self.assertEqual(existing.value, json.dumps(9))
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        refresh_cache.assert_awaited_once()

        with patch('core.trading_settings.get_trading_settings', return_value=trading_settings.TradingSettings(offer_min_quantity=14)):
            self.assertEqual(trading_settings.get_setting('offer_min_quantity'), 14)

        with patch('core.trading_settings.get_trading_settings_async', AsyncMock(return_value=trading_settings.TradingSettings())), patch(
            'core.trading_settings.save_trading_settings_async', AsyncMock(return_value=True)
        ) as save_async:
            self.assertTrue(await trading_settings.update_setting_async('offer_min_quantity', 15))
            self.assertFalse(await trading_settings.update_setting_async('missing_key', 99))

        save_async.assert_awaited_once()

    async def test_save_trading_settings_async_returns_false_on_error(self):
        with patch('core.db.AsyncSessionLocal', side_effect=RuntimeError('db down')), patch.object(
            trading_settings, 'logger'
        ) as logger:
            self.assertFalse(await trading_settings.save_trading_settings_async({'offer_min_quantity': 1}))
        logger.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()