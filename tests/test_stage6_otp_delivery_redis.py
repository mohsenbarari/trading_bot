import asyncio
import os
import unittest
from datetime import timedelta
from uuid import uuid4

import redis.asyncio as redis_async

from core.registration_contracts import OTPDeliveryStatus, OTPRequestStatus
from core.services.otp_delivery_state_service import (
    arm_sms_fallback,
    build_otp_delivery_state,
    claim_sms_delivery,
    consume_otp_code,
    create_otp_delivery_state,
    due_otp_request_ids,
    load_otp_delivery_state,
    record_sms_delivery_result,
    schedule_sms_fallback,
)
from core.utils import utc_now


REDIS_URL = os.getenv("STAGE6_TEST_REDIS_URL", "").strip()


@unittest.skipUnless(REDIS_URL, "set STAGE6_TEST_REDIS_URL to run real Redis Stage 6 tests")
class Stage6OTPDeliveryRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_async.from_url(REDIS_URL, decode_responses=True)
        await self.redis.flushdb()

    async def asyncTearDown(self):
        await self.redis.flushdb()
        await self.redis.aclose()

    async def test_one_code_schedule_claim_record_and_consume_lifecycle(self):
        state = build_otp_delivery_state(
            mobile="0912" + str(uuid4().int)[-7:],
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
        loaded = await load_otp_delivery_state(self.redis, mobile=state.mobile_number)
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
            await record_sms_delivery_result(
                self.redis,
                request_id=state.otp_request_id,
                outcome=OTPDeliveryStatus.ACCEPTED,
            )
        )
        self.assertTrue(
            await consume_otp_code(
                self.redis,
                mobile=state.mobile_number,
                expected_code="12345",
            )
        )
        self.assertFalse(
            await consume_otp_code(
                self.redis,
                mobile=state.mobile_number,
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
        state = build_otp_delivery_state(
            mobile="0912" + str(uuid4().int)[-7:],
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
                mobile=state.mobile_number,
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
