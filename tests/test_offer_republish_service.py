from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from core.services.offer_republish_service import (
    OfferNotRepeatableError,
    OfferRepeatWindow,
    REPEATABLE_EXPIRE_REASONS,
    ensure_republish_payload_matches_source,
    list_repeatable_offers,
    load_offer_repeat_window,
    lock_repeatable_offer,
    offer_remaining_lot_sizes,
    offer_remaining_quantity,
    repeatable_offer_conditions,
)
from models.offer import Offer, OfferType


class FakeScalarResult:
    def __init__(self, values):
        self.values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: self.values)


class OfferRepublishPolicyTests(unittest.TestCase):
    def test_republish_provenance_is_unique_per_home_not_globally(self):
        provenance_column = Offer.__table__.c.republished_from_offer_public_id
        self.assertFalse(bool(provenance_column.unique))

        index = next(
            item
            for item in Offer.__table__.indexes
            if item.name == "uq_offers_republished_from_offer_public_id_home_server"
        )
        self.assertTrue(index.unique)
        self.assertEqual(
            [column.name for column in index.columns],
            ["republished_from_offer_public_id", "home_server"],
        )

    def test_expiry_allowlist_excludes_bulk_cancel_and_market_close(self):
        self.assertEqual(set(REPEATABLE_EXPIRE_REASONS), {"time_limit", "manual"})
        self.assertNotIn("cancel_all", REPEATABLE_EXPIRE_REASONS)
        self.assertNotIn("bot_cancel_all", REPEATABLE_EXPIRE_REASONS)
        self.assertNotIn("market_closed", REPEATABLE_EXPIRE_REASONS)

    def test_replacement_home_is_required_and_fail_closed(self):
        window = OfferRepeatWindow(
            opened_at=datetime(2026, 7, 14, 9, 0, 0),
            recent_cutoff=datetime(2026, 7, 14, 10, 0, 0),
        )
        with self.assertRaises(ValueError):
            repeatable_offer_conditions(
                owner_user_id=9,
                window=window,
                replacement_home_server="unknown",
            )

    def test_remaining_quantity_and_current_lots_are_authoritative(self):
        source = SimpleNamespace(
            quantity=20,
            remaining_quantity=8,
            is_wholesale=False,
            lot_sizes=[3, 5],
            original_lot_sizes=[4, 8, 8],
        )

        self.assertEqual(offer_remaining_quantity(source), 8)
        self.assertEqual(offer_remaining_lot_sizes(source), [3, 5])

    def test_zero_remainder_never_falls_back_to_original_quantity(self):
        source = SimpleNamespace(quantity=20, remaining_quantity=0)
        self.assertEqual(offer_remaining_quantity(source), 0)

    def test_republish_payload_must_match_remaining_source_snapshot(self):
        source = SimpleNamespace(
            offer_type=OfferType.SELL,
            settlement_type="tomorrow",
            commodity_id=4,
            quantity=20,
            remaining_quantity=8,
            price=170000,
            is_wholesale=False,
            lot_sizes=[3, 5],
            notes="فقط یکجا تحویل",
        )

        ensure_republish_payload_matches_source(
            source,
            offer_type="sell",
            settlement_type="tomorrow",
            commodity_id=4,
            quantity=8,
            price=170000,
            is_wholesale=False,
            lot_sizes=[3, 5],
            notes="فقط یکجا تحویل",
        )

        with self.assertRaises(OfferNotRepeatableError) as exc_info:
            ensure_republish_payload_matches_source(
                source,
                offer_type="sell",
                settlement_type="tomorrow",
                commodity_id=4,
                quantity=20,
                price=170000,
                is_wholesale=False,
                lot_sizes=[3, 5],
                notes="فقط یکجا تحویل",
            )
        self.assertIn("quantity", exc_info.exception.reason)

    def test_invalid_remaining_lot_snapshot_fails_closed(self):
        source = SimpleNamespace(
            offer_type=OfferType.BUY,
            settlement_type="cash",
            commodity_id=2,
            quantity=10,
            remaining_quantity=8,
            price=100,
            is_wholesale=False,
            lot_sizes=[3, 4],
            notes=None,
        )
        with self.assertRaises(OfferNotRepeatableError) as exc_info:
            ensure_republish_payload_matches_source(
                source,
                offer_type="buy",
                settlement_type="cash",
                commodity_id=2,
                quantity=8,
                price=100,
                is_wholesale=False,
                lot_sizes=[3, 4],
                notes=None,
            )
        self.assertEqual(exc_info.exception.reason, "source_lot_quantity_mismatch")

    def test_missing_remaining_lots_for_retail_source_fails_closed(self):
        source = SimpleNamespace(
            offer_type=OfferType.BUY,
            settlement_type="cash",
            commodity_id=2,
            quantity=10,
            remaining_quantity=8,
            price=100,
            is_wholesale=False,
            lot_sizes=None,
            notes=None,
        )
        with self.assertRaises(OfferNotRepeatableError) as exc_info:
            ensure_republish_payload_matches_source(
                source,
                offer_type="buy",
                settlement_type="cash",
                commodity_id=2,
                quantity=8,
                price=100,
                is_wholesale=False,
                lot_sizes=None,
                notes=None,
            )
        self.assertEqual(exc_info.exception.reason, "source_lot_quantity_mismatch")


class OfferRepublishQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_market_has_no_repeat_window(self):
        db = SimpleNamespace(execute=AsyncMock())
        with patch(
            "core.services.offer_republish_service.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=False)),
        ):
            result = await load_offer_repeat_window(db)

        self.assertIsNone(result)
        db.execute.assert_not_awaited()

    async def test_new_market_open_timestamp_is_the_repeat_boundary(self):
        opened_at = datetime(2026, 7, 14, 9, 0, 0)
        db = SimpleNamespace()
        with patch(
            "core.services.offer_republish_service.get_market_runtime_state",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, last_transition_at=opened_at)),
        ):
            result = await load_offer_repeat_window(db, market_is_open=True)

        self.assertIsNotNone(result)
        self.assertEqual(result.opened_at, opened_at)

    async def test_repeatable_query_is_current_session_leaf_only(self):
        captured = []

        async def execute(stmt):
            captured.append(stmt)
            return FakeScalarResult([])

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        window = OfferRepeatWindow(
            opened_at=datetime(2026, 7, 14, 9, 0, 0),
            recent_cutoff=datetime(2026, 7, 14, 10, 0, 0),
        )
        with patch(
            "core.services.offer_republish_service.load_offer_repeat_window",
            new=AsyncMock(return_value=window),
        ):
            result = await list_repeatable_offers(
                db,
                owner_user_id=9,
                replacement_home_server="foreign",
            )

        self.assertEqual(result, [])
        sql = str(captured[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn("offers.status = 'EXPIRED'", sql)
        self.assertIn("offers.user_id = 9", sql)
        self.assertIn("offers.expire_reason IN ('time_limit', 'manual')", sql)
        self.assertIn("offers.created_at >=", sql)
        self.assertIn("republished_from_offer_public_id = offers.offer_public_id", sql)
        self.assertIn("home_server = 'foreign'", sql)
        self.assertIn("NOT (EXISTS", sql)
        self.assertIn("offers.republished_offer_id IS NULL", sql)
        self.assertIn("offers.archived IS false OR offers.archived IS NULL", sql)

    async def test_republish_lock_rechecks_identity_under_row_lock(self):
        captured = []
        source = SimpleNamespace(id=41)

        async def execute(stmt):
            captured.append(stmt)
            return SimpleNamespace(scalar_one_or_none=lambda: source)

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        window = OfferRepeatWindow(
            opened_at=datetime(2026, 7, 14, 9, 0, 0),
            recent_cutoff=datetime(2026, 7, 14, 10, 0, 0),
        )
        with patch(
            "core.services.offer_republish_service.load_offer_repeat_window",
            new=AsyncMock(return_value=window),
        ):
            with self.assertRaises(OfferNotRepeatableError) as exc_info:
                await lock_repeatable_offer(
                    db,
                    owner_user_id=9,
                    offer_public_id="ofr_source_41",
                    expected_local_id=42,
                    market_is_open=True,
                    replacement_home_server="foreign",
                )

        self.assertEqual(exc_info.exception.reason, "offer_identity_mismatch")
        sql = str(captured[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("offers.offer_public_id = 'ofr_source_41'", sql)
        self.assertIn("home_server = 'foreign'", sql)


if __name__ == "__main__":
    unittest.main()
