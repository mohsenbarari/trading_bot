"""P1-00b characterization unit for durable trade delivery intent persistence.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: origin_internal_webapp_notification_intent_persistence_four_receipts

Explicit exclusions:
- no trade execution
- no WebApp realtime/Web Push publication
- no Telegram send
- no delivery worker
- no retry
- no SUCCESS/POLICY-SNAPSHOT/SIDE-EFFECT-RETRY closure
- no whole notification-cell closure
- no seed acceptance
- no scenario-record edits
- no reconciliation
- no blocker closure
- no Stage closure

Hermetic and deterministic under APP_ENV_FILE=config/unit-test.env.example.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from core.config import settings
from core.enums import SettlementType, UserAccountStatus, UserRole
from core.services.trade_delivery_receipt_service import (
    TRADE_COMPLETED_EVENT_TYPE,
    persist_trade_completion_delivery_intents,
    trade_completed_receipt_dedupe_key,
)
from core.services.trade_notification_audience_service import (
    TELEGRAM_CHANNEL,
    WEBAPP_CHANNEL,
)
from models.accountant_relation import AccountantRelation
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import Offer, OfferStatus, OfferType
from models.offer_request import (
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.trade import Trade, TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.user import User
from tests.test_p1_00b_characterization_origin_interactions import _sqlite_requests
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class P100bInternalOfferWebappNotificationIntentPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Characterize durable delivery intent persistence for internal webapp trades."""

    async def test_real_audience_persists_four_durable_intents_without_delivery(self) -> None:
        """Prove real audience persists exactly 4 durable delivery receipts without delivery side effects."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_intents.db"
            engine, Session = _sqlite_requests(str(db_path))
            original_customer_relation_indexes = set(CustomerRelation.__table__.indexes)
            try:
                seen_names: set[str] = set()
                deduped_indexes: set[Any] = set()
                for idx in sorted(original_customer_relation_indexes, key=lambda x: (x.name or "", id(x))):
                    if idx.name not in seen_names:
                        seen_names.add(idx.name)
                        deduped_indexes.add(idx)
                CustomerRelation.__table__.indexes = deduped_indexes
                CustomerRelation.__table__.create(engine, checkfirst=True)
            finally:
                CustomerRelation.__table__.indexes = set(original_customer_relation_indexes)

            self.assertEqual(CustomerRelation.__table__.indexes, original_customer_relation_indexes)
            self.assertEqual(
                {id(idx) for idx in CustomerRelation.__table__.indexes},
                {id(idx) for idx in original_customer_relation_indexes},
            )
            self.assertEqual(
                {idx.name for idx in CustomerRelation.__table__.indexes},
                {idx.name for idx in original_customer_relation_indexes},
            )

            AccountantRelation.__table__.create(engine, checkfirst=True)
            TradeDeliveryReceipt.__table__.create(engine, checkfirst=True)

            fixed_event_created_at = datetime(2026, 9, 16, 10, 30, 0, tzinfo=timezone.utc)
            seeded_sent_at = datetime(2026, 9, 16, 10, 31, 0, tzinfo=timezone.utc)
            seeded_terminal_at = datetime(2026, 9, 16, 10, 31, 0, tzinfo=timezone.utc)
            fixed_aware_utc = datetime(2026, 9, 16, 11, 0, 0, tzinfo=timezone.utc)

            try:
                with Session() as sync_session:
                    commodity = Commodity(id=1, name="Gold")
                    owner_user = User(
                        id=9,
                        account_name="owner_seller",
                        mobile_number="09129999999",
                        full_name="Owner Seller",
                        address="tehran",
                        telegram_id=999,
                        account_status=UserAccountStatus.ACTIVE,
                        role=UserRole.STANDARD,
                        home_server="iran",
                        sync_version=1,
                    )
                    customer_user = User(
                        id=5,
                        account_name="customer_buyer",
                        mobile_number="09125555555",
                        full_name="Customer Buyer",
                        address="tehran",
                        telegram_id=555,
                        account_status=UserAccountStatus.ACTIVE,
                        role=UserRole.STANDARD,
                        home_server="iran",
                        sync_version=1,
                    )
                    offer = Offer(
                        id=7,
                        status=OfferStatus.ACTIVE,
                        user_id=9,
                        quantity=4,
                        remaining_quantity=4,
                        is_wholesale=True,
                        lot_sizes=None,
                        offer_type=OfferType.SELL,
                        settlement_type=SettlementType.CASH,
                        price=123456,
                        offer_public_id="ofr_internal_7",
                        home_server="iran",
                        commodity_id=1,
                        created_at=datetime(2026, 9, 16, 9, 0, 0, tzinfo=timezone.utc),
                        overtime_minutes_snapshot=0,
                        version_id=1,
                    )
                    customer_relation = CustomerRelation(
                        id=44,
                        owner_user_id=9,
                        customer_user_id=5,
                        created_by_user_id=9,
                        invitation_token="inv_customer_44",
                        management_name="management desk",
                        customer_tier=CustomerTier.TIER_2,
                        status=CustomerRelationStatus.ACTIVE,
                        sync_version=1,
                    )
                    trade = Trade(
                        id=501,
                        trade_number=10025,
                        offer_id=7,
                        offer_user_id=9,
                        offer_user_mobile="09129999999",
                        responder_user_id=5,
                        responder_user_mobile="09125555555",
                        actor_user_id=5,
                        commodity_id=1,
                        trade_type=TradeType.BUY,
                        settlement_type=SettlementType.CASH,
                        quantity=4,
                        price=123456,
                        status=TradeStatus.COMPLETED,
                        created_at=fixed_event_created_at,
                        completed_at=fixed_event_created_at,
                        version_id=1,
                    )
                    offer_request = OfferRequest(
                        id=601,
                        request_public_id="req_pub_601",
                        request_home_server="iran",
                        local_offer_id=7,
                        offer_public_id="ofr_internal_7",
                        requester_user_id=5,
                        actor_user_id=5,
                        request_source_surface=OfferRequestSourceSurface.WEBAPP,
                        request_source_server="iran",
                        requested_quantity=4,
                        idempotency_key="idemp_req_601",
                        workflow_kind=OfferRequestWorkflow.DIRECT,
                        offer_owner_user_id=9,
                        result_status=OfferRequestStatus.COMPLETED_TRADE,
                        resulting_trade_id=501,
                        customer_relation_id=44,
                        customer_owner_user_id=9,
                        customer_tier_snapshot=CustomerTier.TIER_2.value,
                        customer_management_name_snapshot="management desk",
                        archived=False,
                        received_at=datetime(2026, 9, 16, 10, 29, 0, tzinfo=timezone.utc),
                        decided_at=fixed_event_created_at,
                        version_id=1,
                    )
                    owner_webapp_dedupe_key = trade_completed_receipt_dedupe_key(
                        channel=TradeDeliveryChannel.WEBAPP,
                        trade_number=10025,
                        recipient_user_id=9,
                    )
                    pre_seeded_receipt = TradeDeliveryReceipt(
                        id=9001,
                        event_type=TRADE_COMPLETED_EVENT_TYPE,
                        dedupe_key=owner_webapp_dedupe_key,
                        trade_id=501,
                        trade_number=10025,
                        offer_id=7,
                        recipient_user_id=9,
                        recipient_role="offer_owner",
                        channel=TradeDeliveryChannel.WEBAPP,
                        destination_server="iran",
                        status=TradeDeliveryReceiptStatus.SENT,
                        reason=None,
                        audit_payload={
                            "message": "پیشنویس وباپ مالک",
                            "extra_payload": {
                                "trade_id": 501,
                                "trade_number": 10025,
                                "offer_id": 7,
                                "offer_home_server": "iran",
                                "recipient_user_id": 9,
                                "recipient_role": "offer_owner",
                                "principal_user_id": 9,
                                "side": "offer_owner",
                            },
                        },
                        worker_id=None,
                        lease_until=None,
                        attempt_count=1,
                        event_created_at=fixed_event_created_at,
                        sent_at=seeded_sent_at,
                        terminal_at=seeded_terminal_at,
                        created_at=fixed_event_created_at,
                        updated_at=seeded_sent_at,
                    )
                    sync_session.add_all([
                        commodity,
                        owner_user,
                        customer_user,
                        offer,
                        customer_relation,
                        trade,
                        offer_request,
                        pre_seeded_receipt,
                    ])
                    sync_session.commit()

                async with real_async_session(engine) as session:
                    stmt_offer = select(Offer).where(Offer.id == 7)
                    reloaded_offer = (await session.execute(stmt_offer)).scalar_one_or_none()
                    self.assertIsNotNone(reloaded_offer)
                    self.assertEqual(reloaded_offer.offer_public_id, "ofr_internal_7")

                    stmt_trade = (
                        select(Trade)
                        .options(
                            joinedload(Trade.offer),
                            joinedload(Trade.commodity),
                            joinedload(Trade.offer_user),
                            joinedload(Trade.responder_user),
                        )
                        .where(Trade.id == 501)
                    )
                    reloaded_trade = (await session.execute(stmt_trade)).scalar_one_or_none()
                    self.assertIsNotNone(reloaded_trade)

                    stmt_req = select(OfferRequest).where(OfferRequest.id == 601)
                    reloaded_request = (await session.execute(stmt_req)).scalar_one_or_none()
                    self.assertIsNotNone(reloaded_request)
                    self.assertEqual(reloaded_request.offer_public_id, "ofr_internal_7")

                    with patch.object(settings, "bot_username", "test_bot"):
                        intent_batch = await persist_trade_completion_delivery_intents(
                            session,
                            reloaded_trade,
                            now=fixed_aware_utc,
                        )

                    audience = intent_batch.audience
                    self.assertEqual(audience.event_type, TRADE_COMPLETED_EVENT_TYPE)
                    self.assertEqual(audience.trade_id, 501)
                    self.assertEqual(audience.trade_number, 10025)
                    self.assertEqual(audience.offer_id, 7)
                    self.assertEqual(audience.offer_home_server, "iran")
                    self.assertEqual(audience.trade_path_kind, "owner_customer_tier2")
                    self.assertEqual(audience.trade_path_summary, "مالک ↔ مشتری سطح ۲")
                    self.assertIsNone(audience.skipped_reason)

                    self.assertEqual(len(audience.recipients), 2)
                    self.assertEqual([r.recipient_user_id for r in audience.recipients], [5, 9])
                    self.assertEqual([r.recipient_role for r in audience.recipients], ["responder", "offer_owner"])

                    self.assertEqual(len(intent_batch.receipts), 4)
                    batch_identities = {
                        (
                            r.recipient_user_id,
                            r.channel.value if hasattr(r.channel, "value") else str(r.channel),
                        )
                        for r in intent_batch.receipts
                    }
                    self.assertEqual(
                        batch_identities,
                        {
                            (5, "webapp"),
                            (5, "telegram"),
                            (9, "webapp"),
                            (9, "telegram"),
                        },
                    )
                    await session.commit()

                async with real_async_session(engine) as session:
                    stmt_receipts = select(TradeDeliveryReceipt).order_by(
                        TradeDeliveryReceipt.recipient_user_id.asc(),
                        TradeDeliveryReceipt.channel.asc(),
                    )
                    durable_receipts = list((await session.execute(stmt_receipts)).scalars().all())
                    self.assertEqual(len(durable_receipts), 4)

                    receipt_map = {
                        (
                            r.recipient_user_id,
                            r.channel.value if hasattr(r.channel, "value") else str(r.channel),
                        ): r
                        for r in durable_receipts
                    }
                    self.assertEqual(len(receipt_map), 4)
                    self.assertEqual(
                        set(receipt_map.keys()),
                        {
                            (5, "webapp"),
                            (5, "telegram"),
                            (9, "webapp"),
                            (9, "telegram"),
                        },
                    )

                    for (user_id, ch), r in receipt_map.items():
                        self.assertEqual(r.event_type, TRADE_COMPLETED_EVENT_TYPE)
                        self.assertEqual(r.trade_id, 501)
                        self.assertEqual(r.trade_number, 10025)
                        self.assertEqual(r.offer_id, 7)
                        expected_dedupe = trade_completed_receipt_dedupe_key(
                            channel=ch,
                            trade_number=10025,
                            recipient_user_id=user_id,
                        )
                        self.assertEqual(r.dedupe_key, expected_dedupe)
                        expected_role = "responder" if user_id == 5 else "offer_owner"
                        self.assertEqual(r.recipient_role, expected_role)
                        self.assertIsNotNone(r.audit_payload)
                        extra = r.audit_payload.get("extra_payload") or {}
                        self.assertEqual(extra.get("offer_home_server"), "iran")
                        self.assertIsNone(r.notification_id)
                        self.assertIsNone(r.telegram_message_id)

                    r5_webapp = receipt_map[(5, "webapp")]
                    status_5_webapp = (
                        r5_webapp.status.value if hasattr(r5_webapp.status, "value") else str(r5_webapp.status)
                    )
                    self.assertEqual(status_5_webapp, "pending")
                    self.assertEqual(r5_webapp.destination_server, "iran")
                    self.assertEqual(r5_webapp.reason, "webapp_required")
                    self.assertIsNone(r5_webapp.terminal_at)

                    r5_tg = receipt_map[(5, "telegram")]
                    status_5_tg = r5_tg.status.value if hasattr(r5_tg.status, "value") else str(r5_tg.status)
                    self.assertEqual(status_5_tg, "not_required")
                    self.assertEqual(r5_tg.destination_server, "foreign")
                    self.assertEqual(r5_tg.reason, "customer_tier2")
                    self.assertIsNotNone(r5_tg.terminal_at)
                    self.assertEqual(_as_utc(r5_tg.terminal_at), fixed_aware_utc)

                    r9_webapp = receipt_map[(9, "webapp")]
                    status_9_webapp = (
                        r9_webapp.status.value if hasattr(r9_webapp.status, "value") else str(r9_webapp.status)
                    )
                    self.assertEqual(status_9_webapp, "sent")
                    self.assertEqual(r9_webapp.destination_server, "iran")
                    self.assertIsNotNone(r9_webapp.sent_at)
                    self.assertEqual(_as_utc(r9_webapp.sent_at), seeded_sent_at)
                    self.assertIsNotNone(r9_webapp.terminal_at)
                    self.assertEqual(_as_utc(r9_webapp.terminal_at), seeded_terminal_at)
                    self.assertIsNone(r9_webapp.worker_id)
                    self.assertIsNone(r9_webapp.lease_until)

                    r9_tg = receipt_map[(9, "telegram")]
                    status_9_tg = r9_tg.status.value if hasattr(r9_tg.status, "value") else str(r9_tg.status)
                    self.assertEqual(status_9_tg, "pending")
                    self.assertEqual(r9_tg.destination_server, "foreign")
                    self.assertEqual(r9_tg.reason, "telegram_required")
                    self.assertIsNone(r9_tg.terminal_at)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
