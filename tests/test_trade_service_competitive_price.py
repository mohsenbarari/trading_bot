import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.services.trade_service import (
    BUY_PRICE_WARNING_TYPE,
    SELL_PRICE_WARNING_TYPE,
    detect_offer_price_warning,
    get_quantity_range,
    validate_competitive_price,
)


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
            proposed_price=100401,
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
            proposed_price=100400,
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
            proposed_price=99599,
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
            proposed_price=99600,
            user_id=7,
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    async def test_detect_offer_price_warning_warns_for_sell_below_lowest_active_price(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100200, 100300])))

        warning = await detect_offer_price_warning(
            db=db,
            offer_type="sell",
            commodity_id=1,
            quantity=10,
            proposed_price=99900,
            user_id=7,
        )

        self.assertIsNotNone(warning)
        self.assertEqual(warning["warning_type"], SELL_PRICE_WARNING_TYPE)
        self.assertEqual(warning["reference_price"], 100000)
        self.assertEqual(warning["proposed_price"], 99900)

    async def test_detect_offer_price_warning_warns_for_buy_above_highest_active_price(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=make_result([100000, 100100, 100300])))

        warning = await detect_offer_price_warning(
            db=db,
            offer_type="buy",
            commodity_id=1,
            quantity=25,
            proposed_price=100500,
            user_id=7,
        )

        self.assertIsNotNone(warning)
        self.assertEqual(warning["warning_type"], BUY_PRICE_WARNING_TYPE)
        self.assertEqual(warning["reference_price"], 100300)
        self.assertEqual(warning["proposed_price"], 100500)

    async def test_validate_competitive_price_filters_excluded_offers_from_query(self):
        seen_stmt = {}

        async def fake_execute(stmt):
            seen_stmt["sql"] = str(stmt)
            return make_result([100000, 100000, 100000])

        db = SimpleNamespace(execute=AsyncMock(side_effect=fake_execute))

        await validate_competitive_price(
            db=db,
            offer_type="sell",
            commodity_id=1,
            quantity=10,
            proposed_price=100400,
            user_id=7,
        )

        self.assertIn("exclude_from_competitive_price", seen_stmt["sql"])


if __name__ == "__main__":
    unittest.main()