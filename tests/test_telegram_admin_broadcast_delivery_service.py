import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_gateway
from core.services import telegram_admin_broadcast_delivery_service as service
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
)
from models.user import User


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


def make_broadcast(**overrides):
    data = {
        "id": 51,
        "content": "پیام اطلاع‌رسانی",
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
        "sent_at": None,
        "terminal_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TelegramAdminBroadcastDeliveryServiceTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
