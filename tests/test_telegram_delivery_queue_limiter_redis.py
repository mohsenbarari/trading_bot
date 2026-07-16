import asyncio
from datetime import timedelta
import os
from types import SimpleNamespace
import unittest
from urllib.parse import urlparse

import redis.asyncio as redis_async

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
)
from core.telegram_delivery_queue_limiter import RedisTelegramDeliveryLimiter
from core.utils import utc_now


TEST_REDIS_URL = str(
    os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_REDIS_URL", "")
).strip()


def _validated_test_redis_url() -> str | None:
    if not TEST_REDIS_URL:
        return None
    parsed = urlparse(TEST_REDIS_URL)
    if (
        parsed.scheme != "redis"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != 56379
        or parsed.path != "/15"
    ):
        raise RuntimeError(
            "Telegram limiter tests require isolated localhost Redis port 56379 database 15"
        )
    return TEST_REDIS_URL


VALIDATED_REDIS_URL = _validated_test_redis_url()


def _job(bot: str, destination: str):
    return SimpleNamespace(bot_identity=bot, destination_key=destination)


def _rate_limited(until):
    return TelegramDeliveryDecision(
        outcome=TelegramDeliveryOutcome.RETRY_PENDING,
        reason="telegram_rate_limited",
        destination_cooldown_until=until,
    )


