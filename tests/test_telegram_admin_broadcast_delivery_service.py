import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_gateway
from core.services import telegram_admin_broadcast_delivery_service as service
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.user import User


NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


class FakeDeliveryDB:
    def __init__(self, *, broadcast=None, user=None):
        self.broadcast = broadcast
        self.user = user
        self.flush_count = 0
        self.get_calls = []

    async def get(self, model, object_id):
        self.get_calls.append((model, object_id))
        if model is TelegramAdminBroadcast:
            return self.broadcast
        if model is User:
            return self.user
        return None

    async def flush(self):
        self.flush_count += 1


class FakeResult:
    def __init__(self, rows=None, scalars=None):
        self._rows = list(rows or [])
        self._scalars = list(scalars or [])

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars))


class FakeFinalizeDB(FakeDeliveryDB):
    def __init__(self, *, broadcast, rows):
        super().__init__(broadcast=broadcast)
        self.rows = rows
        self.execute_calls = []

    async def execute(self, statement):
        self.execute_calls.append(statement)
        return FakeResult(rows=self.rows)


class FakeLeaseRecoveryDB:
    def __init__(self, receipts):
        self.receipts = list(receipts)
        self.execute_calls = []
        self.flush_count = 0

    async def execute(self, statement):
        self.execute_calls.append(statement)
        return FakeResult(scalars=self.receipts)

    async def flush(self):
        self.flush_count += 1


