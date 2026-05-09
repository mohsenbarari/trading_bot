import unittest
from unittest.mock import AsyncMock, patch

from core import redis as redis_manager


class CoreRedisManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        redis_manager._redis_client = None

    async def asyncTearDown(self):
        redis_manager._redis_client = None

    async def test_init_redis_creates_singleton_once(self):
        fake_client = AsyncMock()
        fake_client.ping = AsyncMock()

        with patch('core.redis.redis.Redis', return_value=fake_client) as redis_ctor:
            first = await redis_manager.init_redis()
            second = await redis_manager.init_redis()

        self.assertIs(first, fake_client)
        self.assertIs(second, fake_client)
        fake_client.ping.assert_awaited_once()
        redis_ctor.assert_called_once()

    async def test_close_redis_closes_singleton_and_resets_it(self):
        fake_client = AsyncMock()
        redis_manager._redis_client = fake_client

        await redis_manager.close_redis()

        fake_client.close.assert_awaited_once()
        self.assertIsNone(redis_manager._redis_client)

    async def test_get_redis_client_requires_initialization(self):
        with self.assertRaises(RuntimeError):
            redis_manager.get_redis_client()

    async def test_get_redis_yields_temporary_client_when_uninitialized(self):
        temp_client = AsyncMock()
        with patch('core.redis.redis.Redis', return_value=temp_client):
            agen = redis_manager.get_redis()
            yielded = await anext(agen)
            self.assertIs(yielded, temp_client)
            await agen.aclose()

        temp_client.close.assert_awaited_once()

    async def test_get_redis_yields_singleton_without_closing_it(self):
        fake_client = AsyncMock()
        redis_manager._redis_client = fake_client

        agen = redis_manager.get_redis()
        yielded = await anext(agen)
        self.assertIs(yielded, fake_client)
        await agen.aclose()

        fake_client.close.assert_not_awaited()

    async def test_check_redis_connection_handles_success_and_failure(self):
        ok_client = AsyncMock()
        ok_client.ping = AsyncMock()
        redis_manager._redis_client = ok_client
        self.assertTrue(await redis_manager.check_redis_connection())

        broken_client = AsyncMock()
        broken_client.ping = AsyncMock(side_effect=RuntimeError('down'))
        redis_manager._redis_client = broken_client
        self.assertFalse(await redis_manager.check_redis_connection())


if __name__ == '__main__':
    unittest.main()