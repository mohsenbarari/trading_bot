import asyncio
import os
import unittest

import redis.asyncio as redis_async
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage


REDIS_URL = os.getenv("STAGE5_TEST_REDIS_URL", "").strip()


@unittest.skipUnless(REDIS_URL, "set STAGE5_TEST_REDIS_URL for Stage 5 event-isolation tests")
class Stage5EventIsolationRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_async.from_url(REDIS_URL, decode_responses=True)
        await self.redis.flushdb()
        self.storage = RedisStorage(redis=self.redis)
        self.isolation = self.storage.create_isolation(lock_kwargs={"timeout": 5})

    async def asyncTearDown(self):
        await self.isolation.close()
        await self.storage.close()
        await self.redis.flushdb()
        await self.redis.aclose()

    async def test_same_fsm_key_is_serialized_while_unrelated_user_proceeds(self):
        first_key = StorageKey(bot_id=1, chat_id=10, user_id=10)
        second_key = StorageKey(bot_id=1, chat_id=20, user_id=20)
        same_key_entered = asyncio.Event()
        unrelated_entered = asyncio.Event()

        async with self.isolation.lock(key=first_key):
            async def enter_same_key():
                async with self.isolation.lock(key=first_key):
                    same_key_entered.set()

            async def enter_unrelated_key():
                async with self.isolation.lock(key=second_key):
                    unrelated_entered.set()

            same_task = asyncio.create_task(enter_same_key())
            unrelated_task = asyncio.create_task(enter_unrelated_key())
            await asyncio.wait_for(unrelated_entered.wait(), timeout=1)
            self.assertFalse(same_key_entered.is_set())

        await asyncio.wait_for(same_task, timeout=1)
        await asyncio.wait_for(unrelated_task, timeout=1)
        self.assertTrue(same_key_entered.is_set())
