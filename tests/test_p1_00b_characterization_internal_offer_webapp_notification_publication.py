"""P1-00b characterization: internal offer WebApp notification publication contract.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: docs/refactor/two-server/section-1/inventory/scenario-records.json#ORIGIN-INTERNAL__WEBAPP__NOTIFICATION

Explicit exclusions:
- durable persistence, delivery worker behavior, retry behavior, trade execution,
  audience construction, endpoint SUCCESS, combined POLICY-SNAPSHOT, combined
  SIDE-EFFECT-RETRY, Telegram delivery, seed acceptance, scenario-record edits,
  reconciliation, blocker closure, and Stage closure.
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.enums import NotificationCategory, NotificationLevel
from core.services.trade_delivery_receipt_service import (
    publish_webapp_notification_after_commit,
)
from models.notification import Notification


class P100bInternalOfferWebappNotificationPublicationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize post-commit SSE payload and Web Push scheduling for internal WebApp notifications."""

    async def test_exact_sse_payload_and_web_push_arguments(self) -> None:
        """Prove post-commit publication increments unread count, emits SSE payload, and schedules Web Push."""
        nested_extra_payload = {
            "trade_id": 501,
            "trade_number": 10025,
            "offer_id": 7,
            "offer_home_server": "iran",
            "delivery_receipt_id": 9001,
            "recipient_user_id": 5,
            "recipient_role": "responder",
            "principal_user_id": 5,
            "side": "responder",
        }
        created_at = datetime(2026, 9, 16, 12, 30, tzinfo=timezone.utc)
        notification = Notification(
            id=7301,
            user_id=5,
            message="معامله ۱۰۰۲۵ انجام شد.",
            is_read=False,
            created_at=created_at,
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.TRADE,
            dedupe_key="trade_completed:webapp:10025:5",
            extra_payload=nested_extra_payload,
        )

        expected_nested_extra_payload = {
            "trade_id": 501,
            "trade_number": 10025,
            "offer_id": 7,
            "offer_home_server": "iran",
            "delivery_receipt_id": 9001,
            "recipient_user_id": 5,
            "recipient_role": "responder",
            "principal_user_id": 5,
            "side": "responder",
        }
        expected_payload = {
            "id": 7301,
            "message": "معامله ۱۰۰۲۵ انجام شد.",
            "is_read": False,
            "created_at": "2026-09-16T12:30:00+00:00",
            "level": "SUCCESS",
            "category": "TRADE",
            "dedupe_key": "trade_completed:webapp:10025:5",
            "extra_payload": expected_nested_extra_payload,
            "trade_id": 501,
            "trade_number": 10025,
            "offer_id": 7,
            "offer_home_server": "iran",
            "delivery_receipt_id": 9001,
            "recipient_user_id": 5,
            "recipient_role": "responder",
            "principal_user_id": 5,
            "side": "responder",
        }

        fake_redis_client = AsyncMock()
        fake_redis_cm = MagicMock()
        fake_redis_cm.__aenter__ = AsyncMock(return_value=fake_redis_client)
        fake_redis_cm.__aexit__ = AsyncMock(return_value=None)
        fake_redis_cls = MagicMock(return_value=fake_redis_cm)

        mock_publish_user_event = AsyncMock()
        mock_schedule_web_push = MagicMock()

        with (
            patch(
                "core.services.trade_delivery_receipt_service.redis.Redis",
                fake_redis_cls,
            ),
            patch(
                "core.services.trade_delivery_receipt_service.publish_user_event",
                mock_publish_user_event,
            ),
            patch(
                "core.web_push.schedule_notification_web_push",
                mock_schedule_web_push,
            ),
        ):
            await publish_webapp_notification_after_commit(
                user_id=5,
                notification=notification,
            )

        # Assert unread counter increment exactly once for user:5:unread_count
        fake_redis_client.incr.assert_awaited_once_with("user:5:unread_count")
        self.assertEqual(fake_redis_client.incr.await_count, 1)

        # Assert publish_user_event was awaited exactly once with positional arguments
        mock_publish_user_event.assert_awaited_once_with(5, "message", expected_payload)
        self.assertEqual(mock_publish_user_event.await_count, 1)
        call_args = mock_publish_user_event.await_args
        self.assertEqual(call_args.args[0], 5)
        self.assertEqual(call_args.args[1], "message")
        actual_payload = call_args.args[2]
        self.assertEqual(actual_payload, expected_payload)

        # Assert individual notification fields and nested extra_payload
        self.assertEqual(actual_payload["id"], 7301)
        self.assertEqual(actual_payload["message"], "معامله ۱۰۰۲۵ انجام شد.")
        self.assertIs(actual_payload["is_read"], False)
        self.assertEqual(actual_payload["created_at"], "2026-09-16T12:30:00+00:00")
        self.assertEqual(actual_payload["level"], "SUCCESS")
        self.assertEqual(actual_payload["category"], "TRADE")
        self.assertEqual(actual_payload["dedupe_key"], "trade_completed:webapp:10025:5")
        self.assertEqual(actual_payload["extra_payload"], expected_nested_extra_payload)

        # Assert each flattened provenance field
        for key in (
            "trade_id",
            "trade_number",
            "offer_id",
            "offer_home_server",
            "delivery_receipt_id",
            "recipient_user_id",
            "recipient_role",
            "principal_user_id",
            "side",
        ):
            self.assertEqual(actual_payload[key], expected_nested_extra_payload[key])

        # Assert Web Push scheduling was called exactly once with positional arguments
        mock_schedule_web_push.assert_called_once_with(7301, expected_nested_extra_payload)
        self.assertEqual(mock_schedule_web_push.call_args.args, (7301, expected_nested_extra_payload))


if __name__ == "__main__":
    unittest.main()
