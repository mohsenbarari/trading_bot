import asyncio
import os
import unittest
from datetime import timedelta
from uuid import uuid4

import redis.asyncio as redis_async

from core.registration_contracts import OTPDeliveryStatus, OTPRequestStatus
from core.config import settings
from core.services.otp_delivery_state_service import (
    OTP_FALLBACK_DUE_KEY,
    arm_sms_fallback,
    build_otp_delivery_state,
    claim_sms_delivery,
    consume_otp_code,
    create_otp_delivery_state,
    due_otp_request_ids,
    isolate_invalid_otp_fallback_state,
    load_otp_delivery_state,
    mark_sms_provider_attempt_started,
    record_sms_delivery_result,
    schedule_sms_fallback,
    select_due_otp_requests,
)
from core.registration_observability import summarize_otp_fallback_queue
from core.utils import utc_now


REDIS_URL = os.getenv("STAGE6_TEST_REDIS_URL", "").strip()
TEST_STATE_SECRET = "stage6-real-redis-secret-0123456789abcdef"


@unittest.skipUnless(REDIS_URL, "set STAGE6_TEST_REDIS_URL to run real Redis Stage 6 tests")
class Stage6OTPDeliveryRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_state_secret = settings.otp_delivery_state_secret
        settings.otp_delivery_state_secret = TEST_STATE_SECRET
        self.redis = redis_async.from_url(REDIS_URL, decode_responses=True)
        await self.redis.flushdb()

    async def asyncTearDown(self):
        await self.redis.flushdb()
        await self.redis.aclose()
        settings.otp_delivery_state_secret = self.original_state_secret

    async def test_stage8_queue_summary_uses_real_redis_without_loading_sensitive_state(self):
        request_id = uuid4()
        now = utc_now()
        await self.redis.zadd(
            OTP_FALLBACK_DUE_KEY,
            {str(request_id): now.timestamp() - 2.5},
        )

        summary = await summarize_otp_fallback_queue(self.redis, now=now)

        self.assertEqual(summary.pending_count, 1)
        self.assertAlmostEqual(summary.lag_seconds, 2.5, places=1)

    async def test_one_code_schedule_claim_record_and_consume_lifecycle(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        state = build_otp_delivery_state(
            mobile=mobile,
            telegram_id=8_800_001,
            ttl_seconds=120,
        )
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=state,
                otp_code="12345",
                ttl_seconds=120,
            )
        )
        loaded = await load_otp_delivery_state(self.redis, mobile=mobile)
        self.assertEqual(loaded.otp_request_id, state.otp_request_id)
        self.assertFalse(hasattr(loaded, "otp_code"))

        sent_at = utc_now()
        due_at = sent_at + timedelta(seconds=40)
        recovery_at = due_at + timedelta(seconds=5)
        self.assertTrue(
            await arm_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                recovery_at=recovery_at,
            )
        )
        armed = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(armed.telegram_delivery_status, OTPDeliveryStatus.PENDING)
        self.assertEqual(
            await due_otp_request_ids(self.redis, now=due_at, limit=10),
            [],
        )
        self.assertTrue(
            await schedule_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                telegram_sent_at=sent_at,
                fallback_at=due_at,
            )
        )
        self.assertEqual(
            await due_otp_request_ids(self.redis, now=due_at, limit=10),
            [state.otp_request_id],
        )
        claim = await claim_sms_delivery(
            self.redis,
            state=loaded,
            require_due=True,
            now=due_at,
        )
        self.assertEqual(claim.otp_code, "12345")
        self.assertIsNone(
            await claim_sms_delivery(
                self.redis,
                state=loaded,
                require_due=False,
            )
        )
        self.assertTrue(
            await mark_sms_provider_attempt_started(self.redis, claim=claim)
        )
        self.assertTrue(
            await record_sms_delivery_result(
                self.redis,
                claim=claim,
                outcome=OTPDeliveryStatus.ACCEPTED,
            )
        )
        self.assertTrue(
            await consume_otp_code(
                self.redis,
                mobile=mobile,
                expected_code="12345",
            )
        )
        self.assertFalse(
            await consume_otp_code(
                self.redis,
                mobile=mobile,
                expected_code="12345",
            )
        )
        consumed = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(consumed.status, OTPRequestStatus.CONSUMED)

    async def test_concurrent_create_and_claim_have_exactly_one_winner(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        states = [
            build_otp_delivery_state(mobile=mobile, telegram_id=9_000_001, ttl_seconds=120),
            build_otp_delivery_state(mobile=mobile, telegram_id=9_000_001, ttl_seconds=120),
        ]
        created = await asyncio.gather(
            *(
                create_otp_delivery_state(
                    self.redis,
                    state=item,
                    otp_code=code,
                    ttl_seconds=120,
                )
                for item, code in zip(states, ("12345", "54321"), strict=True)
            )
        )
        self.assertEqual(created.count(True), 1)
        winner = await load_otp_delivery_state(self.redis, mobile=mobile)
        due = utc_now()
        await schedule_sms_fallback(
            self.redis,
            request_id=winner.otp_request_id,
            telegram_sent_at=due - timedelta(seconds=40),
            fallback_at=due,
        )
        claims = await asyncio.gather(
            claim_sms_delivery(self.redis, state=winner, require_due=True, now=due),
            claim_sms_delivery(self.redis, state=winner, require_due=False, now=due),
        )
        self.assertEqual(sum(item is not None for item in claims), 1)

    async def test_verify_before_due_atomically_cancels_future_sms_claim(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        state = build_otp_delivery_state(
            mobile=mobile,
            telegram_id=9_100_001,
            ttl_seconds=120,
        )
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=state,
                otp_code="12345",
                ttl_seconds=120,
            )
        )
        sent_at = utc_now()
        due_at = sent_at + timedelta(seconds=40)
        self.assertTrue(
            await arm_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                recovery_at=due_at + timedelta(seconds=5),
            )
        )
        self.assertTrue(
            await schedule_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                telegram_sent_at=sent_at,
                fallback_at=due_at,
            )
        )

        self.assertTrue(
            await consume_otp_code(
                self.redis,
                mobile=mobile,
                expected_code="12345",
            )
        )
        self.assertEqual(
            await due_otp_request_ids(
                self.redis,
                now=due_at + timedelta(seconds=1),
                limit=10,
            ),
            [],
        )
        consumed = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(consumed.status, OTPRequestStatus.CONSUMED)
        self.assertIsNone(
            await claim_sms_delivery(
                self.redis,
                state=consumed,
                require_due=False,
                now=due_at + timedelta(seconds=1),
            )
        )

    async def test_new_structured_state_contains_no_raw_mobile_or_telegram_identity(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        telegram_id = 9_200_001
        state = build_otp_delivery_state(
            mobile=mobile,
            telegram_id=telegram_id,
            ttl_seconds=120,
        )
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=state,
                otp_code="12345",
                ttl_seconds=120,
            )
        )

        structured_keys = sorted(await self.redis.keys("otp_delivery:*") or [])
        self.assertTrue(structured_keys)
        serialized = "\n".join(structured_keys)
        for key in structured_keys:
            key_type = await self.redis.type(key)
            if key_type == "hash":
                serialized += "\n" + repr(await self.redis.hgetall(key))
            elif key_type == "string":
                serialized += "\n" + repr(await self.redis.get(key))
        self.assertNotIn(mobile, serialized)
        self.assertNotIn(str(telegram_id), serialized)
        self.assertNotIn("code_key", serialized)
        self.assertNotIn("mobile_number", serialized)
        self.assertNotIn("telegram_id", serialized)

    async def test_stale_missing_prefix_is_removed_without_starving_valid_due_work(self):
        due_at = utc_now()
        stale_ids = [uuid4() for _ in range(101)]
        await self.redis.zadd(
            OTP_FALLBACK_DUE_KEY,
            {str(request_id): due_at.timestamp() - 1 for request_id in stale_ids},
        )
        mobile = "0912" + str(uuid4().int)[-7:]
        valid = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=valid,
                otp_code="12345",
                ttl_seconds=120,
            )
        )
        self.assertTrue(
            await schedule_sms_fallback(
                self.redis,
                request_id=valid.otp_request_id,
                telegram_sent_at=due_at - timedelta(seconds=40),
                fallback_at=due_at,
            )
        )

        due_ids = await due_otp_request_ids(self.redis, now=due_at, limit=100)
        self.assertEqual(due_ids, [valid.otp_request_id])
        self.assertEqual(
            await self.redis.zcard(OTP_FALLBACK_DUE_KEY),
            1,
        )

    async def test_partial_pending_prefix_is_isolated_without_starving_valid_due_work(self):
        due_at = utc_now()
        poison_ids = [uuid4() for _ in range(101)]
        for request_id in poison_ids:
            await self.redis.hset(
                f"otp_delivery:request:{request_id}",
                mapping={"status": OTPRequestStatus.PENDING.value},
            )
        await self.redis.zadd(
            OTP_FALLBACK_DUE_KEY,
            {str(request_id): due_at.timestamp() - 1 for request_id in poison_ids},
        )
        mobile = "0912" + str(uuid4().int)[-7:]
        valid = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(await create_otp_delivery_state(
            self.redis,
            state=valid,
            otp_code="12345",
            ttl_seconds=120,
        ))
        self.assertTrue(await schedule_sms_fallback(
            self.redis,
            request_id=valid.otp_request_id,
            telegram_sent_at=due_at - timedelta(seconds=40),
            fallback_at=due_at,
        ))

        selection = await select_due_otp_requests(self.redis, now=due_at, limit=100)

        self.assertEqual(selection.request_ids, (valid.otp_request_id,))
        self.assertEqual(selection.isolated_counts, {"invalid_contract": 101})
        self.assertEqual(await self.redis.zcard(OTP_FALLBACK_DUE_KEY), 1)
        for request_id in poison_ids:
            self.assertEqual(
                await self.redis.hget(f"otp_delivery:request:{request_id}", "status"),
                OTPRequestStatus.EXPIRED.value,
            )

    async def test_more_than_scan_budget_makes_bounded_progress_across_cycles(self):
        due_at = utc_now()
        poison_ids = [uuid4() for _ in range(501)]
        pipe = self.redis.pipeline(transaction=False)
        for request_id in poison_ids:
            pipe.hset(
                f"otp_delivery:request:{request_id}",
                mapping={"status": OTPRequestStatus.PENDING.value},
            )
        await pipe.execute()
        await self.redis.zadd(
            OTP_FALLBACK_DUE_KEY,
            {str(request_id): due_at.timestamp() - 1 for request_id in poison_ids},
        )
        mobile = "0912" + str(uuid4().int)[-7:]
        valid = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(await create_otp_delivery_state(
            self.redis,
            state=valid,
            otp_code="12345",
            ttl_seconds=120,
        ))
        self.assertTrue(await schedule_sms_fallback(
            self.redis,
            request_id=valid.otp_request_id,
            telegram_sent_at=due_at - timedelta(seconds=40),
            fallback_at=due_at,
        ))

        first = await select_due_otp_requests(self.redis, now=due_at, limit=100)
        second = await select_due_otp_requests(self.redis, now=due_at, limit=100)

        self.assertEqual(first.request_ids, ())
        self.assertEqual(first.isolated_counts, {"invalid_contract": 500})
        self.assertEqual(second.request_ids, (valid.otp_request_id,))
        self.assertEqual(second.isolated_counts, {"invalid_contract": 1})
        self.assertEqual(await self.redis.zcard(OTP_FALLBACK_DUE_KEY), 1)

    async def test_concurrent_invalid_state_cleanup_is_idempotent(self):
        request_id = uuid4()
        await self.redis.hset(
            f"otp_delivery:request:{request_id}",
            mapping={"status": OTPRequestStatus.PENDING.value},
        )
        await self.redis.zadd(
            OTP_FALLBACK_DUE_KEY,
            {str(request_id): utc_now().timestamp() - 1},
        )

        statuses = await asyncio.gather(*(
            isolate_invalid_otp_fallback_state(
                self.redis,
                request_id=request_id,
                reason="invalid_contract",
            )
            for _ in range(4)
        ))

        self.assertIn("pending", statuses)
        self.assertEqual(await self.redis.zcard(OTP_FALLBACK_DUE_KEY), 0)
        self.assertEqual(
            await self.redis.hget(f"otp_delivery:request:{request_id}", "status"),
            OTPRequestStatus.EXPIRED.value,
        )
        self.assertEqual(
            await self.redis.hget(
                f"otp_delivery:request:{request_id}",
                "terminal_reason",
            ),
            "invalid_contract",
        )

    async def test_malformed_and_unverifiable_states_are_isolated_as_bounded_reasons(self):
        due_at = utc_now()
        states = []
        for _ in range(4):
            mobile = "0912" + str(uuid4().int)[-7:]
            item = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
            self.assertTrue(await create_otp_delivery_state(
                self.redis,
                state=item,
                otp_code="12345",
                ttl_seconds=120,
            ))
            self.assertTrue(await schedule_sms_fallback(
                self.redis,
                request_id=item.otp_request_id,
                telegram_sent_at=due_at - timedelta(seconds=40),
                fallback_at=due_at,
            ))
            states.append(item)
        await self.redis.hset(
            f"otp_delivery:request:{states[0].otp_request_id}",
            "created_at",
            "not-a-timestamp",
        )
        await self.redis.hset(
            f"otp_delivery:request:{states[1].otp_request_id}",
            "delivery_target_ciphertext",
            "not-a-fernet-token",
        )
        await self.redis.hset(
            f"otp_delivery:request:{states[2].otp_request_id}",
            "identity_digest",
            "0" * 64,
        )

        selection = await select_due_otp_requests(self.redis, now=due_at, limit=100)

        self.assertEqual(selection.request_ids, (states[3].otp_request_id,))
        self.assertEqual(selection.isolated_counts, {
            "invalid_contract": 2,
            "invalid_delivery_target": 1,
        })
        for index, item in enumerate(states[:3]):
            self.assertEqual(
                await self.redis.hget(
                    f"otp_delivery:request:{item.otp_request_id}",
                    "terminal_reason",
                ),
                (
                    "invalid_delivery_target"
                    if index == 2
                    else "invalid_contract"
                ),
            )

    async def test_secret_rotation_isolates_old_due_state_and_allows_new_work(self):
        due_at = utc_now()
        old_mobile = "0912" + str(uuid4().int)[-7:]
        old_state = build_otp_delivery_state(mobile=old_mobile, ttl_seconds=120)
        self.assertTrue(await create_otp_delivery_state(
            self.redis,
            state=old_state,
            otp_code="12345",
            ttl_seconds=120,
        ))
        self.assertTrue(await schedule_sms_fallback(
            self.redis,
            request_id=old_state.otp_request_id,
            telegram_sent_at=due_at - timedelta(seconds=40),
            fallback_at=due_at,
        ))

        settings.otp_delivery_state_secret = "rotated-stage6-secret-0123456789abcdef"
        new_mobile = "0912" + str(uuid4().int)[-7:]
        new_state = build_otp_delivery_state(mobile=new_mobile, ttl_seconds=120)
        self.assertTrue(await create_otp_delivery_state(
            self.redis,
            state=new_state,
            otp_code="54321",
            ttl_seconds=120,
        ))
        self.assertTrue(await schedule_sms_fallback(
            self.redis,
            request_id=new_state.otp_request_id,
            telegram_sent_at=due_at - timedelta(seconds=40),
            fallback_at=due_at,
        ))

        selection = await select_due_otp_requests(self.redis, now=due_at, limit=100)

        self.assertEqual(selection.request_ids, (new_state.otp_request_id,))
        self.assertEqual(selection.isolated_counts, {"invalid_delivery_target": 1})
        self.assertEqual(
            await self.redis.zscore(OTP_FALLBACK_DUE_KEY, str(old_state.otp_request_id)),
            None,
        )

    async def test_pre_provider_claim_reclaims_but_started_provider_becomes_ambiguous(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        state = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=state,
                otp_code="12345",
                ttl_seconds=120,
            )
        )
        due_at = utc_now()
        self.assertTrue(
            await schedule_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                telegram_sent_at=due_at - timedelta(seconds=40),
                fallback_at=due_at,
            )
        )

        first = await claim_sms_delivery(
            self.redis,
            state=state,
            require_due=True,
            now=due_at,
            lease_seconds=5,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(
            await claim_sms_delivery(
                self.redis,
                state=state,
                require_due=True,
                now=due_at + timedelta(seconds=4),
                lease_seconds=5,
            )
        )

        second = await claim_sms_delivery(
            self.redis,
            state=state,
            require_due=True,
            now=due_at + timedelta(seconds=6),
            lease_seconds=5,
        )
        self.assertIsNotNone(second)
        self.assertNotEqual(first.claim_id, second.claim_id)
        self.assertFalse(
            await record_sms_delivery_result(
                self.redis,
                claim=first,
                outcome=OTPDeliveryStatus.ACCEPTED,
            )
        )
        self.assertTrue(
            await mark_sms_provider_attempt_started(self.redis, claim=second)
        )

        self.assertIsNone(
            await claim_sms_delivery(
                self.redis,
                state=state,
                require_due=True,
                now=due_at + timedelta(seconds=12),
                lease_seconds=5,
            )
        )
        recovered = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(recovered.sms_delivery_status, OTPDeliveryStatus.AMBIGUOUS)
        self.assertEqual(await self.redis.zcard(OTP_FALLBACK_DUE_KEY), 0)

    async def test_scheduled_sms_rejection_preserves_telegram_delivered_code(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        state = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=state,
                otp_code="12345",
                ttl_seconds=120,
            )
        )
        due_at = utc_now()
        self.assertTrue(
            await schedule_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                telegram_sent_at=due_at - timedelta(seconds=40),
                fallback_at=due_at,
            )
        )
        claim = await claim_sms_delivery(
            self.redis,
            state=state,
            require_due=True,
            now=due_at,
        )
        self.assertTrue(await mark_sms_provider_attempt_started(self.redis, claim=claim))
        self.assertTrue(
            await record_sms_delivery_result(
                self.redis,
                claim=claim,
                outcome=OTPDeliveryStatus.FAILED,
            )
        )

        self.assertEqual(await self.redis.get(f"otp:{mobile}"), "12345")
        failed = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(failed.status, OTPRequestStatus.PENDING)
        self.assertEqual(failed.sms_delivery_status, OTPDeliveryStatus.FAILED)

    async def test_nearly_expired_code_is_not_claimed_for_provider_delivery(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        state = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(
            await create_otp_delivery_state(
                self.redis,
                state=state,
                otp_code="12345",
                ttl_seconds=120,
            )
        )
        due_at = utc_now()
        self.assertTrue(
            await schedule_sms_fallback(
                self.redis,
                request_id=state.otp_request_id,
                telegram_sent_at=due_at - timedelta(seconds=110),
                fallback_at=due_at,
            )
        )
        await self.redis.expire(f"otp:{mobile}", 5)

        self.assertIsNone(
            await claim_sms_delivery(
                self.redis,
                state=state,
                require_due=True,
                now=due_at,
            )
        )
        loaded = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(loaded.status, OTPRequestStatus.PENDING)
        self.assertEqual(loaded.sms_delivery_status, OTPDeliveryStatus.CANCELLED)
        self.assertEqual(await self.redis.zcard(OTP_FALLBACK_DUE_KEY), 0)

    async def test_provider_started_near_expiry_recovers_as_ambiguous_not_cancelled(self):
        mobile = "0912" + str(uuid4().int)[-7:]
        state = build_otp_delivery_state(mobile=mobile, ttl_seconds=120)
        self.assertTrue(await create_otp_delivery_state(
            self.redis,
            state=state,
            otp_code="12345",
            ttl_seconds=120,
        ))
        due_at = utc_now()
        self.assertTrue(await schedule_sms_fallback(
            self.redis,
            request_id=state.otp_request_id,
            telegram_sent_at=due_at - timedelta(seconds=40),
            fallback_at=due_at,
        ))
        claim = await claim_sms_delivery(
            self.redis,
            state=state,
            require_due=True,
            now=due_at,
            lease_seconds=5,
        )
        self.assertTrue(await mark_sms_provider_attempt_started(self.redis, claim=claim))
        await self.redis.expire(f"otp:{mobile}", 5)

        self.assertIsNone(await claim_sms_delivery(
            self.redis,
            state=state,
            require_due=True,
            now=due_at + timedelta(seconds=6),
            lease_seconds=5,
        ))

        recovered = await load_otp_delivery_state(
            self.redis,
            request_id=state.otp_request_id,
        )
        self.assertEqual(recovered.status, OTPRequestStatus.PENDING)
        self.assertEqual(recovered.sms_delivery_status, OTPDeliveryStatus.AMBIGUOUS)
        self.assertEqual(await self.redis.get(f"otp:{mobile}"), "12345")
        self.assertEqual(await self.redis.zcard(OTP_FALLBACK_DUE_KEY), 0)
