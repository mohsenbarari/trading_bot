from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from core.offer_sync_payload import build_offer_sync_payload
from models.offer import OfferStatus, OfferType


class OfferSyncPayloadTests(unittest.TestCase):
    def test_payload_preserves_competitive_warning_fields(self):
        offer = SimpleNamespace(
            id=7,
            offer_public_id="ofr_warning_7",
            version_id=3,
            user_id=11,
            actor_user_id=12,
            home_server="iran",
            offer_type=OfferType.SELL,
            commodity_id=4,
            quantity=25,
            remaining_quantity=25,
            price=150000,
            exclude_from_competitive_price=True,
            price_warning_type="sell_below_lowest_active",
            is_wholesale=False,
            lot_sizes=[10, 15],
            original_lot_sizes=[10, 15],
            expire_reason=None,
            expired_by_user_id=None,
            expired_by_actor_user_id=None,
            expire_source_surface=None,
            expire_source_server=None,
            notes="warning accepted",
            status=OfferStatus.ACTIVE,
            channel_message_id=9988,
            republished_offer_id=None,
            created_at=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 6, 27, 12, 1, tzinfo=timezone.utc),
            expired_at=None,
            idempotency_key="offer-warning-7",
            archived=False,
        )

        payload = build_offer_sync_payload(offer)

        self.assertTrue(payload["exclude_from_competitive_price"])
        self.assertEqual(payload["price_warning_type"], "sell_below_lowest_active")
        self.assertEqual(payload["offer_public_id"], "ofr_warning_7")
        self.assertEqual(payload["version_id"], 3)

    def test_payload_defaults_missing_competitive_warning_fields(self):
        offer = SimpleNamespace(
            id=8,
            offer_public_id="ofr_plain_8",
            user_id=11,
            actor_user_id=None,
            home_server="foreign",
            offer_type=OfferType.BUY,
            commodity_id=4,
            quantity=1,
            remaining_quantity=1,
            price=100000,
            is_wholesale=True,
            lot_sizes=None,
            original_lot_sizes=None,
            notes=None,
            status=OfferStatus.ACTIVE,
            channel_message_id=None,
            republished_offer_id=None,
            created_at=None,
            updated_at=None,
            idempotency_key=None,
            archived=False,
        )

        payload = build_offer_sync_payload(offer)

        self.assertFalse(payload["exclude_from_competitive_price"])
        self.assertIsNone(payload["price_warning_type"])


if __name__ == "__main__":
    unittest.main()
