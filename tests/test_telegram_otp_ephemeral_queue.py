import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.registration_contracts import TelegramOTPDeliveryCommand, TelegramOTPDeliveryOutcome
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.telegram_otp_ephemeral_queue import (
    OTP_EQ_POISON_STREAM,
    OTP_EQ_STREAM,
    command_hash,
    decrypt_otp_command,
    encrypt_otp_command,
    enqueue_telegram_otp_and_wait,
    enqueue_telegram_otp_command,
    inspect_telegram_otp_ephemeral_health,
    process_telegram_otp_stream_message,
    receipt_key,
)
from core.sms import sms_otp_runtime_complete, validate_iran_sms_fallback_runtime
from core.utils import utc_now
from datetime import timedelta
from uuid import uuid4


TEST_QUEUE_SECRET = "stage6-test-state-secret-0123456789abcdef"


def command(**overrides):
    values = {
        "otp_request_id": uuid4(),
        "telegram_id": 8_700_001,
        "otp_code": "12345",
        "expires_at": utc_now() + timedelta(seconds=120),
    }
    values.update(overrides)
    return TelegramOTPDeliveryCommand(**values)


class FakeStreamRedis:
    def __init__(self):
        self.values = {}
        self.streams = {OTP_EQ_STREAM: [], OTP_EQ_POISON_STREAM: []}
        self.groups = set()
        self.acked = set()
        self._seq = 0

    async def set(self, key, value, *, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def xadd(self, name, fields):
        self._seq += 1
        message_id = f"1-{self._seq}"
        self.streams.setdefault(name, []).append((message_id, dict(fields)))
        return message_id

    async def xgroup_create(self, name, group, id="0", mkstream=True):
        del id
        if mkstream:
            self.streams.setdefault(name, [])
        if group in self.groups:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        self.groups.add(group)

    async def xack(self, name, group, message_id):
        del name, group
        self.acked.add(str(message_id))
        return 1

    async def xlen(self, name):
        return len(self.streams.get(name, []))

    async def xrange(self, name, min="-", max="+", count=1):
        del min, max
        return list(self.streams.get(name, [])[:count])

    async def xreadgroup(self, group, consumer, streams, count=1, block=None):
        del group, consumer, block
        name = next(iter(streams))
        unread = [
            item
            for item in self.streams.get(name, [])
            if item[0] not in self.acked
        ]
        return [[name, unread[:count]]] if unread else []

    async def xautoclaim(self, name, group, consumer, min_idle_time, start, count=1):
        del group, consumer, min_idle_time, start
        pending = [
            item
            for item in self.streams.get(name, [])
            if item[0] not in self.acked
        ]
        return ["0-0", pending[:count], []]


class TelegramOTPEphemeralQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret_patcher = patch(
            "core.services.telegram_otp_ephemeral_queue.settings"
        )
        self.settings = self.secret_patcher.start()
        self.settings.telegram_otp_queue_secret = TEST_QUEUE_SECRET
        self.settings.telegram_delivery_queue_bot_min_interval_seconds = 0.035
        self.settings.telegram_delivery_queue_destination_min_interval_seconds = 1.05
        self.settings.telegram_delivery_queue_rate_limit_probe_delay_seconds = 0.1
        self.settings.telegram_delivery_queue_global_rate_limit_window_seconds = 2.0
        self.settings.telegram_delivery_queue_limiter_key_ttl_seconds = 86400

    def tearDown(self):
        self.secret_patcher.stop()

    def test_encryption_roundtrip_keeps_otp_out_of_ciphertext_inspection(self):
        cmd = command()
        ciphertext = encrypt_otp_command(cmd)
        self.assertNotIn("12345", ciphertext)
        restored = decrypt_otp_command(ciphertext)
        self.assertEqual(restored.otp_code, cmd.otp_code)
        self.assertEqual(restored.otp_request_id, cmd.otp_request_id)

    async def test_enqueue_consume_ack_and_no_plaintext_otp_in_redis(self):
        redis = FakeStreamRedis()
        cmd = command()
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_ephemeral_queue.execute_telegram_otp_via_gateway",
            new=AsyncMock(return_value=TelegramOTPDeliveryOutcome.SENT),
        ), patch(
            "core.services.telegram_otp_ephemeral_queue._admit_central_bot",
            new=AsyncMock(return_value=None),
        ):
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=redis.streams[OTP_EQ_STREAM][0][1],
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.SENT)
        self.assertIn(message_id, redis.acked)
        dumped = str(redis.values) + str(redis.streams)
        self.assertNotIn("12345", dumped)
        self.assertNotIn(str(cmd.telegram_id), receipt_key(cmd.otp_request_id))

    async def test_duplicate_same_command_does_not_resend(self):
        redis = FakeStreamRedis()
        cmd = command()
        send = AsyncMock(return_value=TelegramOTPDeliveryOutcome.SENT)
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_ephemeral_queue.execute_telegram_otp_via_gateway",
            new=send,
        ), patch(
            "core.services.telegram_otp_ephemeral_queue._admit_central_bot",
            new=AsyncMock(return_value=None),
        ):
            first_id = await enqueue_telegram_otp_command(redis, command=cmd)
            first = await process_telegram_otp_stream_message(
                redis,
                message_id=first_id,
                fields=redis.streams[OTP_EQ_STREAM][0][1],
            )
            second_id = await enqueue_telegram_otp_command(redis, command=cmd)
            second = await process_telegram_otp_stream_message(
                redis,
                message_id=second_id,
                fields=redis.streams[OTP_EQ_STREAM][1][1],
            )
        self.assertEqual(first, TelegramOTPDeliveryOutcome.SENT)
        self.assertEqual(second, TelegramOTPDeliveryOutcome.SENT)
        send.assert_awaited_once()

    async def test_changed_replay_is_invalid(self):
        redis = FakeStreamRedis()
        original = command()
        changed = original.model_copy(update={"otp_code": "54321"})
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_ephemeral_queue.execute_telegram_otp_via_gateway",
            new=AsyncMock(return_value=TelegramOTPDeliveryOutcome.SENT),
        ), patch(
            "core.services.telegram_otp_ephemeral_queue._admit_central_bot",
            new=AsyncMock(return_value=None),
        ):
            first_id = await enqueue_telegram_otp_command(redis, command=original)
            await process_telegram_otp_stream_message(
                redis,
                message_id=first_id,
                fields=redis.streams[OTP_EQ_STREAM][0][1],
            )
            result = await enqueue_telegram_otp_and_wait(redis, command=changed)
        self.assertEqual(result.outcome, TelegramOTPDeliveryOutcome.INVALID)

    async def test_expired_command_is_invalid_before_enqueue(self):
        redis = FakeStreamRedis()
        with override_current_server(SERVER_FOREIGN):
            result = await enqueue_telegram_otp_and_wait(
                redis,
                command=command(expires_at=utc_now() - timedelta(seconds=1)),
            )
        self.assertEqual(result.outcome, TelegramOTPDeliveryOutcome.INVALID)
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_poison_command_is_quarantined_without_payload(self):
        redis = FakeStreamRedis()
        await process_telegram_otp_stream_message(
            redis,
            message_id="1-9",
            fields={"payload": "not-valid-ciphertext", "request_id": str(uuid4()), "command_hash": "abc"},
        )
        self.assertEqual(len(redis.streams[OTP_EQ_POISON_STREAM]), 1)
        poison = str(redis.streams[OTP_EQ_POISON_STREAM])
        self.assertNotIn("12345", poison)
        self.assertIn("1-9", redis.acked)

    async def test_reclaim_after_restart_processes_unacked_command(self):
        redis = FakeStreamRedis()
        cmd = command()
        with override_current_server(SERVER_FOREIGN), patch(
            "core.services.telegram_otp_ephemeral_queue.execute_telegram_otp_via_gateway",
            new=AsyncMock(return_value=TelegramOTPDeliveryOutcome.SENT),
        ), patch(
            "core.services.telegram_otp_ephemeral_queue._admit_central_bot",
            new=AsyncMock(return_value=None),
        ):
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=redis.streams[OTP_EQ_STREAM][0][1],
                deliveries=2,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.SENT)

    async def test_iran_cannot_enqueue(self):
        with override_current_server(SERVER_IRAN):
            with self.assertRaisesRegex(RuntimeError, "requires_foreign"):
                await enqueue_telegram_otp_and_wait(FakeStreamRedis(), command=command())

    async def test_health_has_no_identity_labels(self):
        redis = FakeStreamRedis()
        health = await inspect_telegram_otp_ephemeral_health(redis)
        payload = str(health)
        self.assertNotIn("12345", payload)
        self.assertFalse(health.worker_present)
        self.assertEqual(health.pending_count, 0)

    def test_command_hash_changes_when_otp_changes(self):
        original = command()
        changed = original.model_copy(update={"otp_code": "54321"})
        self.assertNotEqual(command_hash(original), command_hash(changed))


class SMSFallbackConfigTests(unittest.TestCase):
    def test_incomplete_iran_fallback_fails_closed(self):
        self.assertFalse(sms_otp_runtime_complete(SimpleNamespace(smsir_api_key=None)))
        with self.assertRaisesRegex(RuntimeError, "sms_fallback_config_incomplete"):
            validate_iran_sms_fallback_runtime(
                SimpleNamespace(
                    otp_sms_auto_fallback_enabled=True,
                    server_mode=SERVER_IRAN,
                    trading_bot_service="api",
                    smsir_api_key=None,
                    smsir_line_number=None,
                    smsir_otp_template_id="1",
                    smsir_otp_template_parameter="CODE",
                )
            )

    def test_complete_iran_config_is_accepted(self):
        configured = SimpleNamespace(
            otp_sms_auto_fallback_enabled=True,
            server_mode=SERVER_IRAN,
            trading_bot_service="api",
            smsir_api_key="present",
            smsir_line_number=3000,
            smsir_otp_template_id="585147",
            smsir_otp_template_parameter="CODE",
        )
        self.assertTrue(sms_otp_runtime_complete(configured))
        validate_iran_sms_fallback_runtime(configured)


if __name__ == "__main__":
    unittest.main()