def make_broadcast(**overrides):
    data = {
        "id": 51,
        "content": "پیام اطلاع‌رسانی",
        "status": TelegramAdminBroadcastStatus.RUNNING,
        "updated_at": None,
        "completed_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_receipt(**overrides):
    data = {
        "id": 71,
        "broadcast_id": 51,
        "recipient_user_id": 9,
        "telegram_id_at_enqueue": 9001,
        "telegram_id_at_send": None,
        "dedupe_key": "telegram-admin-broadcast:51:9",
        "status": TelegramAdminBroadcastReceiptStatus.SENDING,
        "reason": None,
        "telegram_message_id": None,
        "attempt_count": 1,
        "next_retry_at": None,
        "last_error_class": None,
        "last_error_message": None,
        "worker_id": "worker",
        "lease_until": object(),
        "queue_job_id": None,
        "queue_handed_off_at": None,
        "sent_at": None,
        "terminal_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TelegramAdminBroadcastDeliveryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_delivery_refuses_queue_owner_before_gateway(self):
        gateway_send = AsyncMock()
        with patch(
            "core.services.telegram_admin_broadcast_delivery_service."
            "configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "legacy_admin_broadcast_direct_sender_is_not_runtime_owner",
            ):
                await service.deliver_claimed_telegram_admin_broadcast_receipt(
                    FakeDeliveryDB(),
                    make_receipt(),
                    current_server="foreign",
                    gateway_send=gateway_send,
                )
        gateway_send.assert_not_awaited()

    def test_failure_classifier_splits_retryable_skipped_and_terminal_errors(self):
        rate_limited = service.classify_telegram_admin_broadcast_failure(
            telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_json={"parameters": {"retry_after": 90}},
            )
        )
        self.assertEqual(rate_limited.status, TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED)
        self.assertEqual(rate_limited.reason, "telegram_rate_limited")
        self.assertEqual(rate_limited.retry_after_seconds, 90)

        blocked = service.classify_telegram_admin_broadcast_failure(
            telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=403,
                response_text="Forbidden: bot was blocked by the user",
            )
        )
        self.assertEqual(blocked.status, TelegramAdminBroadcastReceiptStatus.SKIPPED)
        self.assertEqual(blocked.reason, "telegram_user_unreachable")

        missing_token = service.classify_telegram_admin_broadcast_failure(
            telegram_gateway.TelegramGatewayResult(ok=False, method="sendMessage", error="missing_bot_token")
        )
        self.assertEqual(missing_token.status, TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED)
        self.assertTrue(missing_token.alert_required)

    def test_bounded_retry_delay_uses_retry_after_or_exponential_backoff(self):
        rate_limited = service.TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
            reason="telegram_rate_limited",
            retry_after_seconds=90,
        )
        receipt = make_receipt(attempt_count=3)
        self.assertEqual(
            service.bounded_retry_delay_seconds(rate_limited, receipt=receipt, max_jitter_seconds=0),
            90,
        )

        server_error = service.TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
            reason="telegram_server_error",
        )
        self.assertEqual(
            service.bounded_retry_delay_seconds(server_error, receipt=receipt, max_jitter_seconds=0),
            8,
        )
        self.assertEqual(
            service.bounded_retry_delay_seconds(server_error, receipt=make_receipt(attempt_count=20), max_jitter_seconds=0),
            256,
        )

    async def test_delivery_is_foreign_only_and_does_not_call_gateway_on_iran(self):
        receipt = make_receipt()
        gateway_send = AsyncMock()

        result = await service.deliver_claimed_telegram_admin_broadcast_receipt(
            FakeDeliveryDB(),
            receipt,
            current_server="iran",
            gateway_send=gateway_send,
        )

        self.assertEqual(result.status, service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_BLOCKED_WRONG_SERVER)
        self.assertEqual(result.reason, "telegram_foreign_only")
        gateway_send.assert_not_awaited()
        self.assertEqual(receipt.status, TelegramAdminBroadcastReceiptStatus.SENDING)

    async def test_delivery_sends_plain_text_to_current_telegram_id_and_marks_sent(self):
        receipt = make_receipt(telegram_id_at_enqueue=9001)
        db = FakeDeliveryDB(
            broadcast=make_broadcast(content="متن ساده"),
            user=SimpleNamespace(id=9, telegram_id=9010),
        )
        gateway_calls = []

        async def fake_send(chat_id, text, **kwargs):
            gateway_calls.append((chat_id, text, kwargs))
            return telegram_gateway.TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                response_json={"result": {"message_id": 777}},
            )

        with patch(
            "core.services.telegram_admin_broadcast_delivery_service.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.services.telegram_admin_broadcast_delivery_service.finalize_telegram_admin_broadcast_status",
            new=AsyncMock(),
        ) as finalize:
            result = await service.deliver_claimed_telegram_admin_broadcast_receipt(
                db,
                receipt,
                current_server="foreign",
                gateway_send=fake_send,
                bot_token="token",
            )

        self.assertEqual(result.status, service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SENT)
        self.assertEqual(receipt.status, TelegramAdminBroadcastReceiptStatus.SENT)
        self.assertEqual(receipt.reason, "sent")
        self.assertEqual(receipt.telegram_id_at_send, 9010)
        self.assertEqual(receipt.telegram_message_id, 777)
        self.assertIsNone(receipt.worker_id)
        self.assertIsNone(receipt.lease_until)
        self.assertEqual(gateway_calls, [(9010, "متن ساده", {
            "parse_mode": None,
            "bot_token": "token",
            "idempotency_key": "telegram-admin-broadcast:51:9:attempt:1",
        })])
        finalize.assert_awaited_once()

    async def test_delivery_skips_when_user_unlinked_or_access_denied_after_enqueue(self):
        receipt = make_receipt()
        db = FakeDeliveryDB(broadcast=make_broadcast(), user=SimpleNamespace(id=9, telegram_id=None))
        gateway_send = AsyncMock()

        with patch(
            "core.services.telegram_admin_broadcast_delivery_service.finalize_telegram_admin_broadcast_status",
            new=AsyncMock(),
        ):
            result = await service.deliver_claimed_telegram_admin_broadcast_receipt(
                db,
                receipt,
                current_server="foreign",
                gateway_send=gateway_send,
            )

        self.assertEqual(result.status, service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(receipt.status, TelegramAdminBroadcastReceiptStatus.SKIPPED)
        self.assertEqual(receipt.reason, "telegram_unlinked_current")
        gateway_send.assert_not_awaited()

        receipt = make_receipt()
        db = FakeDeliveryDB(broadcast=make_broadcast(), user=SimpleNamespace(id=9, telegram_id=9010))
        with patch(
            "core.services.telegram_admin_broadcast_delivery_service.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=False, reason="customer_tier2")),
        ), patch(
            "core.services.telegram_admin_broadcast_delivery_service.finalize_telegram_admin_broadcast_status",
            new=AsyncMock(),
        ):
            result = await service.deliver_claimed_telegram_admin_broadcast_receipt(
                db,
                receipt,
                current_server="foreign",
                gateway_send=gateway_send,
            )

        self.assertEqual(result.status, service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SKIPPED)
        self.assertEqual(receipt.reason, "customer_tier2")
        gateway_send.assert_not_awaited()

    async def test_retryable_failure_uses_backoff_and_retry_exhaustion_goes_terminal(self):
        receipt = make_receipt(attempt_count=3)
        db = FakeDeliveryDB(
            broadcast=make_broadcast(content="متن ساده"),
            user=SimpleNamespace(id=9, telegram_id=9010),
        )

        async def server_error_send(*_args, **_kwargs):
            return telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=500,
                response_text="server error",
            )

        with patch(
            "core.services.telegram_admin_broadcast_delivery_service.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.services.telegram_admin_broadcast_delivery_service.finalize_telegram_admin_broadcast_status",
            new=AsyncMock(),
        ):
            result = await service.deliver_claimed_telegram_admin_broadcast_receipt(
                db,
                receipt,
                current_server="foreign",
                gateway_send=server_error_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_RETRY_PENDING)
        self.assertEqual(receipt.status, TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED)
        self.assertEqual(receipt.reason, "telegram_server_error")
        self.assertEqual(result.retry_after_seconds, 8 + service._stable_retry_jitter_seconds(receipt=receipt, attempt_count=3))
        self.assertEqual(receipt.next_retry_at, NOW + timedelta(seconds=result.retry_after_seconds))
        self.assertIsNone(receipt.worker_id)
        self.assertIsNone(receipt.lease_until)

        exhausted = make_receipt(attempt_count=service.MAX_RETRY_ATTEMPTS)
        db = FakeDeliveryDB(
            broadcast=make_broadcast(content="متن ساده"),
            user=SimpleNamespace(id=9, telegram_id=9010),
        )
        with patch(
            "core.services.telegram_admin_broadcast_delivery_service.evaluate_bot_access",
            new=AsyncMock(return_value=SimpleNamespace(allowed=True, reason=None)),
        ), patch(
            "core.services.telegram_admin_broadcast_delivery_service.finalize_telegram_admin_broadcast_status",
            new=AsyncMock(),
        ):
            result = await service.deliver_claimed_telegram_admin_broadcast_receipt(
                db,
                exhausted,
                current_server="foreign",
                gateway_send=server_error_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_TERMINAL_FAILED)
        self.assertEqual(exhausted.status, TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED)
        self.assertEqual(exhausted.reason, "telegram_retry_exhausted")
        self.assertIsNone(exhausted.next_retry_at)
        self.assertEqual(exhausted.terminal_at, NOW)

    async def test_finalize_broadcast_status_taxonomy(self):
        cases = [
            (
                [(TelegramAdminBroadcastReceiptStatus.SENT, 2)],
                TelegramAdminBroadcastStatus.COMPLETED,
            ),
            (
                [(TelegramAdminBroadcastReceiptStatus.SENT, 1), (TelegramAdminBroadcastReceiptStatus.SKIPPED, 1)],
                TelegramAdminBroadcastStatus.COMPLETED_WITH_ERRORS,
            ),
            (
                [(TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED, 2)],
                TelegramAdminBroadcastStatus.FAILED,
            ),
            (
                [(TelegramAdminBroadcastReceiptStatus.SENT, 1), (TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED, 1)],
                TelegramAdminBroadcastStatus.RUNNING,
            ),
        ]
        for rows, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                broadcast = make_broadcast(status=TelegramAdminBroadcastStatus.RUNNING)
                db = FakeFinalizeDB(broadcast=broadcast, rows=rows)

                result = await service.finalize_telegram_admin_broadcast_status(db, broadcast_id=51, now=NOW)

                self.assertEqual(result, expected_status)
                self.assertEqual(broadcast.status, expected_status)
                if expected_status in {
                    TelegramAdminBroadcastStatus.COMPLETED,
                    TelegramAdminBroadcastStatus.COMPLETED_WITH_ERRORS,
                    TelegramAdminBroadcastStatus.FAILED,
                }:
                    self.assertEqual(broadcast.completed_at, NOW)

    async def test_expired_lease_recovery_releases_worker_state(self):
        receipt = make_receipt(
            status=TelegramAdminBroadcastReceiptStatus.SENDING,
            worker_id="stale-worker",
            lease_until=NOW - timedelta(seconds=10),
            next_retry_at=None,
        )
        db = FakeLeaseRecoveryDB([receipt])

        recovered = await service.recover_expired_telegram_admin_broadcast_leases(
            db,
            current_server="foreign",
            now=NOW,
        )

        self.assertEqual(recovered, [receipt])
        self.assertEqual(receipt.status, TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED)
        self.assertEqual(receipt.reason, "lease_expired")
        self.assertIsNone(receipt.worker_id)
        self.assertIsNone(receipt.lease_until)
        self.assertEqual(receipt.next_retry_at, NOW)
        self.assertEqual(db.flush_count, 1)

        iran_db = FakeLeaseRecoveryDB([make_receipt()])
        self.assertEqual(
            await service.recover_expired_telegram_admin_broadcast_leases(iran_db, current_server="iran", now=NOW),
            [],
        )
        self.assertEqual(iran_db.flush_count, 0)


if __name__ == "__main__":
    unittest.main()
