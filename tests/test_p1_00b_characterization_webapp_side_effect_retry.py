"""P1-00b characterization: WebApp side-effect retry outcome.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: docs/refactor/two-server/section-1/inventory/scenario-records.json#ORIGIN-INTERNAL__WEBAPP__SIDE-EFFECT-RETRY

Explicit exclusions:
- trade execution, origin SUCCESS, audience construction, policy/TIER_2, Telegram,
  seed acceptance, whole-cell or blocker disposition changes, reconciliation, and Stage closure.
"""
from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from core.enums import NotificationCategory, NotificationLevel
from core.services.trade_webapp_delivery_service import (
    WEBAPP_DELIVERY_STATUS_SENT,
    WEBAPP_DESTINATION_SERVER,
    deliver_claimed_webapp_receipt,
)
from models.notification import Notification
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


class WebAppSideEffectRetryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize retry of a previously failed claimed WebApp delivery receipt."""

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

    async def test_failed_claimed_receipt_retries_once_after_commit(self) -> None:
        """Prove a PROCESSING receipt after failure retries, persists, publishes once, and deduplicates re-entry."""
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        trade_number = 4242
        recipient_user_id = 9001
        dedupe_key = f"trade_completed:webapp:{trade_number}:{recipient_user_id}"

        # 1. Seed durable PROCESSING receipt with prior failure audit in real session
        async with real_async_session(self.engine) as init_session:
            receipt = TradeDeliveryReceipt(
                event_type="trade_completed",
                dedupe_key=dedupe_key,
                trade_number=trade_number,
                recipient_user_id=recipient_user_id,
                recipient_role="buyer",
                channel=TradeDeliveryChannel.WEBAPP,
                destination_server=WEBAPP_DESTINATION_SERVER,
                status=TradeDeliveryReceiptStatus.PROCESSING,
                worker_id="webapp-trade-delivery",
                lease_until=now + timedelta(seconds=60),
                attempt_count=2,
                next_retry_at=now,
                last_error="Temporary network drop during prior delivery attempt",
                last_error_class="TransportError",
                reason="retry_prior_failure",
                audit_payload={
                    "message": "معامله ۴۲۴۲ انجام شد.",
                    "extra_payload": {"trade_number": trade_number, "side": "buy"},
                },
                event_created_at=now - timedelta(seconds=15),
            )
            init_session.add(receipt)
            await init_session.commit()
            receipt_id = receipt.id

        publication_calls: list[dict] = []

        async def fake_publish_observer(
            *,
            user_id: int,
            notification: Notification,
            extra_payload: dict | None = None,
            **kwargs,
        ) -> None:
            # Independent real session verifies both rows are durably committed BEFORE recording publish call
            async with real_async_session(self.engine) as observer_session:
                stmt_r = select(TradeDeliveryReceipt).where(TradeDeliveryReceipt.id == receipt_id)
                res_r = await observer_session.execute(stmt_r)
                persisted_r = res_r.scalar_one_or_none()
                self.assertIsNotNone(persisted_r)
                self.assertEqual(persisted_r.status, TradeDeliveryReceiptStatus.SENT)
                self.assertIsNotNone(persisted_r.notification_id)
                self.assertIsNone(persisted_r.worker_id)
                self.assertIsNone(persisted_r.lease_until)
                self.assertIsNotNone(persisted_r.sent_at)
                self.assertIsNotNone(persisted_r.terminal_at)

                stmt_n = select(Notification).where(Notification.id == persisted_r.notification_id)
                res_n = await observer_session.execute(stmt_n)
                persisted_n = res_n.scalar_one_or_none()
                self.assertIsNotNone(persisted_n)
                self.assertEqual(persisted_n.user_id, recipient_user_id)
                self.assertEqual(persisted_n.dedupe_key, dedupe_key)
                self.assertEqual(persisted_n.level, NotificationLevel.SUCCESS)
                self.assertEqual(persisted_n.category, NotificationCategory.TRADE)

                count_stmt = select(func.count(Notification.id))
                count_res = await observer_session.execute(count_stmt)
                self.assertEqual(count_res.scalar_one(), 1)

            publication_calls.append({
                "user_id": user_id,
                "notification_id": getattr(notification, "id", None),
                "extra_payload": extra_payload,
            })

        # 2. Invoke real deliver_claimed_webapp_receipt
        with (
            patch(
                "core.services.trade_webapp_delivery_service.should_suppress_user_notification",
                AsyncMock(return_value=False),
            ),
            patch(
                "core.services.trade_webapp_delivery_service.publish_webapp_notification_after_commit",
                side_effect=fake_publish_observer,
            ),
        ):
            async with real_async_session(self.engine) as delivery_session:
                stmt = select(TradeDeliveryReceipt).where(TradeDeliveryReceipt.id == receipt_id)
                res = await delivery_session.execute(stmt)
                receipt_to_deliver = res.scalar_one_or_none()
                self.assertIsNotNone(receipt_to_deliver)

                result1 = await deliver_claimed_webapp_receipt(
                    delivery_session,
                    receipt=receipt_to_deliver,
                    current_server=WEBAPP_DESTINATION_SERVER,
                    commit=True,
                    publish_after_commit=True,
                    now=now,
                )

            # Assert first delivery result
            self.assertEqual(result1.status, WEBAPP_DELIVERY_STATUS_SENT)
            self.assertTrue(result1.sent_changed)
            self.assertTrue(result1.notification_created)
            self.assertTrue(result1.realtime_published)
            self.assertIsNone(result1.publish_error_class)
            self.assertEqual(len(publication_calls), 1)

            # 3. Verify durable facts from another independent session
            async with real_async_session(self.engine) as verify_session:
                stmt_v = select(TradeDeliveryReceipt).where(TradeDeliveryReceipt.id == receipt_id)
                res_v = await verify_session.execute(stmt_v)
                durable_r = res_v.scalar_one_or_none()
                self.assertIsNotNone(durable_r)
                self.assertEqual(durable_r.status, TradeDeliveryReceiptStatus.SENT)
                saved_notification_id = durable_r.notification_id
                saved_sent_at = durable_r.sent_at
                self.assertIsNotNone(saved_notification_id)
                self.assertIsNotNone(saved_sent_at)

                stmt_nv = select(Notification).where(Notification.id == saved_notification_id)
                res_nv = await verify_session.execute(stmt_nv)
                durable_n = res_nv.scalar_one_or_none()
                self.assertIsNotNone(durable_n)
                self.assertEqual(durable_n.id, saved_notification_id)

                cnt_stmt = select(func.count(Notification.id))
                cnt_res = await verify_session.execute(cnt_stmt)
                self.assertEqual(cnt_res.scalar_one(), 1)

            # 4. Re-enter deliver_claimed_webapp_receipt for the same receipt from a further independent session
            async with real_async_session(self.engine) as reentry_session:
                stmt_re = select(TradeDeliveryReceipt).where(TradeDeliveryReceipt.id == receipt_id)
                res_re = await reentry_session.execute(stmt_re)
                reloaded_receipt = res_re.scalar_one_or_none()
                self.assertIsNotNone(reloaded_receipt)

                result2 = await deliver_claimed_webapp_receipt(
                    reentry_session,
                    receipt=reloaded_receipt,
                    current_server=WEBAPP_DESTINATION_SERVER,
                    commit=True,
                    publish_after_commit=True,
                    now=now + timedelta(seconds=10),
                )

                self.assertEqual(result2.status, WEBAPP_DELIVERY_STATUS_SENT)
                self.assertFalse(result2.sent_changed)
                self.assertFalse(result2.notification_created)
                self.assertFalse(result2.realtime_published)
                self.assertEqual(result2.receipt.notification_id, saved_notification_id)
                self.assertEqual(result2.receipt.sent_at, saved_sent_at)

            # Assert no second publication call occurred and notification count remains exactly one
            self.assertEqual(len(publication_calls), 1)

            async with real_async_session(self.engine) as final_session:
                cnt_stmt = select(func.count(Notification.id))
                cnt_res = await final_session.execute(cnt_stmt)
                self.assertEqual(cnt_res.scalar_one(), 1)

                stmt_final = select(TradeDeliveryReceipt).where(TradeDeliveryReceipt.id == receipt_id)
                res_final = await final_session.execute(stmt_final)
                final_receipt = res_final.scalar_one_or_none()
                self.assertIsNotNone(final_receipt)
                self.assertEqual(final_receipt.notification_id, saved_notification_id)
                self.assertEqual(final_receipt.sent_at, saved_sent_at)


if __name__ == "__main__":
    unittest.main()
