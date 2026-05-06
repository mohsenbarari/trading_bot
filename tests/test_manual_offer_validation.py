import unittest

from bot.utils import offer_parser
from bot.utils.offer_parser import _match_commodity_name
from core.services.trade_service import validate_price, validate_quantity


class ManualOfferValidationTests(unittest.TestCase):
    def test_price_must_be_five_or_six_digits(self):
        self.assertFalse(validate_price(9999)[0])
        self.assertTrue(validate_price(10000)[0])
        self.assertTrue(validate_price(999999)[0])
        self.assertFalse(validate_price(1000000)[0])

    def test_quantity_never_allows_more_than_fifty(self):
        self.assertTrue(validate_quantity(50)[0])
        self.assertFalse(validate_quantity(51)[0])

    def test_bahar_variants_match_distinct_longest_aliases(self):
        commodities = {
            "بهار": (1, "بهار"),
            "ربع بهار": (2, "ربع بهار"),
            "نیم بهار": (3, "نیم بهار"),
        }

        self.assertEqual(_match_commodity_name("بهار 10تا 75800", commodities), (1, "بهار"))
        self.assertEqual(_match_commodity_name("ربع بهار 10تا 75800", commodities), (2, "ربع بهار"))
        self.assertEqual(_match_commodity_name("نیم بهار 10تا 75800", commodities), (3, "نیم بهار"))

    def test_short_bahar_alias_does_not_capture_missing_variant(self):
        commodities = {"بهار": (1, "بهار")}

        self.assertEqual(_match_commodity_name("ربع بهار 10تا 75800", commodities), (None, "نامشخص"))
        self.assertEqual(_match_commodity_name("نیم بهار 10تا 75800", commodities), (None, "نامشخص"))


class ManualOfferParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_find_commodity = offer_parser.find_commodity

        async def fake_find_commodity(text):
            mapping = {
                "امام": (1, "امام"),
                "بهار": (2, "بهار"),
                "ربع بهار": (3, "ربع بهار"),
                "نیم بهار": (4, "نیم بهار"),
            }
            return _match_commodity_name(text, mapping)

        offer_parser.find_commodity = fake_find_commodity

    async def asyncTearDown(self):
        offer_parser.find_commodity = self.original_find_commodity

    async def test_text_offer_accepts_required_manual_format(self):
        result, error = await offer_parser.parse_offer_text("خ امام 30تا 75800 15 15: فقط نقدی")

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.trade_type, "buy")
        self.assertEqual(result.commodity_id, 1)
        self.assertEqual(result.quantity, 30)
        self.assertEqual(result.price, 75800)
        self.assertEqual(result.lot_sizes, [15, 15])
        self.assertEqual(result.notes, "فقط نقدی")

    async def test_text_offer_rejects_invalid_manual_format(self):
        invalid_samples = [
            "خ امام 51تا 75800",
            "خ امام 30تا 9999",
            "خ 30تا 75800",
            "خ امام 30 75800",
            "خ امام 30تا 75800 10 10",
            "خ ربع بهار 10تا 75800".replace("ربع بهار", "ربع بهارک"),
        ]

        for sample in invalid_samples:
            with self.subTest(sample=sample):
                result, error = await offer_parser.parse_offer_text(sample)
                self.assertIsNone(result)
                self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
