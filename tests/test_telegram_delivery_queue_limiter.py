import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
)
from core.telegram_delivery_queue_limiter import (
    RedisTelegramDeliveryLimiter,
    TelegramDeliveryLimiterConfigurationError,
    TelegramDeliveryLimiterUnavailableError,
    configured_redis_telegram_delivery_limiter,
)
from core.utils import utc_now


class _FakeRedis:
    def __init__(self, *eval_results):
        self.eval_results = list(eval_results)
        self.eval_calls = []
        self.set_calls = []
        self.delete_calls = []

    async def eval(self, *args):
        self.eval_calls.append(args)
        result = self.eval_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def set(self, *args):
        self.set_calls.append(args)
        return True

    async def delete(self, *args):
        self.delete_calls.append(args)
        return len(args)


def _job(*, bot="primary", destination="channel:-100123"):
    return SimpleNamespace(bot_identity=bot, destination_key=destination)


def _limiter(redis):
    return RedisTelegramDeliveryLimiter(
        redis=redis,
        bot_min_interval_seconds=0.035,
        destination_min_interval_seconds=1.05,
        rate_limit_probe_delay_seconds=0.1,
        global_rate_limit_window_seconds=2.0,
        key_ttl_seconds=60,
    )


class TelegramDeliveryQueueLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_maps_allow_cadence_and_circuit_responses(self):
        now = utc_now()
        redis = _FakeRedis(
            [1, int(now.timestamp() * 1000), 0],
            [0, int((now + timedelta(seconds=0.35)).timestamp() * 1000), 1],
            [0, int((now + timedelta(seconds=1.2)).timestamp() * 1000), 2],
            [-1, 0, 3],
        )
        limiter = _limiter(redis)

        allowed = await limiter.acquire(_job(), now=now)
        bot_wait = await limiter.acquire(_job(), now=now)
        destination_wait = await limiter.acquire(_job(), now=now)
        circuit = await limiter.acquire(_job(), now=now)

        self.assertTrue(allowed.allowed)
        self.assertEqual(bot_wait.wait_reason, "bot_lane")
        self.assertAlmostEqual(bot_wait.retry_after_seconds, 0.35, places=2)
        self.assertEqual(destination_wait.wait_reason, "destination_gate")
        self.assertAlmostEqual(destination_wait.retry_after_seconds, 1.2, places=2)
        self.assertEqual(circuit.wait_reason, "circuit")
        self.assertEqual(circuit.retry_after_seconds, 30.0)

    async def test_destination_is_hashed_in_redis_keys_and_never_rendered(self):
        raw_destination = "private-user-sensitive-919191"
        now = utc_now()
        redis = _FakeRedis([1, int(now.timestamp() * 1000), 0])
        limiter = _limiter(redis)

        await limiter.acquire(_job(destination=raw_destination), now=now)

        rendered_call = repr(redis.eval_calls[0])
        self.assertNotIn(raw_destination, rendered_call)
        self.assertNotIn(raw_destination, repr(limiter))
        self.assertIn(":destination:", rendered_call)

    async def test_invalid_or_failed_redis_response_blocks_future_admission_locally(self):
        now = utc_now()
        for first_result, reason in (
            ([99, 0, 0], "telegram_limiter_invalid_redis_response"),
            (ConnectionError("down"), "telegram_limiter_redis_unavailable"),
        ):
            with self.subTest(reason=reason):
                redis = _FakeRedis(first_result)
                limiter = _limiter(redis)
                with self.assertRaisesRegex(
                    TelegramDeliveryLimiterUnavailableError,
                    reason,
                ):
                    await limiter.acquire(_job(), now=now)
                with self.assertRaisesRegex(
                    TelegramDeliveryLimiterUnavailableError,
                    reason,
                ):
                    await limiter.acquire(_job(), now=now)
                self.assertEqual(len(redis.eval_calls), 1)

    async def test_naive_clock_is_rejected_before_redis_admission(self):
        redis = _FakeRedis()
        limiter = _limiter(redis)
        with self.assertRaisesRegex(
            TelegramDeliveryLimiterUnavailableError,
            "timestamp_must_be_timezone_aware",
        ):
            await limiter.acquire(_job(), now=datetime(2026, 7, 16, 12, 0, 0))
        self.assertEqual(redis.eval_calls, [])

    async def test_retry_after_ttl_can_never_expire_before_large_provider_deadline(self):
        now = utc_now()
        until = now + timedelta(seconds=1_000_000)
        redis = _FakeRedis([1, int(until.timestamp() * 1000)])
        limiter = _limiter(redis)
        decision = TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.RETRY_PENDING,
            reason="telegram_rate_limited",
            destination_cooldown_until=until,
        )

        await limiter.observe(_job(), decision, now=now)

        ttl_ms = int(redis.eval_calls[0][-1])
        self.assertGreaterEqual(ttl_ms, 1_000_000_000 + 60_000)

    async def test_cadence_key_ttl_cannot_be_configured_shorter_than_interval(self):
        now = utc_now()
        redis = _FakeRedis([1, int(now.timestamp() * 1000), 0])
        limiter = RedisTelegramDeliveryLimiter(
            redis=redis,
            bot_min_interval_seconds=0.035,
            destination_min_interval_seconds=1_000.0,
            rate_limit_probe_delay_seconds=0.1,
            global_rate_limit_window_seconds=2.0,
            key_ttl_seconds=1,
        )

        await limiter.acquire(_job(), now=now)

        ttl_ms = int(redis.eval_calls[0][-1])
        self.assertGreaterEqual(ttl_ms, 1_000_000 + 60_000)

    async def test_pause_outcomes_write_only_the_scoped_block_key(self):
        cases = (
            (TelegramDeliveryOutcome.BOT_PAUSED, ":bot:primary:blocked"),
            (TelegramDeliveryOutcome.DESTINATION_PAUSED, ":destination:"),
            (TelegramDeliveryOutcome.GATEWAY_PAUSED, ":gateway:blocked"),
        )
        for outcome, expected_key in cases:
            with self.subTest(outcome=outcome):
                redis = _FakeRedis()
                limiter = _limiter(redis)
                await limiter.observe(
                    _job(destination="raw-private-destination"),
                    TelegramDeliveryDecision(outcome=outcome, reason="paused"),
                    now=utc_now(),
                )
                self.assertEqual(len(redis.set_calls), 1)
                self.assertIn(expected_key, redis.set_calls[0][0])
                self.assertNotIn("raw-private-destination", repr(redis.set_calls))

    async def test_resume_validates_scope_and_only_clears_local_block_after_redis_success(self):
        redis = _FakeRedis()
        limiter = _limiter(redis)
        with self.assertRaisesRegex(
            TelegramDeliveryLimiterUnavailableError,
            "telegram_limiter_destination_missing",
        ):
            await limiter.resume_destination("")

        await limiter.resume_bot("primary")
        await limiter.resume_destination("channel:-100")
        await limiter.resume_gateway()
        self.assertEqual(len(redis.delete_calls), 3)

        failing = _limiter(_FakeRedis())

        async def fail_delete(*_args):
            raise ConnectionError("down")

        failing.redis.delete = fail_delete
        with self.assertRaisesRegex(
            TelegramDeliveryLimiterUnavailableError,
            "telegram_limiter_resume_failed",
        ):
            await failing.resume_gateway()
        with self.assertRaisesRegex(
            TelegramDeliveryLimiterUnavailableError,
            "telegram_limiter_resume_failed",
        ):
            await failing.acquire(_job(), now=utc_now())

    def test_configuration_rejects_nonpositive_or_nonfinite_rate_settings(self):
        for name, value in (
            ("bot_min_interval_seconds", 0),
            ("destination_min_interval_seconds", -1),
            ("rate_limit_probe_delay_seconds", float("nan")),
            ("global_rate_limit_window_seconds", float("inf")),
        ):
            values = {
                "redis": _FakeRedis(),
                "bot_min_interval_seconds": 0.035,
                "destination_min_interval_seconds": 1.05,
                "rate_limit_probe_delay_seconds": 0.1,
                "global_rate_limit_window_seconds": 2.0,
            }
            values[name] = value
            with self.subTest(name=name), self.assertRaisesRegex(
                TelegramDeliveryLimiterConfigurationError,
                name,
            ):
                RedisTelegramDeliveryLimiter(**values)

    def test_configured_factory_copies_all_explicit_settings(self):
        settings = SimpleNamespace(
            telegram_delivery_queue_bot_min_interval_seconds=0.04,
            telegram_delivery_queue_destination_min_interval_seconds=1.1,
            telegram_delivery_queue_rate_limit_probe_delay_seconds=0.2,
            telegram_delivery_queue_global_rate_limit_window_seconds=3.0,
            telegram_delivery_queue_limiter_key_ttl_seconds=900,
        )
        limiter = configured_redis_telegram_delivery_limiter(
            _FakeRedis(),
            settings=settings,
        )
        self.assertEqual(limiter.bot_min_interval_seconds, 0.04)
        self.assertEqual(limiter.destination_min_interval_seconds, 1.1)
        self.assertEqual(limiter.rate_limit_probe_delay_seconds, 0.2)
        self.assertEqual(limiter.global_rate_limit_window_seconds, 3.0)
        self.assertEqual(limiter.key_ttl_seconds, 900)


if __name__ == "__main__":
    unittest.main()
