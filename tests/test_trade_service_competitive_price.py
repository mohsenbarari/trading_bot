import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.services.trade_service import get_quantity_range, validate_competitive_price


def make_result(prices):
    return SimpleNamespace(fetchall=lambda: [(price,) for price in prices])


class TradeServiceCompetitivePriceTests(unittest.IsolatedAsyncioTestCase):
    def test_get_quantity_range_respects_defined_boundaries(self):
        cases = [
            (4, None),
            (5, "A"),
            (20, "A"),
            (21, "B"),
            (40, "B"),
            (41, "C"),
            (50, "C"),
            (51, None),
        ]

        for quantity, expected in cases:
            with self.subTest(quantity=quantity):
                self.assertEqual(get_quantity_range(quantity), expected)

    async def test_validate_competitive_price_skips_db_for_out_of_range_quantities(self):
        db = SimpleNamespace(execute=AsyncMock())

        is_valid, error = await validate_competitive_price(
            db=db,
            offer_type="sell",
            commodity_id=1,
            quantity=99,
            proposed_price=100000,
            user_id=7,
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")
        db.execute.assert_not_awaited()

    async def test_validate_competitive_price_accepts_when_not_enough_similar_offers_exist(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100100])))

        is_valid, error = await validate_competitive_price(
            db=db,
            offer_type="sell",
            commodity_id=1,
            quantity=10,
            proposed_price=110000,
            user_id=7,
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")
        db.execute.assert_awaited_once()

    async def test_validate_competitive_price_rejects_sell_above_tolerance(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100000, 100000])))

        is_valid, error = await validate_competitive_price(
            db=db,
            offer_type="sell",
            commodity_id=1,
            quantity=10,
            proposed_price=100301,
            user_id=7,
        )

        self.assertFalse(is_valid)
        self.assertEqual(error, "❌ لفظ شما تایید نشد.\nفروشنده‌ای با قیمت پایین‌تر وجود دارد.")

    async def test_validate_competitive_price_allows_sell_at_tolerance_boundary(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100000, 100000])))

        is_valid, error = await validate_competitive_price(
            db=db,
            offer_type="sell",
            commodity_id=1,
            quantity=10,
            proposed_price=100300,
            user_id=7,
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    async def test_validate_competitive_price_rejects_buy_below_tolerance(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100000, 100000])))

        is_valid, error = await validate_competitive_price(
            db=db,
            offer_type="buy",
            commodity_id=1,
            quantity=25,
            proposed_price=99699,
            user_id=7,
        )

        self.assertFalse(is_valid)
        self.assertEqual(error, "❌ لفظ شما تایید نشد.\nخریداری با قیمت بالاتر وجود دارد.")

    async def test_validate_competitive_price_allows_buy_at_tolerance_boundary(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100000, 100000])))

        is_valid, error = await validate_competitive_price(
            db=db,
            offer_type="buy",
            commodity_id=1,
            quantity=25,
            proposed_price=99700,
            user_id=7,
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()