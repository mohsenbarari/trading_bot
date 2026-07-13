import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.utils import offer_parser
from bot.utils.offer_parser import _match_commodity_name
from core.services.trade_service import (
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
    validate_offer_trade_amount,
    validate_price,
    validate_quantity,
)


class _AsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def all_result(items):
    scalars = Mock()
    scalars.all.return_value = items
    result = Mock()
    result.scalars.return_value = scalars
    return result


class ManualOfferValidationTests(unittest.TestCase):
    def test_price_must_be_five_or_six_digits(self):
        self.assertFalse(validate_price(9999)[0])
        self.assertTrue(validate_price(10000)[0])
        self.assertTrue(validate_price(999999)[0])
        self.assertFalse(validate_price(1000000)[0])

    def test_quantity_uses_admin_configured_system_maximum(self):
        custom_settings = SimpleNamespace(offer_min_quantity=5, offer_max_quantity=75)

        with patch("core.services.trade_service.get_trading_settings", return_value=custom_settings):
            self.assertTrue(validate_quantity(75)[0])
            self.assertFalse(validate_quantity(76)[0])

    def test_retail_trade_buttons_include_total_remaining_and_registered_lots(self):
        available = get_available_trade_amounts(
            quantity=34,
            remaining_quantity=34,
            is_wholesale=False,
            lot_sizes=[16, 10, 8],
        )
        self.assertEqual(available, [34, 16, 10, 8])

        valid, error, amount, _ = validate_offer_trade_amount(34, 34, False, [16, 10, 8], 10)
        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertEqual(amount, 10)

        valid_after_first_trade, error_after_first_trade, _, available_after_first_trade = validate_offer_trade_amount(
            quantity=34,
            remaining_quantity=24,
            is_wholesale=False,
            lot_sizes=[16, 8],
            requested_amount=10,
        )

        self.assertFalse(valid_after_first_trade)
        self.assertEqual(error_after_first_trade, "این لات دیگر موجود نیست.")
        self.assertEqual(available_after_first_trade, [24, 16, 8])

    def test_retail_trade_allows_full_remaining_quantity_button(self):
        available = get_available_trade_amounts(
            quantity=34,
            remaining_quantity=18,
            is_wholesale=False,
            lot_sizes=[10, 8],
        )

        self.assertEqual(available, [18, 10, 8])
        self.assertIn(18, available)

        valid, error, amount, available_amounts = validate_offer_trade_amount(34, 18, False, [10, 8], 18)
        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertEqual(amount, 18)
        self.assertEqual(available_amounts, [18, 10, 8])

    def test_lot_unavailable_payload_contains_remaining_buttons(self):
        payload = build_lot_unavailable_suggestion_payload(
            offer_id=77,
            requested_amount=10,
            offer_type="sell",
            commodity_name="سکه امامی",
            price=75800,
            remaining_quantity=24,
            available_amounts=[24, 16, 8],
        )

        self.assertEqual(payload["error_code"], "TRADE_LOT_UNAVAILABLE")
        self.assertEqual(payload["offer_id"], 77)
        self.assertEqual(payload["offer_type"], "sell")
        self.assertEqual(payload["offer_type_label"], "فروش")
        self.assertEqual(payload["settlement_type"], "cash")
        self.assertEqual(payload["settlement_type_label"], "نقد حاضر ☀️")
        self.assertEqual(payload["available_lots"], [24, 16, 8])
        self.assertEqual(payload["lot_summary"], "24 + 16 + 8")
        self.assertIn("بخش 10 عددی", payload["message"])
        self.assertIn("🔴فروش سکه امامی 24 عدد نقد حاضر ☀️ 75,800", payload["message"])
        self.assertIn("24 + 16 + 8", payload["message"])

    def test_lot_unavailable_payload_handles_empty_available_lots(self):
        payload = build_lot_unavailable_suggestion_payload(
            offer_id=78,
            requested_amount=10,
            offer_type="sell",
            commodity_name="سکه امامی",
            price=75800,
            remaining_quantity=0,
            available_amounts=[],
        )

        self.assertEqual(payload["lot_summary"], "ندارد")
        self.assertIn("این پیشنهاد در حال حاضر دکمه فعالی ندارد.", payload["message"])

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

    def test_offer_parser_helper_error_paths(self):
        self.assertEqual(offer_parser.normalize_digits("۱۲٣"), "123")
        self.assertEqual(offer_parser.validate_characters("امام 30تا"), (True, None))
        self.assertEqual(offer_parser.validate_characters(""), (False, "کاراکتر غیرمجاز در متن"))
        self.assertEqual(offer_parser.validate_characters("امام 30تا @"), (False, "کاراکتر غیرمجاز: «@»"))
        self.assertEqual(offer_parser._extract_residual_commodity_text("30تا 75800 15 15"), "")
        self.assertEqual(offer_parser._extract_residual_commodity_text("ربغ 30تا 75800"), "ربغ")

        self.assertEqual(offer_parser.extract_trade_type("سلام"), (None, None))
        self.assertEqual(
            offer_parser.extract_trade_type("خ خرید امام 30تا 75800"),
            (None, "❌ چندین نشانگر خرید در لفظ وجود دارد"),
        )
        self.assertEqual(
            offer_parser.extract_trade_type("ف فروش امام 30تا 75800"),
            (None, "❌ چندین نشانگر فروش در لفظ وجود دارد"),
        )
        self.assertEqual(
            offer_parser.extract_trade_type("خ ف امام 30تا 75800"),
            (None, "❌ هم نشانگر خرید و هم فروش در لفظ وجود دارد"),
        )
        self.assertEqual(offer_parser.extract_trade_type("ف امام 30تا 75800"), ("sell", None))

        self.assertEqual(
            offer_parser.extract_quantity("30تا 20 عدد"),
            (None, "❌ چندین تعداد در لفظ وجود دارد"),
        )
        self.assertEqual(
            offer_parser.extract_price("75800 85900"),
            (None, "❌ چندین قیمت در لفظ وجود دارد (فقط یک عدد 5 یا 6 رقمی مجاز است)"),
        )
        with patch("bot.utils.offer_parser.validate_price", return_value=(False, "boom")) as validate_price_mock:
            self.assertEqual(
                offer_parser.extract_price("75800"),
                (None, "❌ قیمت یافت نشد (باید عدد 5 یا 6 رقمی باشد)"),
            )
            validate_price_mock.assert_called_once_with("75800")

        with patch("bot.utils.offer_parser.get_trading_settings", return_value=SimpleNamespace(lot_min_size=5, lot_max_count=2)):
            self.assertEqual(
                offer_parser.extract_lot_sizes("30تا 75800 10 10 10", 30, 75800),
                (None, False, "❌ حداکثر 2 بخش مجاز است (تعداد فعلی: 3)"),
            )
        with patch("bot.utils.offer_parser.get_trading_settings", return_value=SimpleNamespace(lot_min_size=5, lot_max_count=4)):
            self.assertEqual(
                offer_parser.extract_lot_sizes("30تا 75800 3 27", 30, 75800),
                (None, False, "❌ حداقل تعداد باید 5 باشد (بخش نامعتبر: 3)"),
            )


class ManualOfferParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_find_commodity = offer_parser.find_commodity
        self.original_get_trading_settings = offer_parser.get_trading_settings
        offer_parser.get_trading_settings = lambda: SimpleNamespace(
            offer_min_quantity=5,
            offer_max_quantity=50,
            lot_min_size=5,
            lot_max_count=5,
        )

        async def fake_find_commodity(text):
            mapping = {
                "امام": (1, "امام"),
                "بهار": (2, "بهار"),
                "ربع بهار": (3, "ربع بهار"),
                "نیم بهار": (4, "نیم بهار"),
            }
            commodity = _match_commodity_name(text, mapping)
            if commodity[0] is not None:
                return commodity
            if not offer_parser._extract_residual_commodity_text(text):
                return mapping["امام"]
            return None, "نامشخص"

        offer_parser.find_commodity = fake_find_commodity

    async def asyncTearDown(self):
        offer_parser.find_commodity = self.original_find_commodity
        offer_parser.get_trading_settings = self.original_get_trading_settings

    async def test_text_offer_accepts_required_manual_format(self):
        result, error = await offer_parser.parse_offer_text("خ ن امام 30تا 75800 15 15: فقط نقدی")

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.trade_type, "buy")
        self.assertEqual(result.settlement_type, "cash")
        self.assertEqual(result.commodity_id, 1)
        self.assertEqual(result.quantity, 30)
        self.assertEqual(result.price, 75800)
        self.assertEqual(result.lot_sizes, [15, 15])
        self.assertEqual(result.notes, "فقط نقدی")

    async def test_text_offer_quantity_limit_follows_system_settings(self):
        offer_parser.get_trading_settings = lambda: SimpleNamespace(
            offer_min_quantity=5,
            offer_max_quantity=75,
            lot_min_size=5,
            lot_max_count=3,
        )

        result, error = await offer_parser.parse_offer_text("خ ن امام 60تا 75800")

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.quantity, 60)

    async def test_text_offer_defaults_to_implicit_imam_when_commodity_is_omitted(self):
        result, error = await offer_parser.parse_offer_text("خ ن 30تا 75800")

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.commodity_id, 1)
        self.assertEqual(result.commodity_name, "امام")

    async def test_text_offer_rejects_invalid_manual_format(self):
        invalid_samples = [
            "خ ن امام 51تا 75800",
            "خ ن امام 30تا 9999",
            "خ ن امام 30 75800",
            "خ ن امام 30تا 75800 10 10",
            "خ ن ربغ 30تا 75800",
            "خ ن ربع بهار 10تا 75800".replace("ربع بهار", "ربع بهارک"),
        ]

        for sample in invalid_samples:
            with self.subTest(sample=sample):
                result, error = await offer_parser.parse_offer_text(sample)
                self.assertIsNone(result)
                self.assertIsNotNone(error)

    async def test_text_offer_rejects_retail_lot_below_min_with_quantity_style_message(self):
        offer_parser.get_trading_settings = lambda: SimpleNamespace(
            offer_min_quantity=7,
            offer_max_quantity=75,
            lot_min_size=7,
            lot_max_count=4,
        )

        result, error = await offer_parser.parse_offer_text("خ ن امام 45تا 184000 5 20 20: تک فیش")

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error.message, "❌ حداقل تعداد باید 7 باشد (بخش نامعتبر: 5)")

    async def test_find_commodity_supports_cached_aliases_and_db_backfill(self):
        cached_items = [
            {"id": 1, "name": "امام", "aliases": ["ام", {"alias": "امامی"}]},
        ]
        with patch("bot.utils.redis_helpers.get_cached_commodities", AsyncMock(return_value=cached_items)), patch(
            "bot.utils.redis_helpers.set_cached_commodities", AsyncMock()
        ) as set_cached:
            self.assertEqual(await self.original_find_commodity("امامی 30تا 75800"), (1, "امام"))
            self.assertEqual(await self.original_find_commodity("30تا 75800"), (1, "امام"))
            self.assertEqual(await self.original_find_commodity("ربغ 30تا 75800"), (None, "نامشخص"))
        set_cached.assert_not_awaited()

        commodities = [SimpleNamespace(id=1, name="امام"), SimpleNamespace(id=2, name="بهار")]
        aliases = [SimpleNamespace(id=7, alias="امامی", commodity_id=1)]
        session = SimpleNamespace(execute=AsyncMock(side_effect=[all_result(commodities), all_result(aliases)]))
        with patch("bot.utils.redis_helpers.get_cached_commodities", AsyncMock(return_value=None)), patch(
            "bot.utils.redis_helpers.set_cached_commodities", AsyncMock()
        ) as set_cached, patch("bot.utils.offer_parser.AsyncSessionLocal", return_value=_AsyncSessionContext(session)):
            self.assertEqual(await self.original_find_commodity("امامی 30تا 75800"), (1, "امام"))

        set_cached.assert_awaited_once()
        self.assertEqual(set_cached.await_args.kwargs["ttl"], 300)

        with patch(
            "bot.utils.redis_helpers.get_cached_commodities",
            AsyncMock(return_value=[{"id": 1, "name": "امام", "aliases": []}]),
        ), patch("bot.utils.redis_helpers.set_cached_commodities", AsyncMock()):
            self.assertEqual(await self.original_find_commodity("ناشناس 30تا 75800"), (None, "نامشخص"))

    async def test_parse_offer_text_guard_paths(self):
        too_long_notes = "خ ن امام 30تا 75800:" + ("الف" * 201)
        result, error = await offer_parser.parse_offer_text(too_long_notes)
        self.assertIsNone(result)
        self.assertEqual(error.message, "❌ توضیحات نباید بیش از 200 کاراکتر باشد")

        result, error = await offer_parser.parse_offer_text("سلام")
        self.assertIsNone(result)
        self.assertIsNone(error)

        result, error = await offer_parser.parse_offer_text("خ ن امام 30تا 75800 @")
        self.assertIsNone(result)
        self.assertEqual(error.message, "کاراکتر غیرمجاز: «@»")

        result, error = await offer_parser.parse_offer_text("خ ن خرید امام 30تا 75800")
        self.assertIsNone(result)
        self.assertEqual(error.message, "❌ نشانگر خرید یا فروش فقط در ابتدای لفظ مجاز است")

        offer_parser.get_trading_settings = lambda: SimpleNamespace(
            offer_min_quantity=5,
            offer_max_quantity=75,
            lot_min_size=5,
            lot_max_count=3,
        )
        result, error = await offer_parser.parse_offer_text("خ ن امام 4تا 75800")
        self.assertIsNone(result)
        self.assertEqual(error.message, "❌ حداقل تعداد باید 5 باشد")

    async def test_text_offer_accepts_all_exact_settlement_prefixes(self):
        cases = (
            ("خ ن امام 30تا 75800", "buy", "cash"),
            ("ف ن امام 30تا 75800", "sell", "cash"),
            ("خ ن ف امام 30تا 75800", "buy", "tomorrow"),
            ("ف ن ف امام 30تا 75800", "sell", "tomorrow"),
            ("خرید نقد امام 30تا 75800", "buy", "cash"),
            ("فروش نقد امام 30تا 75800", "sell", "cash"),
            ("خرید نقد فردا امام 30تا 75800", "buy", "tomorrow"),
            ("فروش نقد فردا امام 30تا 75800", "sell", "tomorrow"),
        )

        for sample, expected_trade_type, expected_settlement_type in cases:
            with self.subTest(sample=sample):
                result, error = await offer_parser.parse_offer_text(sample)
                self.assertIsNone(error)
                self.assertIsNotNone(result)
                self.assertEqual(result.trade_type, expected_trade_type)
                self.assertEqual(result.settlement_type, expected_settlement_type)

    async def test_text_offer_rejects_legacy_and_loose_settlement_prefixes(self):
        invalid_samples = (
            "خ امام 30تا 75800",
            "ف امام 30تا 75800",
            "خرید امام 30تا 75800",
            "فروش امام 30تا 75800",
            "خ فردا امام 30تا 75800",
            "خ ف امام 30تا 75800",
            "خ ن فردا امام 30تا 75800",
            "فروش فردا امام 30تا 75800",
        )

        for sample in invalid_samples:
            with self.subTest(sample=sample):
                result, error = await offer_parser.parse_offer_text(sample)
                self.assertIsNone(result)
                self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
