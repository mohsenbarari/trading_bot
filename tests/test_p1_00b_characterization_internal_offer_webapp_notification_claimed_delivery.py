"""P1-00b characterization: claimed internal offer WebApp notification receipt delivery.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Canonical scenario: docs/refactor/two-server/section-1/inventory/scenario-records.json#ORIGIN-INTERNAL__WEBAPP__NOTIFICATION
Scenario key: origin_internal_webapp_notification_claimed_receipt_first_delivery_provenance

Characterizes first successful delivery of an already-claimed PROCESSING WebApp
trade-delivery receipt with exact accepted ORIGIN-INTERNAL__WEBAPP responder provenance.
Exercises deliver_claimed_webapp_receipt with commit=True and publish_after_commit=True,
observing at the publication boundary that Notification and receipt are already durably committed.

Explicit exclusions:
- No trade execution or audience construction.
- No intent creation or endpoint SUCCESS assertions.
- No combined POLICY-SNAPSHOT, SIDE-EFFECT-RETRY, or retry/re-entry testing.
- No Telegram delivery, SSE, or Web Push payload serialization.
- No seed acceptance, scenario-record edits, reconciliation, blocker closure, or Stage closure claims.
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from datetime import datetime, timedelta, timezone
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

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


class P100bInternalOfferWebappNotificationClaimedDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization of claimed internal WebApp receipt delivery."""

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

    async def test_claimed_internal_receipt_commits_exact_provenance_before_publication(self) -> None:
        """Prove claimed PROCESSING receipt commits Notification and SENT status before publication."""
        event_time = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
        lease_until = datetime(2026, 9, 16, 12, 0, 30, tzinfo=timezone.utc)
        fixed_now = datetime(2026, 9, 16, 12, 0, 5, tzinfo=timezone.utc)

        trade_number = 10025
        recipient_user_id = 5
        recipient_role = "responder"
        destination_server = "iran"
        reason = "webapp_required"
        worker_id = "test-worker-p1-00b-iran"
        attempt_count = 1

        expected_message = "معامله #10025 با موفقیت تکمیل شد."
        audit_extra_payload = {
            "trade_id": 501,
            "trade_number": trade_number,
            "offer_id": 7,
            "offer_home_server": "iran",
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
                trade_id=501,
                trade_number=trade_number,
                offer_id=7,
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
            self.assertIsNotNone(seeded_receipt_id)

        # 2. Publication observer checking durable persistence before recording publication
        observed_publications: list[dict[str, Any]] = []

        async def publication_observer(
            *,
            user_id: int,
            notification: Notification | Any,
            extra_payload: Any = None,
        ) -> None:
            async with real_async_session(self.engine) as observer_session:
                _configure_no_expire_on_commit(observer_session)
                stmt_notif = select(Notification)
                res_notif = await observer_session.execute(stmt_notif)
                notifications = list(res_notif.scalars().all())
                self.assertEqual(len(notifications), 1)
                durable_notification = notifications[0]

                stmt_rcpt = select(TradeDeliveryReceipt)
                res_rcpt = await observer_session.execute(stmt_rcpt)
                receipts = list(res_rcpt.scalars().all())
                self.assertEqual(len(receipts), 1)
                durable_receipt = receipts[0]

                # Receipt must already be SENT with timestamps and cleared lease
                self.assertEqual(durable_receipt.status, TradeDeliveryReceiptStatus.SENT)
                self.assertEqual(durable_receipt.notification_id, durable_notification.id)
                self._assert_utc_dt_equal(durable_receipt.sent_at, fixed_now)
                self._assert_utc_dt_equal(durable_receipt.terminal_at, fixed_now)
                self.assertIsNone(durable_receipt.worker_id)
                self.assertIsNone(durable_receipt.lease_until)

                # Notification exact properties and nested provenance
                self.assertEqual(durable_notification.user_id, recipient_user_id)
                self.assertEqual(durable_notification.category, NotificationCategory.TRADE)
                self.assertEqual(durable_notification.level, NotificationLevel.SUCCESS)
                self.assertEqual(durable_notification.dedupe_key, dedupe_key)
                self.assertEqual(durable_notification.message, expected_message)
                self.assertFalse(durable_notification.is_read)

                expected_notif_payload = dict(audit_extra_payload)
                expected_notif_payload["delivery_receipt_id"] = durable_receipt.id
                self.assertEqual(durable_notification.extra_payload, expected_notif_payload)

            observed_publications.append(
                {
                    "user_id": user_id,
                    "notification": notification,
                    "extra_payload": extra_payload,
                }
            )

        # 3. Deliver claimed receipt with observer patch
        with patch(
            "core.services.trade_webapp_delivery_service.should_suppress_user_notification",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
            new=AsyncMock(side_effect=publication_observer),
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

        # 4. Assert returned WebAppTradeDeliveryResult first-delivery flags and status
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

        # 5. Independent post-call verification session
        async with real_async_session(self.engine) as verify_session:
            _configure_no_expire_on_commit(verify_session)
            verify_rcpt = await verify_session.get(TradeDeliveryReceipt, seeded_receipt_id)
            self.assertIsNotNone(verify_rcpt)
            self.assertEqual(verify_rcpt.event_type, TRADE_COMPLETED_EVENT_TYPE)
            self.assertEqual(verify_rcpt.trade_id, 501)
            self.assertEqual(verify_rcpt.trade_number, trade_number)
            self.assertEqual(verify_rcpt.offer_id, 7)
            self.assertEqual(verify_rcpt.recipient_user_id, recipient_user_id)
            self.assertEqual(verify_rcpt.recipient_role, recipient_role)
            self.assertEqual(verify_rcpt.channel, TradeDeliveryChannel.WEBAPP)
            self.assertEqual(verify_rcpt.destination_server, destination_server)
            self.assertEqual(verify_rcpt.status, TradeDeliveryReceiptStatus.SENT)
            self.assertEqual(verify_rcpt.dedupe_key, dedupe_key)
            self.assertEqual(verify_rcpt.reason, reason)
            self.assertIsNotNone(verify_rcpt.notification_id)
            self._assert_utc_dt_equal(verify_rcpt.sent_at, fixed_now)
            self._assert_utc_dt_equal(verify_rcpt.terminal_at, fixed_now)
            self.assertIsNone(verify_rcpt.worker_id)
            self.assertIsNone(verify_rcpt.lease_until)
            self.assertEqual(verify_rcpt.attempt_count, 1)

            verify_notif = await verify_session.get(Notification, verify_rcpt.notification_id)
            self.assertIsNotNone(verify_notif)
            self.assertEqual(verify_notif.user_id, recipient_user_id)
            self.assertEqual(verify_notif.category, NotificationCategory.TRADE)
            self.assertEqual(verify_notif.level, NotificationLevel.SUCCESS)
            self.assertEqual(verify_notif.dedupe_key, dedupe_key)
            self.assertEqual(verify_notif.message, expected_message)
            self.assertFalse(verify_notif.is_read)

            expected_final_extra_payload = dict(audit_extra_payload)
            expected_final_extra_payload["delivery_receipt_id"] = verify_rcpt.id
            self.assertEqual(verify_notif.extra_payload, expected_final_extra_payload)

        # 6. Verify exactly one publication was observed with matching payload
        self.assertEqual(len(observed_publications), 1)
        publication_event = observed_publications[0]
        self.assertEqual(publication_event["user_id"], recipient_user_id)
        self.assertEqual(publication_event["notification"].id, verify_rcpt.notification_id)
        self.assertIs(publication_event["notification"], delivery_result.notification)
        self.assertEqual(publication_event["extra_payload"], expected_final_extra_payload)


if __name__ == "__main__":
    unittest.main()
