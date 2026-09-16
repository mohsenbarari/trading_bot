"""P1-00b characterization: claimed internal offer WebApp notification real delivery publication.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Canonical scenario: docs/refactor/two-server/section-1/inventory/scenario-records.json#ORIGIN-INTERNAL__WEBAPP__NOTIFICATION
Scenario key: origin_internal_webapp_notification_claimed_delivery_real_publication_dispatch

Bridges accepted claimed delivery to actual WebApp publication dispatch by exercising
deliver_claimed_webapp_receipt through its real post-commit publication function while
replacing only external Redis/SSE/Web Push boundaries and the production-isolation check.

Explicit exclusions:
- No trade route or trade execution.
- No request_source_surface or request_pre_gated route assertions.
- No audience construction or intent creation.
- No endpoint SUCCESS assertions.
- No combined POLICY-SNAPSHOT, SIDE-EFFECT-RETRY, or retry/re-entry testing.
- No Telegram delivery.
- No seed acceptance, scenario-record edits, reconciliation, notification-cell or blocker closure, and no Stage closure claims.
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from datetime import datetime, timezone
from typing import Any
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from core.enums import NotificationCategory, NotificationLevel
from core.services.trade_delivery_receipt_service import (
    TRADE_COMPLETED_EVENT_TYPE,
    WEBAPP_DESTINATION_SERVER,
    webapp_notification_dedupe_key,
)
from core.services.trade_webapp_delivery_service import (
    WEBAPP_DELIVERY_STATUS_SENT,
    WebAppTradeDeliveryResult,
    deliver_claimed_webapp_receipt,
)
from models.notification import Notification
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


def _configure_no_expire_on_commit(session: Any) -> None:
    """Prevent DetachedInstanceError on committed models accessed post-commit or post-close."""
    if hasattr(session, "sync_session"):
        session.sync_session.expire_on_commit = False
    if hasattr(session, "expire_on_commit"):
        session.expire_on_commit = False


class P100bInternalOfferWebappNotificationRealDeliveryPublicationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of claimed internal WebApp delivery through real publication dispatch."""

    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Notification.__table__.create(bind=self.engine, checkfirst=True)
        TradeDeliveryReceipt.__table__.create(bind=self.engine, checkfirst=True)
        self.addCleanup(self._dispose_engine)

    def _dispose_engine(self) -> None:
        try:
            TradeDeliveryReceipt.__table__.drop(bind=self.engine, checkfirst=True)
            Notification.__table__.drop(bind=self.engine, checkfirst=True)
        finally:
            self.engine.dispose()

    def _assert_utc_dt_equal(self, actual: datetime | None, expected: datetime) -> None:
        self.assertIsNotNone(actual)
        if actual.tzinfo is None:
            self.assertEqual(actual, expected.replace(tzinfo=None))
        else:
            self.assertEqual(actual.astimezone(timezone.utc), expected.astimezone(timezone.utc))

    async def test_claimed_receipt_runs_real_post_commit_publication_once(self) -> None:
        """Prove claimed PROCESSING receipt commits and runs real post-commit publication dispatch once."""
        event_time = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
        lease_until = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        fixed_now = datetime(2026, 9, 16, 12, 0, 5, tzinfo=timezone.utc)

        trade_id = 501
        trade_number = 10025
        offer_id = 7
        offer_home_server = "iran"
        recipient_user_id = 5
        recipient_role = "responder"
        destination_server = "iran"
        reason = "webapp_required"
        worker_id = "test-worker-p1-00b-iran"
        attempt_count = 1

        expected_message = "معامله #10025 با موفقیت تکمیل شد."
        audit_extra_payload = {
            "trade_id": trade_id,
            "trade_number": trade_number,
            "offer_id": offer_id,
            "offer_home_server": offer_home_server,
            "recipient_user_id": recipient_user_id,
            "recipient_role": recipient_role,
            "principal_user_id": 5,
            "side": "responder",
        }
        audit_payload = {
            "message": expected_message,
            "extra_payload": audit_extra_payload,
        }

        dedupe_key = webapp_notification_dedupe_key(
            trade_number=trade_number,
            recipient_user_id=recipient_user_id,
        )
        self.assertEqual(dedupe_key, "trade_completed:webapp:10025:5")

        # 1. Seed claimed TradeDeliveryReceipt in PROCESSING state
        async with real_async_session(self.engine) as session_seed:
            _configure_no_expire_on_commit(session_seed)
            seeded_receipt = TradeDeliveryReceipt(
                event_type=TRADE_COMPLETED_EVENT_TYPE,
                dedupe_key=dedupe_key,
                trade_id=trade_id,
                trade_number=trade_number,
                offer_id=offer_id,
                recipient_user_id=recipient_user_id,
                recipient_role=recipient_role,
                channel=TradeDeliveryChannel.WEBAPP,
                destination_server=destination_server,
                status=TradeDeliveryReceiptStatus.PROCESSING,
                reason=reason,
                worker_id=worker_id,
                lease_until=lease_until,
                attempt_count=attempt_count,
                audit_payload=audit_payload,
                event_created_at=event_time,
                created_at=event_time,
                updated_at=event_time,
            )
            session_seed.add(seeded_receipt)
            await session_seed.commit()
            seeded_receipt_id = seeded_receipt.id
            self.assertEqual(seeded_receipt_id, 1)

        # 2. Expected literal payloads for real publication dispatch
        expected_nested_extra_payload = {
            "trade_id": 501,
            "trade_number": 10025,
            "offer_id": 7,
            "offer_home_server": "iran",
            "recipient_user_id": 5,
            "recipient_role": "responder",
            "principal_user_id": 5,
            "side": "responder",
            "delivery_receipt_id": seeded_receipt_id,
        }
        expected_realtime_payload = {
            "id": 1,
            "message": expected_message,
            "is_read": False,
            "created_at": ANY,
            "level": "SUCCESS",
            "category": "TRADE",
            "dedupe_key": dedupe_key,
            "extra_payload": expected_nested_extra_payload,
            "trade_id": 501,
            "trade_number": 10025,
            "offer_id": 7,
            "offer_home_server": "iran",
            "recipient_user_id": 5,
            "recipient_role": "responder",
            "principal_user_id": 5,
            "side": "responder",
            "delivery_receipt_id": seeded_receipt_id,
        }

        # 3. Setup hermetic boundaries for external Redis, publish_user_event, and Web Push
        fake_redis_client = AsyncMock()
        fake_redis_cm = MagicMock()
        fake_redis_cm.__aenter__ = AsyncMock(return_value=fake_redis_client)
        fake_redis_cm.__aexit__ = AsyncMock(return_value=None)
        fake_redis_cls = MagicMock(return_value=fake_redis_cm)

        mock_publish_user_event = AsyncMock()
        mock_schedule_web_push = MagicMock()
        mock_should_suppress = AsyncMock(return_value=False)

        # 4. Deliver claimed receipt exercising real publication dispatch
        with (
            patch(
                "core.services.trade_webapp_delivery_service.should_suppress_user_notification",
                mock_should_suppress,
            ),
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
            async with real_async_session(self.engine) as session_deliver:
                _configure_no_expire_on_commit(session_deliver)
                loaded_receipt = await session_deliver.get(TradeDeliveryReceipt, seeded_receipt_id)
                self.assertIsNotNone(loaded_receipt)

                delivery_result = await deliver_claimed_webapp_receipt(
                    session_deliver,
                    receipt=loaded_receipt,
                    current_server=destination_server,
                    commit=True,
                    publish_after_commit=True,
                    now=fixed_now,
                )

        # 5. Assert returned WebAppTradeDeliveryResult first-delivery flags and status
        self.assertIsInstance(delivery_result, WebAppTradeDeliveryResult)
        self.assertEqual(delivery_result.status, WEBAPP_DELIVERY_STATUS_SENT)
        self.assertEqual(delivery_result.trade_number, trade_number)
        self.assertEqual(delivery_result.recipient_user_id, recipient_user_id)
        self.assertEqual(delivery_result.current_server, destination_server)
        self.assertEqual(delivery_result.destination_server, WEBAPP_DESTINATION_SERVER)
        self.assertTrue(delivery_result.notification_created)
        self.assertTrue(delivery_result.receipt_changed)
        self.assertTrue(delivery_result.sent_changed)
        self.assertTrue(delivery_result.realtime_published)
        self.assertIsNone(delivery_result.publish_error_class)
        self.assertIsNone(delivery_result.reason)

        # 6. Assert unread count increment exactly once for user:5:unread_count
        fake_redis_client.incr.assert_awaited_once_with("user:5:unread_count")
        self.assertEqual(fake_redis_client.incr.await_count, 1)

        # 7. Assert publish_user_event awaited exactly once with positional arguments
        mock_publish_user_event.assert_awaited_once_with(5, "message", expected_realtime_payload)
        self.assertEqual(mock_publish_user_event.await_count, 1)
        call_args = mock_publish_user_event.await_args
        self.assertEqual(call_args.args[0], 5)
        self.assertEqual(call_args.args[1], "message")
        actual_payload = call_args.args[2]
        self.assertIsInstance(actual_payload["created_at"], str)
        self.assertTrue(len(actual_payload["created_at"]) >= 19)

        # 8. Assert Web Push scheduling call exactly once with notification ID and nested extra payload
        mock_schedule_web_push.assert_called_once_with(1, expected_nested_extra_payload)
        self.assertEqual(mock_schedule_web_push.call_count, 1)
        self.assertEqual(mock_schedule_web_push.call_args.args, (1, expected_nested_extra_payload))

        # 9. Independent post-delivery verification session
        async with real_async_session(self.engine) as verify_session:
            _configure_no_expire_on_commit(verify_session)

            stmt_rcpt = select(TradeDeliveryReceipt)
            res_rcpt = await verify_session.execute(stmt_rcpt)
            receipts = list(res_rcpt.scalars().all())
            self.assertEqual(len(receipts), 1)
            verify_rcpt = receipts[0]

            self.assertEqual(verify_rcpt.id, seeded_receipt_id)
            self.assertEqual(verify_rcpt.event_type, TRADE_COMPLETED_EVENT_TYPE)
            self.assertEqual(verify_rcpt.trade_id, trade_id)
            self.assertEqual(verify_rcpt.trade_number, trade_number)
            self.assertEqual(verify_rcpt.offer_id, offer_id)
            self.assertEqual(verify_rcpt.recipient_user_id, recipient_user_id)
            self.assertEqual(verify_rcpt.recipient_role, recipient_role)
            self.assertEqual(verify_rcpt.channel, TradeDeliveryChannel.WEBAPP)
            self.assertEqual(verify_rcpt.destination_server, destination_server)
            self.assertEqual(verify_rcpt.status, TradeDeliveryReceiptStatus.SENT)
            self.assertEqual(verify_rcpt.dedupe_key, dedupe_key)
            self.assertEqual(verify_rcpt.reason, reason)
            self.assertEqual(verify_rcpt.attempt_count, 1)
            self.assertIsNone(verify_rcpt.worker_id)
            self.assertIsNone(verify_rcpt.lease_until)
            self.assertEqual(verify_rcpt.notification_id, 1)
            self._assert_utc_dt_equal(verify_rcpt.sent_at, fixed_now)
            self._assert_utc_dt_equal(verify_rcpt.terminal_at, fixed_now)
            self.assertEqual(verify_rcpt.audit_payload, audit_payload)

            stmt_notif = select(Notification)
            res_notif = await verify_session.execute(stmt_notif)
            notifications = list(res_notif.scalars().all())
            self.assertEqual(len(notifications), 1)
            verify_notif = notifications[0]

            self.assertEqual(verify_notif.id, verify_rcpt.notification_id)
            self.assertEqual(verify_notif.user_id, recipient_user_id)
            self.assertEqual(verify_notif.message, expected_message)
            self.assertFalse(verify_notif.is_read)
            self.assertEqual(verify_notif.category, NotificationCategory.TRADE)
            self.assertEqual(verify_notif.level, NotificationLevel.SUCCESS)
            self.assertEqual(verify_notif.dedupe_key, dedupe_key)
            self.assertEqual(verify_notif.extra_payload, expected_nested_extra_payload)
            self.assertIsNotNone(verify_notif.created_at)
            self.assertEqual(actual_payload["created_at"], verify_notif.created_at.isoformat())


if __name__ == "__main__":
    unittest.main()
