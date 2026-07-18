import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from core.services.telegram_notification_outbox_service import (
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_notification_action_freshness import (
    build_telegram_notification_action_snapshot,
    telegram_notification_action_outbox_matches_current_user,
    telegram_notification_action_source_natural_id,
)


def make_outbox(*, source_id: str, extra_payload: dict, telegram_id: int = 7007):
    source_type = "queue_action:account_status"
    return SimpleNamespace(
        source_type=source_type,
        source_id=source_id,
        recipient_user_id=7,
        telegram_id_at_enqueue=telegram_id,
        dedupe_key=telegram_notification_dedupe_key(
            source_type=source_type,
            source_id=source_id,
            recipient_user_id=7,
        ),
        text="پیام وضعیت حساب",
        parse_mode="Markdown",
        extra_payload=extra_payload,
    )


class TelegramDeliveryAccountNoticeFreshnessTests(unittest.TestCase):
    def test_active_restriction_requires_exact_state_and_user_version(self):
        outbox = make_outbox(
            source_id="restriction:7:5",
            extra_payload={
                "account_notice_kind": "restriction_active",
                "queue_action": "account_status",
                "restriction_kind": "block",
                "restriction_snapshot": {
                    "trading_restricted_until": "2026-07-20T10:30:00+00:00"
                },
                "user_sync_version": 5,
            },
        )
        user = SimpleNamespace(
            id=7,
            telegram_id=7007,
            sync_version=5,
            trading_restricted_until=datetime(
                2026,
                7,
                20,
                10,
                30,
                tzinfo=timezone.utc,
            ),
        )

        self.assertTrue(
            telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )
        source_identity = telegram_notification_action_source_natural_id(outbox)
        self.assertIn(":payload-v1:", source_identity)
        self.assertFalse(
            telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
                now=datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc),
            )
        )

        user.sync_version = 5
        user.telegram_id = 7008
        self.assertFalse(
            telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )

        user.sync_version = 6
        self.assertFalse(
            telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
                now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        )

    def test_deleted_account_uses_enqueue_route_after_user_route_is_cleared(self):
        outbox = make_outbox(
            source_id="deleted:7:6",
            telegram_id=7999,
            extra_payload={
                "account_notice_kind": "account_deleted",
                "deleted_account_snapshot": {
                    "deleted_at": "2026-07-18T12:00:00+00:00"
                },
                "queue_action": "account_status",
                "user_sync_version": 6,
            },
        )
        user = SimpleNamespace(
            id=7,
            telegram_id=None,
            sync_version=6,
            is_deleted=True,
            deleted_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            account_status="active",
            messenger_blocked_at=None,
        )

        self.assertTrue(
            telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
            )
        )
        snapshot = build_telegram_notification_action_snapshot(outbox, user)
        self.assertEqual(snapshot.payload["chat_id"], 7999)

        user.is_deleted = False
        self.assertFalse(
            telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
            )
        )


if __name__ == "__main__":
    unittest.main()