@unittest.skipUnless(
    VALIDATED_REDIS_URL,
    "set TELEGRAM_QUEUE_STAGE3_TEST_REDIS_URL for real Redis limiter tests",
)
class TelegramDeliveryQueueRedisLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_async.from_url(VALIDATED_REDIS_URL, decode_responses=True)
        await self.redis.ping()
        await self.redis.flushdb()
        self.limiter = RedisTelegramDeliveryLimiter(
            redis=self.redis,
            bot_min_interval_seconds=0.2,
            destination_min_interval_seconds=1.0,
            rate_limit_probe_delay_seconds=0.1,
            global_rate_limit_window_seconds=2.0,
            key_ttl_seconds=60,
            namespace="telegram:delivery:stage3-test",
        )

    async def asyncTearDown(self):
        await self.redis.flushdb()
        await self.redis.close()

    async def test_fifty_concurrent_requests_admit_exactly_one(self):
        now = utc_now()
        job = _job("primary", "channel:market-concurrent")
        results = await asyncio.gather(
            *(self.limiter.acquire(job, now=now) for _ in range(50))
        )
        self.assertEqual(sum(result.allowed for result in results), 1)
        self.assertEqual(
            {result.wait_reason for result in results if not result.allowed},
            {"destination_gate"},
        )

    async def test_bot_budgets_are_independent_but_destination_gate_is_shared(self):
        now = utc_now()
        primary, editor = await asyncio.gather(
            self.limiter.acquire(_job("primary", "private:user-a"), now=now),
            self.limiter.acquire(
                _job("channel_editor", "channel:market-a"),
                now=now,
            ),
        )
        self.assertTrue(primary.allowed)
        self.assertTrue(editor.allowed)

        await self.redis.flushdb()
        same_destination = await asyncio.gather(
            self.limiter.acquire(_job("primary", "channel:shared"), now=now),
            self.limiter.acquire(
                _job("channel_editor", "channel:shared"),
                now=now,
            ),
        )
        self.assertEqual(sum(result.allowed for result in same_destination), 1)
        self.assertEqual(
            [result.wait_reason for result in same_destination if not result.allowed],
            ["destination_gate"],
        )

    async def test_first_and_second_distinct_429_apply_only_expected_scopes(self):
        now = utc_now()
        first_until = now + timedelta(seconds=2)
        await self.limiter.observe(
            _job("primary", "channel:rate-a"),
            _rate_limited(first_until),
            now=now,
        )

        primary_probe = await self.limiter.acquire(
            _job("primary", "private:unrelated-a"),
            now=now,
        )
        editor_unrelated = await self.limiter.acquire(
            _job("channel_editor", "private:unrelated-b"),
            now=now,
        )
        self.assertFalse(primary_probe.allowed)
        self.assertEqual(primary_probe.wait_reason, "bot_lane")
        self.assertLessEqual(primary_probe.retry_after_seconds, 0.101)
        self.assertTrue(editor_unrelated.allowed)

        second_until = now + timedelta(seconds=3)
        await self.limiter.observe(
            _job("primary", "channel:rate-b"),
            _rate_limited(second_until),
            now=now + timedelta(milliseconds=20),
        )
        primary_global = await self.limiter.acquire(
            _job("primary", "private:unrelated-c"),
            now=now + timedelta(milliseconds=20),
        )
        editor_still_independent = await self.limiter.acquire(
            _job("channel_editor", "private:unrelated-d"),
            now=now + timedelta(milliseconds=220),
        )
        self.assertFalse(primary_global.allowed)
        self.assertEqual(primary_global.wait_reason, "bot_lane")
        self.assertGreaterEqual(primary_global.retry_after_seconds, 2.97)
        self.assertTrue(editor_still_independent.allowed)

    async def test_429_destination_cooldown_is_shared_across_bot_identities(self):
        now = utc_now()
        shared = "channel:shared-rate-limit"
        await self.limiter.observe(
            _job("channel_editor", shared),
            _rate_limited(now + timedelta(seconds=4)),
            now=now,
        )
        primary = await self.limiter.acquire(_job("primary", shared), now=now)
        self.assertFalse(primary.allowed)
        self.assertEqual(primary.wait_reason, "destination_gate")
        self.assertGreaterEqual(primary.retry_after_seconds, 3.99)

    async def test_bot_destination_and_gateway_pauses_require_scoped_resume(self):
        now = utc_now()
        await self.limiter.observe(
            _job("primary", "private:blocked-bot"),
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.BOT_PAUSED,
                reason="bot-paused",
            ),
            now=now,
        )
        blocked_bot = await self.limiter.acquire(
            _job("primary", "private:other"),
            now=now,
        )
        other_bot = await self.limiter.acquire(
            _job("channel_editor", "private:editor-other"),
            now=now,
        )
        self.assertFalse(blocked_bot.allowed)
        self.assertEqual(blocked_bot.wait_reason, "bot_lane")
        self.assertTrue(other_bot.allowed)
        await self.limiter.resume_bot("primary")
        self.assertTrue(
            (await self.limiter.acquire(_job("primary", "private:resumed"), now=now)).allowed
        )

        await self.redis.flushdb()
        destination = "channel:blocked-destination"
        await self.limiter.observe(
            _job("primary", destination),
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.DESTINATION_PAUSED,
                reason="destination-paused",
            ),
            now=now,
        )
        self.assertFalse(
            (await self.limiter.acquire(_job("channel_editor", destination), now=now)).allowed
        )
        await self.limiter.resume_destination(destination)
        self.assertTrue(
            (await self.limiter.acquire(_job("channel_editor", destination), now=now)).allowed
        )

        await self.redis.flushdb()
        await self.limiter.observe(
            _job("primary", "private:gateway"),
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.GATEWAY_PAUSED,
                reason="gateway-paused",
            ),
            now=now,
        )
        self.assertFalse(
            (await self.limiter.acquire(_job("primary", "private:g1"), now=now)).allowed
        )
        self.assertFalse(
            (
                await self.limiter.acquire(
                    _job("channel_editor", "private:g2"),
                    now=now,
                )
            ).allowed
        )
        await self.limiter.resume_gateway()
        self.assertTrue(
            (await self.limiter.acquire(_job("primary", "private:g3"), now=now)).allowed
        )

    async def test_redis_keyspace_never_contains_raw_destination(self):
        raw_destination = "private:phone-like-09120000000"
        await self.limiter.acquire(_job("primary", raw_destination), now=utc_now())
        keys = tuple(await self.redis.keys("*"))
        self.assertTrue(keys)
        self.assertNotIn(raw_destination, repr(keys))


if __name__ == "__main__":
    unittest.main()
