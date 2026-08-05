"""Transaction-safety tests for the P3 project market outbox."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import unittest

from core.enums import SettlementType
from models.coin_intelligence_market_outbox import CoinIntelligenceMarketOutbox
from models.offer import Offer, OfferStatus, OfferType
from models.trade import Trade, TradeStatus, TradeType


class ProjectMarketOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        # Avoid Base.metadata.create_all: unrelated product tables contain
        # PostgreSQL-only generated columns.  These are the exact P3 tables.
        Offer.__table__.create(self.engine)
        Trade.__table__.create(self.engine)
        CoinIntelligenceMarketOutbox.__table__.create(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _offer(self) -> Offer:
        return Offer(
            offer_public_id="P3-OUTBOX-TEST",
            home_server="foreign",
            offer_type=OfferType.SELL,
            settlement_type=SettlementType.CASH,
            commodity_id=1,
            quantity=5,
            remaining_quantity=5,
            price=186_900,
            status=OfferStatus.ACTIVE,
        )

    def _events(self) -> list[CoinIntelligenceMarketOutbox]:
        return list(
            self.session.scalars(
                select(CoinIntelligenceMarketOutbox).order_by(
                    CoinIntelligenceMarketOutbox.id
                )
            )
        )

    def test_offer_lifecycle_is_appended_in_its_business_transaction(self) -> None:
        offer = self._offer()
        self.session.add(offer)
        self.session.commit()

        offer.remaining_quantity = 2
        self.session.commit()
        offer.status = OfferStatus.COMPLETED
        offer.remaining_quantity = 0
        self.session.commit()

        events = self._events()
        self.assertEqual(
            [item.event_kind for item in events],
            ["OFFER_OPENED", "OFFER_PARTIAL", "OFFER_COMPLETED"],
        )
        self.assertEqual(events[0].status, "PENDING")
        self.assertEqual(events[1].payload["remaining_quantity"], 2)
        self.assertEqual(events[2].payload["status"], "COMPLETED")
        self.assertEqual(events[0].payload["price_unit"], "PROJECT_THOUSAND_TOMAN")
        self.assertTrue(events[0].model_eligible)

    def test_cancel_expire_and_completed_trade_are_separate_facts(self) -> None:
        cancelled = self._offer()
        cancelled.offer_public_id = "P3-CANCELLED"
        expired = self._offer()
        expired.offer_public_id = "P3-EXPIRED"
        self.session.add_all([cancelled, expired])
        self.session.commit()

        cancelled.status = OfferStatus.CANCELLED
        expired.status = OfferStatus.EXPIRED
        trade = Trade(
            trade_number=10_001,
            offer_id=cancelled.id,
            commodity_id=1,
            trade_type=TradeType.BUY,
            settlement_type=SettlementType.CASH,
            quantity=5,
            price=186_900,
            status=TradeStatus.COMPLETED,
        )
        self.session.add(trade)
        self.session.commit()

        events = self._events()
        kinds = [item.event_kind for item in events]
        self.assertEqual(kinds.count("OFFER_CANCELLED"), 1)
        self.assertEqual(kinds.count("OFFER_EXPIRED"), 1)
        self.assertEqual(kinds.count("TRADE_COMPLETED"), 1)
        trade_event = next(item for item in events if item.event_kind == "TRADE_COMPLETED")
        self.assertEqual(trade_event.payload["side"], "SELL")
        self.assertEqual(trade_event.payload["event_type"], "TRADE")

    def test_rollback_persists_neither_offer_nor_outbox_event(self) -> None:
        self.session.add(self._offer())
        self.session.flush()
        self.session.rollback()

        self.assertEqual(self._events(), [])

    def test_payload_excludes_identity_and_free_text(self) -> None:
        offer = self._offer()
        offer.user_id = 99
        offer.actor_user_id = 100
        offer.notes = "private settlement condition"
        self.session.add(offer)
        self.session.commit()

        payload = self._events()[0].payload
        serialized = repr(payload).lower()
        for forbidden in ("user", "actor", "note", "private", "telegram", "mobile"):
            self.assertNotIn(forbidden, serialized)

    def test_non_competitive_offer_is_retained_but_model_ineligible(self) -> None:
        offer = self._offer()
        offer.exclude_from_competitive_price = True
        self.session.add(offer)
        self.session.commit()

        event = self._events()[0]
        self.assertFalse(event.model_eligible)
        self.assertTrue(event.payload["exclude_from_competitive_price"])


if __name__ == "__main__":
    unittest.main()
