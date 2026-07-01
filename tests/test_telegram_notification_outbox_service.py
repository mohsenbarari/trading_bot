import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import telegram_gateway
from core.services.bot_access_policy import BotAccessDecision
from core.services import telegram_notification_outbox_service as service
from models.telegram_notification_outbox import TelegramNotificationOutbox, TelegramNotificationOutboxStatus


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


class FakeQueueDB:
    def __init__(self):
        self.added = []
        self.flush_count = 0
        self._next_id = 100

    def add_all(self, objects):
        self.added.extend(objects)

    async def flush(self):
        self.flush_count += 1
        for obj in self.added:
            if isinstance(obj, TelegramNotificationOutbox) and getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1


class FakeDeliveryDB:
    def __init__(self, *, user=None):
        self.user = user
        self.flush_count = 0

    async def get(self, model, record_id):
        return self.user

    async def flush(self):
        self.flush_count += 1


def _outbox(**overrides):
    values = {
        "id": 1,
        "dedupe_key": "telegram-notification:project_user_joined:9:7",
        "source_type": service.TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
        "source_id": "9",
        "recipient_user_id": 7,
        "telegram_id_at_enqueue": 7007,
        "text": "ali به لیست همکاران اضافه شدند.",
        "parse_mode": None,
        "status": TelegramNotificationOutboxStatus.SENDING,
        "attempt_count": 1,
        "extra_payload": {"exclude_customers": True},
        "created_at": NOW,
    }
    values.update(overrides)
    return TelegramNotificationOutbox(**values)


class TelegramNotificationOutboxServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_dedupe_key_is_source_and_recipient_scoped(self):
        self.assertEqual(
            service.telegram_notification_dedupe_key(
                source_type="project_user_joined",
                source_id=9,
                recipient_user_id=7,
            ),
            "telegram-notification:project_user_joined:9:7",
        )

    async def test_enqueue_creates_pending_rows_without_calling_telegram(self):
        db = FakeQueueDB()

        rows = await service.enqueue_telegram_notifications(
            db,
            recipients=[
                service.TelegramNotificationRecipient(user_id=7, telegram_id=7007),
                service.TelegramNotificationRecipient(user_id=7, telegram_id=7007),
                service.TelegramNotificationRecipient(user_id=8, telegram_id=8008),
            ],
            text=" پیام ",
            source_type="project_user_joined",
            source_id=9,
            extra_payload={"exclude_customers": True},
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(db.flush_count, 1)
        self.assertEqual(rows[0].status, TelegramNotificationOutboxStatus.PENDING)
        self.assertEqual(rows[0].text, "پیام")
        self.assertEqual(rows[0].telegram_id_at_enqueue, 7007)
        self.assertEqual(rows[0].dedupe_key, "telegram-notification:project_user_joined:9:7")
        self.assertEqual(rows[1].dedupe_key, "telegram-notification:project_user_joined:9:8")

    async def test_deliver_sends_current_telegram_id_and_marks_sent(self):
        db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
        outbox = _outbox()
        gateway_send = AsyncMock(
            return_value=telegram_gateway.TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"result": {"message_id": 55}},
            )
        )

        with patch.object(service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
            service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
        ):
            result = await service.deliver_claimed_telegram_notification_outbox(
                db,
                outbox,
                current_server="foreign",
                gateway_send=gateway_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SENT)
        gateway_send.assert_awaited_once()
        self.assertEqual(gateway_send.await_args.args[:2], (7777, "ali به لیست همکاران اضافه شدند."))
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
        self.assertEqual(outbox.telegram_id_at_send, 7777)
        self.assertEqual(outbox.telegram_message_id, 55)
        self.assertEqual(outbox.reason, "sent")

    async def test_deliver_skips_current_customer_when_policy_excludes_customers(self):
        db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
        outbox = _outbox()
        gateway_send = AsyncMock()

        with patch.object(service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
            service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=object())
        ):
            result = await service.deliver_claimed_telegram_notification_outbox(
                db,
                outbox,
                current_server="foreign",
                gateway_send=gateway_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_SKIPPED)
        gateway_send.assert_not_awaited()
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SKIPPED)
        self.assertEqual(outbox.reason, "customer_excluded_current")

    async def test_deliver_rate_limit_uses_retry_after(self):
        db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
        outbox = _outbox()
        gateway_send = AsyncMock(
            return_value=telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_json={"parameters": {"retry_after": 42}},
                response_text="Too Many Requests",
            )
        )

        with patch.object(service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
            service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
        ):
            result = await service.deliver_claimed_telegram_notification_outbox(
                db,
                outbox,
                current_server="foreign",
                gateway_send=gateway_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_RETRY_PENDING)
        self.assertGreaterEqual(result.retry_after_seconds, 42)
        self.assertLessEqual(result.retry_after_seconds, 47)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.RETRYABLE_FAILED)
        self.assertEqual(outbox.reason, "telegram_rate_limited")
        self.assertIsNotNone(outbox.next_retry_at)

    async def test_rate_limit_retry_after_can_exceed_generic_backoff_cap(self):
        db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
        outbox = _outbox(attempt_count=service.MAX_RETRY_ATTEMPTS)
        gateway_send = AsyncMock(
            return_value=telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_json={"parameters": {"retry_after": 1200}},
                response_text="Too Many Requests",
            )
        )

        with patch.object(service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
            service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
        ):
            result = await service.deliver_claimed_telegram_notification_outbox(
                db,
                outbox,
                current_server="foreign",
                gateway_send=gateway_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_RETRY_PENDING)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.RETRYABLE_FAILED)
        self.assertEqual(outbox.reason, "telegram_rate_limited")
        self.assertGreaterEqual(result.retry_after_seconds, 1200)
        self.assertLessEqual(result.retry_after_seconds, 1205)
        self.assertEqual(outbox.next_retry_at, NOW + timedelta(seconds=result.retry_after_seconds))
        self.assertIsNone(outbox.terminal_at)

    async def test_retry_exhaustion_still_terminalizes_non_rate_limit_failures(self):
        db = FakeDeliveryDB(user=SimpleNamespace(id=7, telegram_id=7777))
        outbox = _outbox(attempt_count=service.MAX_RETRY_ATTEMPTS)
        gateway_send = AsyncMock(
            return_value=telegram_gateway.TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=500,
                response_text="server error",
            )
        )

        with patch.object(service, "evaluate_bot_access", new=AsyncMock(return_value=BotAccessDecision(True))), patch.object(
            service, "get_active_customer_relation_for_user", new=AsyncMock(return_value=None)
        ):
            result = await service.deliver_claimed_telegram_notification_outbox(
                db,
                outbox,
                current_server="foreign",
                gateway_send=gateway_send,
                now=NOW,
            )

        self.assertEqual(result.status, service.TELEGRAM_NOTIFICATION_DELIVERY_STATUS_TERMINAL_FAILED)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.TERMINAL_FAILED)
        self.assertEqual(outbox.reason, "telegram_retry_exhausted")
        self.assertEqual(outbox.terminal_at, NOW)


if __name__ == "__main__":
    unittest.main()
