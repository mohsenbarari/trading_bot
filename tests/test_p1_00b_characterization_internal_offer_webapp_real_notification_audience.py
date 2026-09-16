"""P1-00b characterization unit for real trade notification audience derivation.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: origin_internal_webapp_real_tier2_notification_audience

Explicit exclusions:
- no create_trade or authoritative trade execution
- no WebApp publish
- no Telegram send
- no delivery receipt creation/retry
- no SUCCESS or POLICY-SNAPSHOT closure
- no seed acceptance
- no scenario-record mutation
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
from core.services.trade_notification_audience_service import (
    TRADE_COMPLETED_EVENT_TYPE,
    WEBAPP_CHANNEL,
    build_trade_completion_notification_audience,
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
from models.user import User
from tests.test_p1_00b_characterization_origin_interactions import _sqlite_requests
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


class P100bInternalOfferWebappRealNotificationAudienceCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize real trade notification audience derivation over durable SQLite rows."""

    async def test_real_audience_uses_durable_tier2_internal_webapp_context(self) -> None:
        """Prove real audience derivation preserves tier2 path without audience or bot mocks."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_audience.db"
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
                        created_at=datetime(2026, 9, 16, 10, 30, 0, tzinfo=timezone.utc),
                        completed_at=datetime(2026, 9, 16, 10, 30, 0, tzinfo=timezone.utc),
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
                        decided_at=datetime(2026, 9, 16, 10, 30, 0, tzinfo=timezone.utc),
                        version_id=1,
                    )
                    sync_session.add_all([
                        commodity,
                        owner_user,
                        customer_user,
                        offer,
                        customer_relation,
                        trade,
                        offer_request,
                    ])
                    sync_session.commit()

                async with real_async_session(engine) as session:
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

                    with patch.object(settings, "bot_username", "test_bot"):
                        audience = await build_trade_completion_notification_audience(session, reloaded_trade)

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

                    customer_recipient = audience.recipients[0]
                    self.assertEqual(customer_recipient.principal_user_id, 5)
                    self.assertEqual(customer_recipient.side, "responder")
                    self.assertEqual(customer_recipient.counterparty_user_id, 9)
                    self.assertEqual(customer_recipient.extra_payload["trade_id"], 501)
                    self.assertEqual(customer_recipient.extra_payload["trade_number"], 10025)
                    self.assertEqual(customer_recipient.extra_payload["offer_id"], 7)
                    self.assertEqual(customer_recipient.extra_payload["offer_home_server"], "iran")
                    self.assertEqual(customer_recipient.extra_payload["trade_path_kind"], "owner_customer_tier2")
                    self.assertEqual(
                        customer_recipient.extra_payload["trade_path_summary"],
                        "مالک ↔ مشتری سطح ۲",
                    )

                    customer_channels = {req.channel: req for req in customer_recipient.channel_requirements}
                    self.assertIn(WEBAPP_CHANNEL, customer_channels)
                    self.assertIn("telegram", customer_channels)

                    customer_webapp = customer_channels[WEBAPP_CHANNEL]
                    self.assertTrue(customer_webapp.required)
                    self.assertEqual(customer_webapp.destination_server, "iran")
                    self.assertEqual(customer_webapp.reason, "webapp_required")
                    self.assertEqual(customer_webapp.message, customer_recipient.webapp_message)
                    self.assertIn("10025", customer_webapp.message)

                    customer_tg = customer_channels["telegram"]
                    self.assertFalse(customer_tg.required)
                    self.assertEqual(customer_tg.destination_server, "foreign")
                    self.assertEqual(customer_tg.reason, "customer_tier2")
                    self.assertEqual(customer_tg.telegram_id, 555)
                    self.assertIsNone(customer_tg.message)

                    owner_recipient = audience.recipients[1]
                    self.assertEqual(owner_recipient.principal_user_id, 9)
                    self.assertEqual(owner_recipient.side, "offer_owner")
                    self.assertEqual(owner_recipient.counterparty_user_id, 5)
                    self.assertEqual(owner_recipient.extra_payload["trade_id"], 501)
                    self.assertEqual(owner_recipient.extra_payload["trade_number"], 10025)
                    self.assertEqual(owner_recipient.extra_payload["offer_id"], 7)
                    self.assertEqual(owner_recipient.extra_payload["offer_home_server"], "iran")
                    self.assertEqual(owner_recipient.extra_payload["trade_path_kind"], "owner_customer_tier2")
                    self.assertEqual(
                        owner_recipient.extra_payload["trade_path_summary"],
                        "مالک ↔ مشتری سطح ۲",
                    )

                    owner_channels = {req.channel: req for req in owner_recipient.channel_requirements}
                    self.assertIn(WEBAPP_CHANNEL, owner_channels)
                    self.assertIn("telegram", owner_channels)

                    owner_webapp = owner_channels[WEBAPP_CHANNEL]
                    self.assertTrue(owner_webapp.required)
                    self.assertEqual(owner_webapp.destination_server, "iran")
                    self.assertEqual(owner_webapp.reason, "webapp_required")
                    self.assertEqual(owner_webapp.message, owner_recipient.webapp_message)
                    self.assertIn("10025", owner_webapp.message)

                    owner_tg = owner_channels["telegram"]
                    self.assertTrue(owner_tg.required)
                    self.assertEqual(owner_tg.destination_server, "foreign")
                    self.assertEqual(owner_tg.reason, "telegram_required")
                    self.assertEqual(owner_tg.telegram_id, 999)
                    self.assertIsNotNone(owner_tg.message)
                    self.assertIn("10025", owner_tg.message)

                    self.assertEqual(reloaded_request.request_source_surface, OfferRequestSourceSurface.WEBAPP)
                    self.assertEqual(reloaded_request.request_source_server, "iran")
                    self.assertEqual(reloaded_request.request_home_server, "iran")
                    self.assertEqual(reloaded_request.result_status, OfferRequestStatus.COMPLETED_TRADE)
                    self.assertEqual(reloaded_request.workflow_kind, OfferRequestWorkflow.DIRECT)
                    self.assertEqual(reloaded_request.resulting_trade_id, 501)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
