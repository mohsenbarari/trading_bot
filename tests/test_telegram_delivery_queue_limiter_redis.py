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
    return SimpleNamespace(
        id=f"{bot}:{destination}",
        bot_identity=bot,
        destination_key=destination,
    )


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
            rate_limit_probe_lease_seconds=30.0,
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
        await asyncio.sleep(0.22)
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
        self.assertGreaterEqual(primary_global.retry_after_seconds, 2.7)
        self.assertTrue(editor_still_independent.allowed)

    async def test_explicit_bot_cooldown_applies_with_empty_recent_429_history(self):
        now = utc_now()
        job = _job("primary", "channel:durable-bot-cooldown")
        self.assertEqual(await self.redis.keys("*:recent-429"), [])

        await self.limiter.observe(
            job,
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.RETRY_PENDING,
                reason="telegram_rate_limited",
                destination_cooldown_until=now + timedelta(seconds=2),
                bot_cooldown_until=now + timedelta(seconds=5),
            ),
            now=now,
        )

        primary = await self.limiter.acquire(
            _job("primary", "private:unrelated-after-restart"),
            now=now,
        )
        editor = await self.limiter.acquire(
            _job("channel_editor", "private:editor-after-restart"),
            now=now,
        )
        self.assertFalse(primary.allowed)
        self.assertEqual(primary.wait_reason, "bot_lane")
        self.assertGreaterEqual(primary.retry_after_seconds, 4.99)
        self.assertTrue(editor.allowed)

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

    async def test_first_429_allows_exactly_one_probe_until_its_result(self):
        now = utc_now()
        await self.limiter.observe(
            _job("primary", "channel:probe-source"),
            _rate_limited(now + timedelta(seconds=5)),
            now=now,
        )

        probe_time = now + timedelta(milliseconds=110)
        await asyncio.sleep(0.11)
        candidates = [
            _job("primary", f"private:probe-candidate-{index}")
            for index in range(50)
        ]
        admissions = await asyncio.gather(
            *(self.limiter.acquire(job, now=probe_time) for job in candidates)
        )
        self.assertEqual(sum(item.allowed for item in admissions), 1)
        allowed_index = next(
            index for index, item in enumerate(admissions) if item.allowed
        )
        self.assertTrue(admissions[allowed_index].is_rate_limit_probe)
        self.assertEqual(
            {item.wait_reason for item in admissions if not item.allowed},
            {"bot_probe_inflight"},
        )

        still_waiting = await self.limiter.acquire(
            _job("primary", "private:probe-still-waiting"),
            now=probe_time + timedelta(seconds=1),
        )
        self.assertFalse(still_waiting.allowed)
        self.assertEqual(still_waiting.wait_reason, "bot_probe_inflight")

        await asyncio.sleep(0.21)
        await self.limiter.observe(
            candidates[allowed_index],
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.SENT,
                reason="sent",
            ),
            now=probe_time + timedelta(seconds=1),
        )
        after_result = await self.limiter.acquire(
            _job("primary", "private:probe-after-result"),
            now=probe_time + timedelta(seconds=1, milliseconds=10),
        )
        self.assertTrue(after_result.allowed)
        self.assertFalse(after_result.is_rate_limit_probe)

    async def test_cancelled_probe_is_rearmed_and_only_owner_can_release_it(self):
        now = utc_now()
        await self.limiter.observe(
            _job("primary", "channel:cancelled-probe-source"),
            _rate_limited(now + timedelta(seconds=5)),
            now=now,
        )
        first_probe_job = _job("primary", "private:cancelled-probe-owner")
        await asyncio.sleep(0.11)
        first_probe = await self.limiter.acquire(
            first_probe_job,
            now=now + timedelta(milliseconds=110),
        )
        self.assertTrue(first_probe.allowed)
        self.assertTrue(first_probe.is_rate_limit_probe)

        cancellation = TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.ALREADY_RESOLVED,
            reason="rate_limit_probe_cancelled_before_dispatch",
        )
        await self.limiter.observe(
            _job("primary", "private:not-the-probe-owner"),
            cancellation,
            now=now + timedelta(milliseconds=310),
        )
        still_owned = await self.limiter.acquire(
            _job("primary", "private:still-blocked-by-owner"),
            now=now + timedelta(milliseconds=320),
        )
        self.assertFalse(still_owned.allowed)
        self.assertEqual(still_owned.wait_reason, "bot_probe_inflight")

        await asyncio.sleep(0.21)
        await self.limiter.observe(
            first_probe_job,
            cancellation,
            now=now + timedelta(milliseconds=330),
        )
        candidates = [
            _job("primary", f"private:replacement-probe-{index}")
            for index in range(30)
        ]
        admissions = await asyncio.gather(
            *(
                self.limiter.acquire(
                    job,
                    now=now + timedelta(milliseconds=340),
                )
                for job in candidates
            )
        )
        self.assertEqual(sum(item.allowed for item in admissions), 1)
        replacement = next(item for item in admissions if item.allowed)
        self.assertTrue(replacement.is_rate_limit_probe)
        self.assertEqual(
            {item.wait_reason for item in admissions if not item.allowed},
            {"bot_probe_inflight"},
        )

    async def test_expired_probe_lease_elects_exactly_one_replacement_probe(self):
        limiter = RedisTelegramDeliveryLimiter(
            redis=self.redis,
            bot_min_interval_seconds=0.02,
            destination_min_interval_seconds=0.02,
            rate_limit_probe_delay_seconds=0.01,
            global_rate_limit_window_seconds=2.0,
            # Keep the replacement lease comfortably longer than the
            # concurrent Redis round-trip. The original 50 ms fixture could
            # expire again while the 30 contenders were still being served,
            # making a safe pacing rejection appear as ``bot_lane`` and
            # turning scheduler load into a test-only race.
            rate_limit_probe_lease_seconds=1.0,
            key_ttl_seconds=60,
            namespace="telegram:delivery:stage3-probe-expiry-test",
        )
        now = utc_now()
        await limiter.observe(
            _job("primary", "channel:probe-expiry-source"),
            _rate_limited(now + timedelta(seconds=2)),
            now=now,
        )
        await asyncio.sleep(0.02)
        owner = await limiter.acquire(
            _job("primary", "private:probe-expiry-owner"),
            now=now + timedelta(milliseconds=20),
        )
        self.assertTrue(owner.allowed)
        self.assertTrue(owner.is_rate_limit_probe)

        probe_key = f"{limiter.namespace}:bot:primary:probe-inflight"
        self.assertTrue(await self.redis.exists(probe_key))
        self.assertTrue(await self.redis.pexpire(probe_key, 1))
        for _ in range(100):
            if not await self.redis.exists(probe_key):
                break
            await asyncio.sleep(0.002)
        self.assertFalse(await self.redis.exists(probe_key))
        await asyncio.sleep(0.03)

        # Use one stable logical instant after the previous pacing window;
        # only the atomic probe election, not wall-clock test scheduling,
        # should decide the 30 concurrent outcomes.
        replacement_time = now + timedelta(milliseconds=100)
        candidates = [
            _job("primary", f"private:probe-expiry-replacement-{index}")
            for index in range(30)
        ]
        admissions = await asyncio.gather(
            *(limiter.acquire(job, now=replacement_time) for job in candidates)
        )
        self.assertEqual(sum(item.allowed for item in admissions), 1)
        self.assertTrue(next(item for item in admissions if item.allowed).is_rate_limit_probe)
        self.assertEqual(
            {item.wait_reason for item in admissions if not item.allowed},
            {"bot_probe_inflight"},
        )

    async def test_preflight_prepare_never_bypasses_inflight_probe(self):
        limiter = RedisTelegramDeliveryLimiter(
            redis=self.redis,
            bot_min_interval_seconds=0.02,
            destination_min_interval_seconds=0.02,
            rate_limit_probe_delay_seconds=0.01,
            global_rate_limit_window_seconds=2.0,
            rate_limit_probe_lease_seconds=0.05,
            key_ttl_seconds=60,
            namespace="telegram:delivery:stage3-preflight-probe-test",
        )
        now = utc_now()
        await limiter.observe(
            _job("primary", "channel:preflight-probe-source"),
            _rate_limited(now + timedelta(seconds=2)),
            now=now,
        )
        await asyncio.sleep(0.02)
        probe = await limiter.acquire(
            _job("primary", "private:preflight-probe-owner"),
            now=now + timedelta(milliseconds=20),
        )
        self.assertTrue(probe.is_rate_limit_probe)
        self.assertFalse(await limiter.prepare_preflight("primary"))
        self.assertFalse(await limiter.preflight_gate_open("primary"))

        await asyncio.sleep(0.08)
        self.assertFalse(await limiter.preflight_gate_open("primary"))
        self.assertTrue(await limiter.prepare_preflight("primary"))
        self.assertTrue(await limiter.preflight_gate_open("primary"))
        after_prepare = await limiter.acquire(
            _job("primary", "private:preflight-after-expiry"),
            now=utc_now(),
        )
        self.assertTrue(after_prepare.allowed)
        self.assertFalse(after_prepare.is_rate_limit_probe)

    async def test_preflight_prepare_never_bypasses_active_bot_cooldown(self):
        limiter = RedisTelegramDeliveryLimiter(
            redis=self.redis,
            bot_min_interval_seconds=0.02,
            destination_min_interval_seconds=0.02,
            rate_limit_probe_delay_seconds=0.01,
            global_rate_limit_window_seconds=2.0,
            rate_limit_probe_lease_seconds=0.05,
            key_ttl_seconds=60,
            namespace="telegram:delivery:stage3-preflight-cooldown-test",
        )
        await limiter.extend_bot_cooldown(
            "primary",
            until=utc_now() + timedelta(seconds=0.06),
        )

        self.assertFalse(await limiter.prepare_preflight("primary"))
        await asyncio.sleep(0.08)
        self.assertTrue(await limiter.prepare_preflight("primary"))
        self.assertTrue(await limiter.preflight_gate_open("primary"))

    async def test_probe_429_creates_bot_cooldown_and_keeps_other_bot_independent(self):
        now = utc_now()
        await self.limiter.observe(
            _job("primary", "channel:probe-429-source"),
            _rate_limited(now + timedelta(seconds=2)),
            now=now,
        )
        probe_job = _job("primary", "private:probe-429-destination")
        probe_time = now + timedelta(milliseconds=110)
        await asyncio.sleep(0.11)
        probe = await self.limiter.acquire(probe_job, now=probe_time)
        self.assertTrue(probe.allowed)
        self.assertTrue(probe.is_rate_limit_probe)

        await self.limiter.observe(
            probe_job,
            _rate_limited(now + timedelta(seconds=5)),
            now=probe_time + timedelta(milliseconds=10),
        )
        primary = await self.limiter.acquire(
            _job("primary", "private:blocked-after-probe-429"),
            now=probe_time + timedelta(milliseconds=20),
        )
        editor = await self.limiter.acquire(
            _job("channel_editor", "private:independent-after-probe-429"),
            now=probe_time + timedelta(milliseconds=20),
        )
        self.assertFalse(primary.allowed)
        self.assertEqual(primary.wait_reason, "bot_lane")
        self.assertGreater(primary.retry_after_seconds, 4.8)
        self.assertTrue(editor.allowed)

    async def test_durable_replay_extension_is_idempotent_monotonic_and_scoped(self):
        now = utc_now()
        destination = "channel:rehydrated-rate-limit"
        job = _job("primary", destination)
        await self.limiter.extend_destination_cooldown(
            job,
            until=now + timedelta(seconds=127.1),
        )
        await self.limiter.extend_destination_cooldown(
            job,
            until=now + timedelta(seconds=10),
        )

        shared_destination = await self.limiter.acquire(
            _job("channel_editor", destination),
            now=now,
        )
        unrelated_destination = await self.limiter.acquire(
            _job("channel_editor", "channel:rehydrated-unrelated"),
            now=now,
        )
        self.assertFalse(shared_destination.allowed)
        self.assertEqual(shared_destination.wait_reason, "destination_gate")
        self.assertGreaterEqual(shared_destination.retry_after_seconds, 127.09)
        self.assertTrue(unrelated_destination.allowed)

    async def test_bot_replay_extension_is_idempotent_monotonic_and_bot_scoped(self):
        now = utc_now()
        maximum = now + timedelta(seconds=127.1)
        await self.limiter.extend_bot_cooldown("primary", until=maximum)
        await self.limiter.extend_bot_cooldown(
            "primary",
            until=now + timedelta(seconds=10),
        )
        await self.limiter.extend_bot_cooldown("primary", until=maximum)

        primary = await self.limiter.acquire(
            _job("primary", "private:bot-replay-primary"),
            now=now,
        )
        editor = await self.limiter.acquire(
            _job("channel_editor", "private:bot-replay-editor"),
            now=now,
        )
        self.assertFalse(primary.allowed)
        self.assertEqual(primary.wait_reason, "bot_lane")
        self.assertGreaterEqual(primary.retry_after_seconds, 127.09)
        self.assertTrue(editor.allowed)

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

    async def test_skewed_worker_clocks_cannot_bypass_atomic_min_interval(self):
        actual = utc_now()
        job = _job("primary", "channel:clock-skew-cadence")
        admissions = await asyncio.gather(
            self.limiter.acquire(job, now=actual + timedelta(days=365)),
            self.limiter.acquire(job, now=actual - timedelta(days=365)),
            *(self.limiter.acquire(job, now=actual) for _ in range(48)),
        )

        self.assertEqual(sum(item.allowed for item in admissions), 1)
        self.assertEqual(
            {item.wait_reason for item in admissions if not item.allowed},
            {"destination_gate"},
        )

    async def test_skewed_worker_clock_cannot_bypass_or_extend_cooldown(self):
        actual = utc_now()
        job = _job("primary", "channel:clock-skew-cooldown")
        await self.limiter.extend_destination_cooldown(
            job,
            until=actual + timedelta(milliseconds=250),
        )

        future_clock = await self.limiter.acquire(
            job,
            now=actual + timedelta(days=365),
        )
        self.assertFalse(future_clock.allowed)
        self.assertEqual(future_clock.wait_reason, "destination_gate")
        self.assertGreater(future_clock.retry_after_seconds, 0.15)

        await asyncio.sleep(0.28)
        past_clock = await self.limiter.acquire(
            job,
            now=actual - timedelta(days=365),
        )
        self.assertTrue(past_clock.allowed)


if __name__ == "__main__":
    unittest.main()
