import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.bot_access_policy import BotAccessDecision
from core.services.telegram_notification_outbox_queue_service import (
    NOTIFICATION_OUTBOX_QUEUE_HANDOFF,
    handoff_next_due_telegram_notification_outbox,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
    TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
    TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH,
    TelegramNotificationRecipient,
    enqueue_telegram_notification_once,
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_delivery_repeat_offer_freshness import RepeatOfferResponseSnapshot
from models.telegram_notification_outbox import TelegramNotificationOutboxStatus
from models.user import User


NOW = datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc)


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.flush_count = 0

    async def get(self, model, record_id):
        if model is User and int(record_id) == int(self.user.id):
            return self.user
        return None

    async def flush(self):
        self.flush_count += 1


def _outbox():
    source_id = "repeat-success:bot-repeat:intent-1"
    return SimpleNamespace(
        id=91,
        dedupe_key=telegram_notification_dedupe_key(
            source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
            source_id=source_id,
            recipient_user_id=7,
        ),
        source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
        source_id=source_id,
        recipient_user_id=7,
        telegram_id_at_enqueue=7007,
        text=TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
        parse_mode=None,
        status=TelegramNotificationOutboxStatus.PENDING,
        extra_payload={
            "response_kind": TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH
        },
        queue_job_id=None,
        queue_handed_off_at=None,
        worker_id=None,
        lease_until=None,
        next_retry_at=None,
        reason=None,
        updated_at=None,
    )


class TelegramRepeatOfferQueueBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_idempotent_enqueue_rejects_invalid_recipient_before_db_access(self):
        with self.assertRaisesRegex(
            ValueError,
            "telegram_notification_recipient_invalid",
        ):
            await enqueue_telegram_notification_once(
                SimpleNamespace(),
                recipient=TelegramNotificationRecipient(
                    user_id=7,
                    telegram_id=0,
                ),
                text=TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
                source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
                source_id="repeat-success:invalid-recipient",
            )

    async def test_repeat_response_handoff_uses_offer_control_m1_action(self):
        outbox = _outbox()
        user = SimpleNamespace(
            id=7,
            telegram_id=7007,
            sync_version=3,
            role="user",
        )
        db = FakeDB(user)
        payload = {
            "chat_id": 7007,
            "text": TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
            "parse_mode": None,
            "reply_markup": {"keyboard": [[{"text": "📈 معامله"}]]},
        }
        enqueue_result = SimpleNamespace(
            job=SimpleNamespace(id=811),
            created=True,
        )

        with patch(
            "core.services.telegram_notification_outbox_queue_service."
            "_select_next_due_outbox",
            new=AsyncMock(return_value=outbox),
        ), patch(
            "core.services.telegram_notification_outbox_queue_service."
            "evaluate_bot_access",
            new=AsyncMock(return_value=BotAccessDecision(True)),
        ), patch(
            "core.services.telegram_notification_outbox_queue_service."
            "build_repeat_offer_response_snapshot",
            new=AsyncMock(
                return_value=RepeatOfferResponseSnapshot(
                    payload=payload,
                    source_version=3,
                )
            ),
        ), patch(
            "core.services.telegram_notification_outbox_queue_service."
            "enqueue_telegram_delivery_job",
            new=AsyncMock(return_value=enqueue_result),
        ) as enqueue:
            result = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                now=NOW,
            )

        self.assertEqual(result.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        self.assertEqual(outbox.queue_job_id, 811)
        self.assertEqual(outbox.queue_handed_off_at, NOW)
        self.assertEqual(enqueue.await_args.kwargs["feeder"], TelegramFeederKind.OFFER_CONTROL)
        self.assertEqual(
            enqueue.await_args.kwargs["action"],
            TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
        )
        self.assertEqual(
            enqueue.await_args.kwargs["destination_class"],
            TelegramDestinationClass.PRIVATE,
        )
        self.assertEqual(enqueue.await_args.kwargs["method"], "sendMessage")
        self.assertIsNone(enqueue.await_args.kwargs["campaign_id"])
        self.assertEqual(enqueue.await_args.kwargs["payload"], payload)


if __name__ == "__main__":
    unittest.main()
