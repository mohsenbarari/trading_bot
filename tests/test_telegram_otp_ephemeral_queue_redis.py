import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import redis.asyncio as redis_async

from core.registration_contracts import TelegramOTPDeliveryCommand, TelegramOTPDeliveryOutcome
from core.server_routing import SERVER_FOREIGN, override_current_server
from core.services.telegram_otp_ephemeral_queue import (
    OTP_EQ_GROUP,
    OTP_EQ_POISON_STREAM,
    OTP_EQ_RECEIPT_PREFIX,
    OTP_EQ_STREAM,
    OTP_EQ_WORKER_KEY,
    command_hash,
    enqueue_telegram_otp_command,
    inspect_telegram_otp_ephemeral_health,
    process_telegram_otp_stream_message,
    run_telegram_otp_ephemeral_once,
)
from core.utils import utc_now


def _redis_url() -> str:
    for key in (
        "TELEGRAM_OTP_EQ_TEST_REDIS_URL",
        "STAGE6_TEST_REDIS_URL",
        "TELEGRAM_QUEUE_STAGE3_TEST_REDIS_URL",
    ):
        value = str(os.getenv(key, "")).strip()
        if value:
            return value
    return str(os.getenv("TELEGRAM_OTP_EQ_LOCAL_REDIS_URL", "")).strip()


REDIS_URL = _redis_url()
TEST_QUEUE_SECRET = "stage6-real-redis-secret-0123456789abcdef"


def _command(**overrides):
    values = {
        "otp_request_id": uuid4(),
        "telegram_id": 8_700_002,
        "otp_code": "12345",
        "expires_at": utc_now() + timedelta(seconds=120),
    }
    values.update(overrides)
    return TelegramOTPDeliveryCommand(**values)


@unittest.skipUnless(REDIS_URL, "set TELEGRAM_OTP_EQ_TEST_REDIS_URL for real Redis OTP queue tests")
class TelegramOTPEphemeralQueueRedisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_async.from_url(REDIS_URL, decode_responses=True)
        await self._cleanup()
        self.secret_patcher = patch(
            "core.services.telegram_otp_ephemeral_queue.settings.telegram_otp_queue_secret",
            TEST_QUEUE_SECRET,
        )
        self.secret_patcher.start()

    async def asyncTearDown(self):
        self.secret_patcher.stop()
        await self._cleanup()
        close = getattr(self.redis, "aclose", None) or self.redis.close
        result = close()
        if hasattr(result, "__await__"):
            await result

    async def _cleanup(self):
        await self.redis.delete(OTP_EQ_STREAM, OTP_EQ_POISON_STREAM, OTP_EQ_WORKER_KEY)
        async for key in self.redis.scan_iter(match=f"{OTP_EQ_RECEIPT_PREFIX}*"):
            await self.redis.delete(key)

    async def test_real_redis_ack_delete_health_and_reclaim_without_resend(self):
        cmd = _command()
        send = AsyncMock(return_value=TelegramOTPDeliveryOutcome.SENT)
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_ephemeral_queue.execute_telegram_otp_via_gateway",
            new=send,
        ), patch(
            "core.services.telegram_otp_ephemeral_queue._admit_central_bot",
            new=AsyncMock(return_value=None),
        ):
            await enqueue_telegram_otp_command(self.redis, command=cmd)
            before = await inspect_telegram_otp_ephemeral_health(self.redis)
            self.assertIsNone(before.error)
            self.assertEqual(before.pending_count, 1)
            self.assertIsNotNone(before.oldest_command_age_seconds)
            self.assertGreaterEqual(int(await self.redis.xlen(OTP_EQ_STREAM) or 0), 1)

            processed = await run_telegram_otp_ephemeral_once(self.redis, consumer="bot")
            self.assertEqual(processed, 1)
            after = await inspect_telegram_otp_ephemeral_health(self.redis)
            self.assertIsNone(after.error)
            self.assertEqual(after.pending_count, 0)
            self.assertIsNone(after.oldest_command_age_seconds)
            self.assertEqual(int(await self.redis.xlen(OTP_EQ_STREAM) or 0), 0)
            pending = await self.redis.xpending(OTP_EQ_STREAM, OTP_EQ_GROUP)
            pending_count = pending[0] if isinstance(pending, (list, tuple)) else pending.get("pending")
            self.assertEqual(int(pending_count or 0), 0)
            self.assertEqual(int(await self.redis.xlen(OTP_EQ_POISON_STREAM) or 0), 0)
            send.assert_awaited_once()

            replay = _command(otp_request_id=cmd.otp_request_id, otp_code=cmd.otp_code)
            await enqueue_telegram_otp_command(self.redis, command=replay)
            second = await run_telegram_otp_ephemeral_once(self.redis, consumer="bot")
            self.assertEqual(second, 1)
            send.assert_awaited_once()
            self.assertEqual(int(await self.redis.xlen(OTP_EQ_STREAM) or 0), 0)

            crash = _command()
            message_id = await enqueue_telegram_otp_command(self.redis, command=crash)
            raw = await self.redis.xrange(OTP_EQ_STREAM, min="-", max="+", count=1)
            fields = raw[0][1]
            await self.redis.set(
                f"{OTP_EQ_RECEIPT_PREFIX}{crash.otp_request_id}",
                f"{TelegramOTPDeliveryOutcome.SENT.value}:{command_hash(crash)}",
                ex=60,
            )
            await self.redis.xreadgroup(
                OTP_EQ_GROUP,
                "crashed",
                streams={OTP_EQ_STREAM: ">"},
                count=1,
            )
            recovered = await process_telegram_otp_stream_message(
                self.redis,
                message_id=str(message_id),
                fields=fields,
                deliveries=2,
            )
            self.assertEqual(recovered, TelegramOTPDeliveryOutcome.SENT)
            send.assert_awaited_once()
            self.assertEqual(int(await self.redis.xlen(OTP_EQ_STREAM) or 0), 0)
            dumped = str(await self.redis.dump(OTP_EQ_STREAM) or b"")
            self.assertNotIn("12345", dumped)
            self.assertNotIn("12345", str(await self.redis.keys(f"{OTP_EQ_RECEIPT_PREFIX}*")))
