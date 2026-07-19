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
        if not self.eval_results:
            return 0
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
    return SimpleNamespace(
        id=f"{bot}:{destination}",
        bot_identity=bot,
        destination_key=destination,
    )


def _limiter(redis):
    return RedisTelegramDeliveryLimiter(
        redis=redis,
        bot_min_interval_seconds=0.035,
        destination_min_interval_seconds=1.05,
        rate_limit_probe_delay_seconds=0.1,
        global_rate_limit_window_seconds=2.0,
        rate_limit_probe_lease_seconds=30.0,
        key_ttl_seconds=60,
    )


class TelegramDeliveryQueueLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_maps_allow_cadence_and_circuit_responses(self):
        now = utc_now()
        now_ms = int(now.timestamp() * 1000)
        redis = _FakeRedis(
            [1, now_ms, 0, now_ms],
            [0, now_ms + 350, 1, now_ms],
            [0, now_ms + 1200, 2, now_ms],
            [-1, 0, 3, now_ms],
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
        now_ms = int(now.timestamp() * 1000)
        redis = _FakeRedis([1, now_ms, 0, now_ms])
        limiter = _limiter(redis)

        await limiter.acquire(_job(destination=raw_destination), now=now)

        rendered_call = repr(redis.eval_calls[0])
        self.assertNotIn(raw_destination, rendered_call)
        self.assertNotIn(raw_destination, repr(limiter))
        self.assertIn(":destination:", rendered_call)

    async def test_invalid_or_failed_redis_response_blocks_future_admission_locally(self):
        now = utc_now()
        for first_result, reason in (
            ([99, 0, 0, int(now.timestamp() * 1000)], "telegram_limiter_invalid_redis_response"),
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

    async def test_explicit_bot_cooldown_is_atomic_with_destination_set_max(self):
        now = utc_now()
        destination_until = now + timedelta(seconds=5)
        bot_until = now + timedelta(seconds=127)
        redis = _FakeRedis([2, int(bot_until.timestamp() * 1000)])
        limiter = _limiter(redis)

        await limiter.observe(
            _job(bot="primary", destination="channel:atomic-bot-cooldown"),
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.RETRY_PENDING,
                reason="telegram_rate_limited",
                destination_cooldown_until=destination_until,
                bot_cooldown_until=bot_until,
            ),
            now=now,
        )

        self.assertEqual(len(redis.eval_calls), 1)
        call = redis.eval_calls[0]
        script = call[0]
        self.assertEqual(call[1], 5)
        self.assertIn("local function set_max", script)
        self.assertIn("set_max(KEYS[1], retry_until_ms, ttl_ms)", script)
        self.assertIn("set_max(KEYS[2], durable_bot_until_ms, ttl_ms)", script)
        self.assertEqual(call[8], 5_000)
        self.assertEqual(call[11], 127_000)
        self.assertGreaterEqual(
            int(call[13]),
            int((bot_until - now).total_seconds() * 1000) + 60_000,
        )
        self.assertEqual(redis.set_calls, [])

    async def test_cancelled_probe_compare_and_sets_owner_and_rearms_requirement(self):
        redis = _FakeRedis(1)
        limiter = _limiter(redis)
        await limiter.observe(
            _job(bot="primary", destination="private:cancelled-probe"),
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.ALREADY_RESOLVED,
                reason="rate_limit_probe_cancelled_before_dispatch",
            ),
            now=utc_now(),
        )

        self.assertEqual(len(redis.eval_calls), 1)
        call = redis.eval_calls[0]
        self.assertEqual(call[1], 2)
        self.assertIn("redis.call('get', KEYS[1]) == ARGV[1]", call[0])
        self.assertIn("redis.call('set', KEYS[2], 'required'", call[0])
        self.assertIn(":probe-inflight", call[2])
        self.assertIn(":probe-required", call[3])

    async def test_recent_429_script_excludes_future_observations(self):
        now = utc_now()
        redis = _FakeRedis([1, int(now.timestamp() * 1000)])
        limiter = _limiter(redis)
        await limiter.observe(
            _job(),
            TelegramDeliveryDecision(
                outcome=TelegramDeliveryOutcome.RETRY_PENDING,
                reason="telegram_rate_limited",
                destination_cooldown_until=now + timedelta(seconds=5),
            ),
            now=now,
        )
        self.assertIn(
            "seen_at <= now_ms and now_ms - seen_at <= window_ms",
            redis.eval_calls[0][0],
        )

    async def test_preflight_prepare_waits_for_probe_owner_then_retires_requirement(self):
        redis = _FakeRedis(0, 1)
        limiter = _limiter(redis)

        self.assertFalse(await limiter.prepare_preflight("primary"))
        self.assertTrue(await limiter.prepare_preflight("primary"))
        self.assertEqual(len(redis.eval_calls), 2)
        for call in redis.eval_calls:
            self.assertEqual(call[1], 3)
            self.assertIn(":probe-inflight", call[2])
            self.assertIn(":probe-required", call[3])
            self.assertIn(":next", call[4])
            self.assertIn("redis.call('exists', KEYS[1])", call[0])
            self.assertIn("redis.call('TIME')", call[0])
            self.assertIn("bot_next > now_ms", call[0])

        gate_redis = _FakeRedis(0, 1)
        gate_limiter = _limiter(gate_redis)
        self.assertFalse(await gate_limiter.preflight_gate_open("primary"))
        self.assertTrue(await gate_limiter.preflight_gate_open("primary"))
        self.assertIn("or redis.call('exists', KEYS[2])", gate_redis.eval_calls[0][0])

    async def test_cadence_key_ttl_cannot_be_configured_shorter_than_interval(self):
        now = utc_now()
        now_ms = int(now.timestamp() * 1000)
        redis = _FakeRedis([1, now_ms, 0, now_ms])
        limiter = RedisTelegramDeliveryLimiter(
            redis=redis,
            bot_min_interval_seconds=0.035,
            destination_min_interval_seconds=1_000.0,
            rate_limit_probe_delay_seconds=0.1,
            global_rate_limit_window_seconds=2.0,
            rate_limit_probe_lease_seconds=30.0,
            key_ttl_seconds=1,
        )

        await limiter.acquire(_job(), now=now)

        ttl_ms = int(redis.eval_calls[0][11])
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

    def test_hard_destination_block_is_bot_scoped_but_cadence_is_shared(self):
        limiter = _limiter(_FakeRedis())
        digest = "synthetic-destination-digest"
        primary = limiter._keys("primary", digest)
        editor = limiter._keys("channel_editor", digest)
        self.assertEqual(primary["destination_next"], editor["destination_next"])
        self.assertNotEqual(
            primary["destination_block"],
            editor["destination_block"],
        )

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
            ("rate_limit_probe_lease_seconds", 0),
        ):
            values = {
                "redis": _FakeRedis(),
                "bot_min_interval_seconds": 0.035,
                "destination_min_interval_seconds": 1.05,
                "rate_limit_probe_delay_seconds": 0.1,
                "global_rate_limit_window_seconds": 2.0,
                "rate_limit_probe_lease_seconds": 30.0,
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
        self.assertEqual(limiter.rate_limit_probe_lease_seconds, 30.0)
        self.assertEqual(limiter.key_ttl_seconds, 900)


if __name__ == "__main__":
    unittest.main()
