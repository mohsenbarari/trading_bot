import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from core.registration_contracts import TelegramOTPDeliveryCommand, TelegramOTPDeliveryOutcome
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.telegram_otp_ephemeral_queue import (
    OTP_EQ_GROUP,
    OTP_EQ_MAX_DELIVERIES,
    OTP_EQ_POISON_MAXLEN,
    OTP_EQ_POISON_STREAM,
    OTP_EQ_STREAM,
    OTP_EQ_WORKER_KEY,
    command_hash,
    decrypt_otp_command,
    encrypt_otp_command,
    enqueue_telegram_otp_and_wait,
    enqueue_telegram_otp_command,
    finalize_telegram_otp_command,
    inspect_telegram_otp_ephemeral_health,
    process_telegram_otp_stream_message,
    receipt_key,
    run_telegram_otp_ephemeral_once,
)
from core.sms import (
    sms_otp_runtime_complete,
    validate_iran_sms_fallback_runtime,
    validate_non_iran_sms_isolation,
)
from core.utils import utc_now


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


class _FakePipeline:
    def __init__(self, redis, *, fail=False):
        self._redis = redis
        self._fail = fail
        self._ops = []

    def xack(self, *args, **kwargs):
        self._ops.append(("xack", args, kwargs))
        return self

    def xdel(self, *args, **kwargs):
        self._ops.append(("xdel", args, kwargs))
        return self

    async def execute(self):
        if self._fail:
            raise RuntimeError("pipeline_failed")
        results = []
        for name, args, kwargs in self._ops:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        return results

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeStreamRedis:
    def __init__(self):
        self.values = {}
        self.streams = {OTP_EQ_STREAM: [], OTP_EQ_POISON_STREAM: []}
        self.groups = {}
        self.pel = {}
        self.acked = set()
        self.deleted = set()
        self.expires = {}
        self.fail_pipeline = False
        self._seq = 0
        self._now_ms = int(time.time() * 1000)

    def pipeline(self, transaction=True):
        del transaction
        return _FakePipeline(self, fail=self.fail_pipeline)

    async def set(self, key, value, *, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, name, ttl):
        self.expires[name] = ttl
        return True

    async def eval(self, script, numkeys, *values):
        del script
        if int(numkeys) != 2:
            raise AssertionError("unexpected_eval_key_count")
        marker_key, stream_name, ttl, request_id, reason, quarantined_at = values
        if marker_key in self.values:
            return 0
        await self.xadd(
            stream_name,
            {
                "request_id": request_id,
                "reason": reason,
                "quarantined_at": quarantined_at,
            },
        )
        await self.set(marker_key, "1", ex=int(ttl))
        return 1

    async def xadd(self, name, fields):
        self._now_ms = max(self._now_ms, int(time.time() * 1000))
        self._seq += 1
        message_id = f"{self._now_ms}-{self._seq}"
        self.streams.setdefault(name, []).append((message_id, dict(fields)))
        return message_id

    async def xgroup_create(self, name, group, id="0", mkstream=True):
        del id
        if mkstream:
            self.streams.setdefault(name, [])
        groups = self.groups.setdefault(name, {})
        if group in groups:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        groups[group] = {"last_delivered_id": "0-0", "entries_read": 0}

    async def xack(self, name, group, message_id):
        del name, group
        self.acked.add(str(message_id))
        self.pel.pop(str(message_id), None)
        return 1

    async def xdel(self, name, *message_ids):
        wanted = {str(item) for item in message_ids}
        self.deleted.update(wanted)
        self.streams[name] = [
            item for item in self.streams.get(name, []) if item[0] not in wanted
        ]
        return len(wanted)

    async def xlen(self, name):
        return len(self.streams.get(name, []))

    def _id_ms(self, message_id):
        head, _, _ = str(message_id).partition("-")
        return int(head) if head.isdigit() else 0

    def _id_parts(self, message_id):
        head, separator, tail = str(message_id).lstrip("(").partition("-")
        if not separator or not head.isdigit() or not tail.isdigit():
            return (0, 0)
        return (int(head), int(tail))

    def _in_range(self, message_id, minimum, maximum):
        value = self._id_parts(message_id)
        if minimum not in {"-", ""}:
            exclusive = str(minimum).startswith("(")
            bound = str(minimum)[1:] if exclusive else str(minimum)
            bound_value = self._id_parts(bound)
            if exclusive and value <= bound_value:
                return False
            if not exclusive and value < bound_value:
                return False
        if maximum not in {"+", ""}:
            exclusive = str(maximum).startswith("(")
            bound = str(maximum)[1:] if exclusive else str(maximum)
            bound_value = self._id_parts(bound)
            if exclusive and value >= bound_value:
                return False
            if not exclusive and value > bound_value:
                return False
        return True

    async def xrange(self, name, min="-", max="+", count=None):
        items = [
            item
            for item in self.streams.get(name, [])
            if self._in_range(item[0], min, max)
        ]
        if count is not None:
            return items[:count]
        return items

    async def xtrim(self, name, maxlen=None, approximate=True, minid=None, limit=None):
        del approximate, limit
        items = list(self.streams.get(name, []))
        if minid is not None:
            # never drop pending
            pending = set(self.pel)
            threshold = self._id_parts(str(minid))
            kept = []
            for item in self.streams.get(name, []):
                if item[0] in pending or self._id_parts(item[0]) >= threshold:
                    kept.append(item)
            items = kept
        elif maxlen is not None:
            items = items[-int(maxlen) :]
        self.streams[name] = items
        return 0

    async def xreadgroup(self, group, consumer, streams, count=1, block=None):
        del block
        name = next(iter(streams))
        unread = []
        for message_id, fields in self.streams.get(name, []):
            if message_id in self.pel or message_id in self.acked:
                continue
            unread.append((message_id, fields))
            if len(unread) >= count:
                break
        for message_id, _fields in unread:
            self.pel[message_id] = {
                "consumer": consumer,
                "deliveries": 1,
                "last_deliver_ms": 0,
            }
            self.groups.setdefault(name, {}).setdefault(
                group, {"last_delivered_id": "0-0", "entries_read": 0}
            )
            self.groups[name][group]["last_delivered_id"] = message_id
            self.groups[name][group]["entries_read"] += 1
        return [[name, unread]] if unread else []

    async def xautoclaim(self, name, group, consumer, min_idle_time, start, count=1):
        del group, start
        claimed = []
        for message_id, fields in self.streams.get(name, []):
            pending = self.pel.get(message_id)
            if pending is None:
                continue
            idle = self._now_ms - int(pending.get("last_deliver_ms") or 0)
            if idle < int(min_idle_time):
                continue
            pending["consumer"] = consumer
            pending["deliveries"] = int(pending.get("deliveries") or 0) + 1
            pending["last_deliver_ms"] = self._now_ms
            claimed.append((message_id, fields))
            if len(claimed) >= count:
                break
        return ["0-0", claimed, []]

    async def xpending(self, name, group):
        del group
        ids = [item[0] for item in self.streams.get(name, []) if item[0] in self.pel]
        if not ids:
            return [0, None, None, []]
        return [len(ids), ids[0], ids[-1], [["bot", str(len(ids))]]]

    async def xpending_range(self, name, group, min="-", max="+", count=16):
        del group
        rows = []
        for message_id, _fields in self.streams.get(name, []):
            pending = self.pel.get(message_id)
            if pending is None or not self._in_range(message_id, min, max):
                continue
            rows.append(
                {
                    "message_id": message_id,
                    "consumer": pending["consumer"],
                    "time_since_delivered": 1,
                    "times_delivered": int(pending["deliveries"]),
                }
            )
            if len(rows) >= count:
                break
        return rows

    async def xinfo_groups(self, name):
        info = []
        for group, meta in self.groups.get(name, {}).items():
            pending = sum(1 for item in self.streams.get(name, []) if item[0] in self.pel)
            delivered = set(self.pel) | self.acked
            lag = sum(1 for item in self.streams.get(name, []) if item[0] not in delivered)
            info.append(
                {
                    "name": group,
                    "consumers": 1,
                    "pending": pending,
                    "last-delivered-id": meta.get("last_delivered_id") or "0-0",
                    "entries-read": meta.get("entries_read") or 0,
                    "lag": lag,
                }
            )
        return info


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

    def _send_patches(self, send=None):
        sender = send or AsyncMock(return_value=TelegramOTPDeliveryOutcome.SENT)
        return sender, (
            override_current_server(SERVER_FOREIGN),
            patch(
                "core.services.telegram_otp_ephemeral_queue.execute_telegram_otp_via_gateway",
                new=sender,
            ),
            patch(
                "core.services.telegram_otp_ephemeral_queue._admit_central_bot",
                new=AsyncMock(return_value=None),
            ),
        )

    def test_encryption_roundtrip_keeps_otp_out_of_ciphertext_inspection(self):
        cmd = command()
        ciphertext = encrypt_otp_command(cmd)
        self.assertNotIn("12345", ciphertext)
        restored = decrypt_otp_command(ciphertext)
        self.assertEqual(restored.otp_code, cmd.otp_code)
        self.assertEqual(restored.otp_request_id, cmd.otp_request_id)

    async def test_enqueue_consume_ack_delete_and_no_plaintext_otp_in_redis(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=dict(redis.streams[OTP_EQ_STREAM][0][1]),
                deliveries=1,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.SENT)
        self.assertIn(message_id, redis.acked)
        self.assertIn(message_id, redis.deleted)
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])
        dumped = str(redis.values) + str(redis.streams)
        self.assertNotIn("12345", dumped)
        self.assertNotIn(str(cmd.telegram_id), receipt_key(cmd.otp_request_id))

    async def test_duplicate_same_command_does_not_resend(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            first_id = await enqueue_telegram_otp_command(redis, command=cmd)
            first = await process_telegram_otp_stream_message(
                redis,
                message_id=first_id,
                fields=dict(redis.streams[OTP_EQ_STREAM][0][1]),
                deliveries=1,
            )
            second_id = await enqueue_telegram_otp_command(redis, command=cmd)
            second = await process_telegram_otp_stream_message(
                redis,
                message_id=second_id,
                fields=dict(redis.streams[OTP_EQ_STREAM][0][1]),
                deliveries=1,
            )
        self.assertEqual(first, TelegramOTPDeliveryOutcome.SENT)
        self.assertEqual(second, TelegramOTPDeliveryOutcome.SENT)
        send.assert_awaited_once()
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_changed_replay_is_invalid(self):
        redis = FakeStreamRedis()
        original = command()
        changed = original.model_copy(update={"otp_code": "54321"})
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            first_id = await enqueue_telegram_otp_command(redis, command=original)
            await process_telegram_otp_stream_message(
                redis,
                message_id=first_id,
                fields=dict(redis.streams[OTP_EQ_STREAM][0][1]),
                deliveries=1,
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
            message_id="1700000000000-9",
            fields={"payload": "not-valid-ciphertext", "request_id": str(uuid4()), "command_hash": "abc"},
            deliveries=1,
        )
        self.assertEqual(len(redis.streams[OTP_EQ_POISON_STREAM]), 1)
        poison = str(redis.streams[OTP_EQ_POISON_STREAM])
        self.assertNotIn("12345", poison)
        self.assertIn("1700000000000-9", redis.acked)
        self.assertIn("1700000000000-9", redis.deleted)
        self.assertEqual(redis.expires.get(OTP_EQ_POISON_STREAM), 86_400)

    async def test_terminal_paths_finalize_without_leaving_stream_body(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches(
            AsyncMock(return_value=TelegramOTPDeliveryOutcome.RATE_LIMITED)
        )
        with patches[0], patches[1], patches[2]:
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            fields = dict(redis.streams[OTP_EQ_STREAM][0][1])
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=1,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.RATE_LIMITED)
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_finalize_is_idempotent(self):
        redis = FakeStreamRedis()
        cmd = command()
        message_id = await enqueue_telegram_otp_command(redis, command=cmd)
        await finalize_telegram_otp_command(
            redis,
            message_id=message_id,
            otp_request_id=cmd.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.SENT,
            digest=command_hash(cmd),
            ttl_seconds=30,
        )
        await finalize_telegram_otp_command(
            redis,
            message_id=message_id,
            otp_request_id=cmd.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.SENT,
            digest=command_hash(cmd),
            ttl_seconds=30,
        )
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])
        self.assertIn(f"{TelegramOTPDeliveryOutcome.SENT.value}:{command_hash(cmd)}", redis.values.values())

    async def test_receipt_before_delete_crash_does_not_resend(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            fields = dict(redis.streams[OTP_EQ_STREAM][0][1])
            await finalize_telegram_otp_command(
                redis,
                message_id=message_id,
                otp_request_id=cmd.otp_request_id,
                outcome=TelegramOTPDeliveryOutcome.SENT,
                digest=command_hash(cmd),
                ttl_seconds=30,
            )
            redis.streams[OTP_EQ_STREAM] = [(message_id, fields)]
            redis.acked.discard(message_id)
            redis.deleted.discard(message_id)
            redis.pel[message_id] = {"consumer": "bot", "deliveries": 2, "last_deliver_ms": 0}
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=2,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.SENT)
        send.assert_not_awaited()
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_pipeline_failure_recovers_without_resend(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            fields = dict(redis.streams[OTP_EQ_STREAM][0][1])
            redis.fail_pipeline = True
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=1,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.SENT)
        send.assert_awaited_once()
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_health_counts_outstanding_not_historical_xlen(self):
        redis = FakeStreamRedis()
        await redis.xgroup_create(OTP_EQ_STREAM, OTP_EQ_GROUP, mkstream=True)
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            await enqueue_telegram_otp_command(redis, command=cmd)
            before = await inspect_telegram_otp_ephemeral_health(redis)
            self.assertEqual(before.pending_count, 1)
            self.assertIsNotNone(before.oldest_command_age_seconds)
            self.assertIsNone(before.error)
            processed = await run_telegram_otp_ephemeral_once(redis, consumer="bot")
        self.assertEqual(processed, 1)
        after = await inspect_telegram_otp_ephemeral_health(redis)
        self.assertEqual(after.pending_count, 0)
        self.assertIsNone(after.oldest_command_age_seconds)
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_health_has_no_identity_labels(self):
        redis = FakeStreamRedis()
        health = await inspect_telegram_otp_ephemeral_health(redis)
        payload = str(health)
        self.assertNotIn("12345", payload)
        self.assertFalse(health.worker_present)
        self.assertEqual(health.pending_count, 0)
        self.assertIsNone(health.error)

    async def test_health_is_not_green_when_group_metadata_is_inconsistent(self):
        redis = FakeStreamRedis()
        await redis.xgroup_create(OTP_EQ_STREAM, OTP_EQ_GROUP, mkstream=True)
        await enqueue_telegram_otp_command(redis, command=command())
        redis.groups[OTP_EQ_STREAM][OTP_EQ_GROUP]["last_delivered_id"] = "0-0"

        async def _bad_xpending(name, group):
            del name, group
            return [9, None, None, []]

        redis.xpending = _bad_xpending
        health = await inspect_telegram_otp_ephemeral_health(redis)
        self.assertEqual(health.error, "otp_eq_health_pending_inconsistent")
        self.assertIsNone(health.pending_count)
        self.assertIsNone(health.oldest_command_age_seconds)

    async def test_delivery_counts_come_from_pending_metadata(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2], patch(
            "core.services.telegram_otp_ephemeral_queue.OTP_EQ_CLAIM_IDLE_MS",
            0,
        ):
            await enqueue_telegram_otp_command(redis, command=cmd)
            await ensure_and_read(redis, expected=1)
            first = await run_telegram_otp_ephemeral_once(redis, consumer="bot")
            self.assertEqual(first, 1)
            send.assert_awaited_once()
            await enqueue_telegram_otp_command(redis, command=command())
            await redis.xreadgroup(OTP_EQ_GROUP, "bot", streams={OTP_EQ_STREAM: ">"}, count=1)
            observed = []
            for expected in (2, 3, 4):
                messages = await read_only(redis)
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0][2], expected)
                observed.append(messages[0][2])
            self.assertEqual(observed, [2, 3, 4])
            leftover_id, leftover_fields, leftover_deliveries = (
                await read_only(redis)
            )[0]
            self.assertEqual(leftover_deliveries, 5)
            self.assertGreater(leftover_deliveries, OTP_EQ_MAX_DELIVERIES)
            send.reset_mock()
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=leftover_id,
                fields=leftover_fields,
                deliveries=leftover_deliveries,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.PROVIDER_ERROR)
        send.assert_not_awaited()
        self.assertEqual(len(redis.streams[OTP_EQ_POISON_STREAM]), 1)
        self.assertEqual(redis.streams[OTP_EQ_POISON_STREAM][0][1]["reason"], "max_deliveries")
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_delivery_lookup_targets_message_beyond_first_sixteen_pending(self):
        redis = FakeStreamRedis()
        await redis.xgroup_create(OTP_EQ_STREAM, OTP_EQ_GROUP, mkstream=True)
        commands = [command() for _index in range(18)]
        for item in commands:
            await enqueue_telegram_otp_command(redis, command=item)
        await redis.xreadgroup(
            OTP_EQ_GROUP,
            "stalled",
            streams={OTP_EQ_STREAM: ">"},
            count=17,
        )
        for pending in redis.pel.values():
            pending["last_deliver_ms"] = redis._now_ms

        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            messages = await read_only(redis)
            self.assertEqual(len(messages), 1)
            message_id, fields, deliveries = messages[0]
            self.assertEqual(deliveries, 1)
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=deliveries,
            )

        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.SENT)
        send.assert_awaited_once()
        self.assertEqual(redis.streams[OTP_EQ_POISON_STREAM], [])

    async def test_unknown_delivery_count_is_fail_closed(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            fields = dict(redis.streams[OTP_EQ_STREAM][0][1])
            outcome = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=None,
            )
        self.assertEqual(outcome, TelegramOTPDeliveryOutcome.PROVIDER_ERROR)
        send.assert_not_awaited()
        self.assertEqual(redis.streams[OTP_EQ_POISON_STREAM][0][1]["reason"], "delivery_count_unknown")
        self.assertEqual(redis.streams[OTP_EQ_STREAM], [])

    async def test_max_deliveries_quarantines_once(self):
        redis = FakeStreamRedis()
        cmd = command()
        send, patches = self._send_patches()
        with patches[0], patches[1], patches[2]:
            message_id = await enqueue_telegram_otp_command(redis, command=cmd)
            fields = dict(redis.streams[OTP_EQ_STREAM][0][1])
            first = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=4,
            )
            redis.streams[OTP_EQ_STREAM] = [(message_id, fields)]
            second = await process_telegram_otp_stream_message(
                redis,
                message_id=message_id,
                fields=fields,
                deliveries=5,
            )
        self.assertEqual(first, TelegramOTPDeliveryOutcome.PROVIDER_ERROR)
        self.assertEqual(second, TelegramOTPDeliveryOutcome.PROVIDER_ERROR)
        send.assert_not_awaited()
        self.assertEqual(len(redis.streams[OTP_EQ_POISON_STREAM]), 1)

    async def test_quarantine_is_idempotent_across_crash_before_terminal_receipt(self):
        redis = FakeStreamRedis()
        request_id = str(uuid4())
        fields = {
            "payload": "not-valid-ciphertext",
            "request_id": request_id,
            "command_hash": "synthetic-digest",
        }
        with patch(
            "core.services.telegram_otp_ephemeral_queue.finalize_telegram_otp_command",
            new=AsyncMock(side_effect=RuntimeError("crash_after_poison")),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash_after_poison"):
                await process_telegram_otp_stream_message(
                    redis,
                    message_id="1-9",
                    fields=fields,
                    deliveries=1,
                )

        replay = await process_telegram_otp_stream_message(
            redis,
            message_id="1-9",
            fields=fields,
            deliveries=2,
        )

        self.assertEqual(replay, TelegramOTPDeliveryOutcome.INVALID)
        self.assertEqual(len(redis.streams[OTP_EQ_POISON_STREAM]), 1)
        self.assertEqual(redis.streams[OTP_EQ_POISON_STREAM][0][1]["reason"], "decrypt_or_contract")

    async def test_iran_cannot_enqueue(self):
        with override_current_server(SERVER_IRAN):
            with self.assertRaisesRegex(RuntimeError, "requires_foreign"):
                await enqueue_telegram_otp_and_wait(FakeStreamRedis(), command=command())

    def test_command_hash_changes_when_otp_changes(self):
        original = command()
        changed = original.model_copy(update={"otp_code": "54321"})
        self.assertNotEqual(command_hash(original), command_hash(changed))

    def test_poison_bound_is_documented(self):
        self.assertEqual(OTP_EQ_POISON_MAXLEN, 256)


async def ensure_and_read(redis, expected):
    from core.services.telegram_otp_ephemeral_queue import (
        _read_group_messages,
        ensure_otp_eq_group,
    )

    await ensure_otp_eq_group(redis)
    messages = await _read_group_messages(redis, consumer="bot", count=1)
    if expected is not None:
        assert messages[0][2] == expected
    return messages


async def read_only(redis):
    from core.services.telegram_otp_ephemeral_queue import _read_group_messages

    return await _read_group_messages(redis, consumer="bot", count=1)


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

    def test_foreign_and_bot_cannot_enable_sms_fallback_or_keep_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "sms_fallback_forbidden_outside_iran_api"):
            validate_non_iran_sms_isolation(
                SimpleNamespace(
                    otp_sms_auto_fallback_enabled=True,
                    server_mode=SERVER_FOREIGN,
                    trading_bot_service="api",
                    smsir_api_key=None,
                )
            )
        with self.assertRaisesRegex(RuntimeError, "sms_credential_forbidden_outside_iran_api"):
            validate_non_iran_sms_isolation(
                SimpleNamespace(
                    otp_sms_auto_fallback_enabled=False,
                    server_mode=SERVER_FOREIGN,
                    trading_bot_service="bot",
                    smsir_api_key="present",
                )
            )
        validate_non_iran_sms_isolation(
            SimpleNamespace(
                otp_sms_auto_fallback_enabled=False,
                server_mode=SERVER_FOREIGN,
                trading_bot_service="bot",
                smsir_api_key=None,
            )
        )


if __name__ == "__main__":
    unittest.main()
