"""First-pass private coin-group parsing tests; no raw inputs are persisted."""

from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from core.market_intelligence.coin_groups import (
    CoinGroupMessageInput,
    coin_group_settlement_conflict_reason,
    coin_group_offer_observations,
    parse_coin_group_offers,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


class CoinGroupParserTests(unittest.TestCase):
    def source(self, text: str, **changes: object) -> CoinGroupMessageInput:
        values: dict[str, object] = {
            "group_number": 1,
            "source_event_id": "private-message-1",
            "published_at_utc": "2026-08-04T10:00:00Z",
            "available_at_utc": "2026-08-04T10:00:05Z",
            "text": text,
        }
        values.update(changes)
        return CoinGroupMessageInput(**values)  # type: ignore[arg-type]

    def test_explicit_coin_offer_preserves_market_dimensions(self) -> None:
        parsed = parse_coin_group_offers(self.source("امامی فروش 186,900 / 5 تا نقدی"))

        self.assertEqual(len(parsed), 1)
        offer = parsed[0]
        self.assertEqual(
            (
                offer.commodity_code,
                offer.price_project_thousand_toman,
                offer.quantity,
                offer.side,
                offer.settlement_term,
                offer.trade_form,
                offer.quality_state,
            ),
            ("IMAM", 186_900, 5, "SELL", "CASH", "PHYSICAL", "ELIGIBLE"),
        )

    def test_unnamed_offer_is_not_silently_defaulted_to_imam(self) -> None:
        parsed = parse_coin_group_offers(self.source("خرید 181,900 / 5 تا فردایی"))

        self.assertEqual(len(parsed), 1)
        self.assertIsNone(parsed[0].commodity_code)
        self.assertEqual(parsed[0].quality_state, "PENDING_REVIEW")
        self.assertIn("POINT_IN_TIME", parsed[0].resolution_reason)

    def test_low_date_and_price_shorthand_are_normalized(self) -> None:
        parsed = parse_coin_group_offers(self.source("ربع تاریخ پایین خ 458 / 10 تا"))

        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            (parsed[0].commodity_code, parsed[0].price_project_thousand_toman),
            ("QUARTER_LOW_DATE", 45_800),
        )
        self.assertEqual(parsed[0].settlement_term, "TOMORROW")

    def test_old_and_new_settlement_syntax_and_nagh_are_distinguished(self) -> None:
        cases = {
            "خ ن ف امام 10 تا 183000": "TOMORROW",
            "خ ن امام 10 تا 183000": "CASH",
            "خن امام 10 تا 183000": "CASH",
            "خ ف امام 10 تا 183000": "TOMORROW",
            "خنف امام 10 تا 183000": "TOMORROW",
            "خ امام 10 تا 183000": "TOMORROW",
            "خ نق امام 10 تا 183000": "CASH",
            "10 تا نق خ 183000 امام": "CASH",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_coin_group_offers(self.source(text))
                self.assertEqual(len(parsed), 1)
                self.assertEqual(parsed[0].settlement_term, expected)

        self.assertEqual(
            coin_group_settlement_conflict_reason("خ ن ف امام 10 تا 183000", "CASH"),
            "SETTLEMENT_LABEL_CASH_BUT_TEXT_TOMORROW",
        )
        self.assertEqual(
            coin_group_settlement_conflict_reason("10 تا نق خ 183000", "TOMORROW"),
            "SETTLEMENT_LABEL_TOMORROW_BUT_TEXT_CASH",
        )

    def test_mint_year_is_metadata_but_thursday_cashier_offers_are_ignored(self) -> None:
        dated = parse_coin_group_offers(
            self.source("امام 1404 فروش 186,900 / 5 تا")
        )
        self.assertEqual(len(dated), 1)
        self.assertEqual(dated[0].price_project_thousand_toman, 186_900)
        self.assertEqual(
            parse_coin_group_offers(self.source("امام فروش 186,900 / 5 تا پنجشنبه")),
            [],
        )
        mixed = parse_coin_group_offers(
            self.source("امام 1404 فروش 186,900 / 5 تا\nنیم بهار فروش 94,500 / 5 تا")
        )
        self.assertEqual(
            [item.commodity_code for item in mixed],
            ["IMAM", "HALF_BAHAR"],
        )

    def test_dot_and_attached_slash_are_exact_thousands_separators(self) -> None:
        cases = {
            "نیم ف 95.200 5": (95_200, 5),
            "نیم ف 95/600 ده تا": (95_600, 10),
            "امام ف 188.300 / 5 تا": (188_300, 5),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_coin_group_offers(self.source(text))
                self.assertEqual(len(parsed), 1)
                self.assertEqual(
                    (parsed[0].price_project_thousand_toman, parsed[0].quantity),
                    expected,
                )

    def test_glued_and_bare_quantities_are_contextual_not_prices(self) -> None:
        cases = {
            "189ف 20": (189_000, 20, "SELL"),
            "188600خ 10": (188_600, 10, "BUY"),
            "١٠تا١٨٩ف": (189_000, 10, "SELL"),
            "۱ خريد نقد ۱۸۸": (188_000, 1, "BUY"),
            "۱۰ نیم ۴۰۴ ف ۹۴۵۰۰": (94_500, 10, "SELL"),
            "۲تاف94500 نیم": (94_500, 2, "SELL"),
            "3تا نقدی 187خ": (187_000, 3, "BUY"),
            "5 تا 188/600 ف": (188_600, 5, "SELL"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_coin_group_offers(self.source(text))
                self.assertEqual(len(parsed), 1)
                self.assertEqual(
                    (
                        parsed[0].price_project_thousand_toman,
                        parsed[0].quantity,
                        parsed[0].side,
                    ),
                    expected,
                )

        cash = parse_coin_group_offers(self.source("3تا نقدی 187خ"))[0]
        self.assertIsNone(cash.commodity_code)
        self.assertEqual(cash.settlement_term, "CASH")

    def test_unnamed_market_prices_use_only_the_canonical_scale(self) -> None:
        cases = {
            "10 تا 209700 ف": (209_700, 10, "SELL"),
            "10 تا 210 خ": (210_000, 10, "BUY"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_coin_group_offers(self.source(text))
                self.assertEqual(len(parsed), 1)
                self.assertEqual(
                    (
                        parsed[0].price_project_thousand_toman,
                        parsed[0].quantity,
                        parsed[0].side,
                    ),
                    expected,
                )

        # Four digits have no unambiguous project price scale and must not be
        # guessed into a valid market band.
        self.assertEqual(parse_coin_group_offers(self.source("10 تا 2097 خ")), [])

    def test_price_formats_use_explicit_family_or_causal_same_time_context(self) -> None:
        explicit_cases = {
            "20 تا نیم ف 112": ("HALF_BAHAR", 112_000, 20),
            "15 تا ربع بالای 80 ف 54.2": ("QUARTER_LOW_DATE", 54_200, 15),
            "20 تا نیم ف 10300": ("HALF_BAHAR", 103_000, 20),
            "1 دونه ربع خ 5400": ("QUARTER_BAHAR", 54_000, 1),
        }
        for text, expected in explicit_cases.items():
            with self.subTest(text=text):
                item = parse_coin_group_offers(self.source(text))[0]
                self.assertEqual(
                    (item.commodity_code, item.price_project_thousand_toman, item.quantity),
                    expected,
                )

        contextual = parse_coin_group_offers(
            self.source("100 تا 2181 خ"),
            price_context={"IMAM": (217_900, 218_000)},
        )
        self.assertEqual(
            (
                contextual[0].price_project_thousand_toman,
                contextual[0].quantity,
            ),
            (218_100, 100),
        )
        redundant = parse_coin_group_offers(
            self.source("10 تا 585000 ف"),
            price_context={"QUARTER_BAHAR": (58_400, 58_600)},
        )[0]
        self.assertEqual(redundant.price_project_thousand_toman, 58_500)
        tail = parse_coin_group_offers(
            self.source("500 خرید 5 تا"),
            price_context={"IMAM": (214_300, 214_500)},
        )[0]
        self.assertEqual(tail.price_project_thousand_toman, 214_500)
        missing_hundreds = parse_coin_group_offers(
            self.source("10 تا 14/800 ف"),
            price_context={"IMAM": (214_700, 214_900)},
        )[0]
        self.assertEqual(missing_hundreds.price_project_thousand_toman, 214_800)
        missing_full_zero = parse_coin_group_offers(
            self.source("10 تا نقدی خ 21900000"),
            price_context={"IMAM": (218_900, 219_100)},
        )[0]
        self.assertEqual(missing_full_zero.price_project_thousand_toman, 219_000)

    def test_quantity_aliases_and_multiple_clauses_are_parsed(self) -> None:
        cases = {
            "یدونه نیم 110400 خ": (1, 110_400),
            "دونه نیم 110400 خ": (1, 110_400),
            "یکی نیم نقدی 110.400 خ": (1, 110_400),
            "امام 201 خ بیستا": (20, 201_000),
            "ا عدد 217 ف": (1, 217_000),
            "1 امام نقدی 86 203400 ف": (1, 203_400),
            "45 ف 55500 ربع 86": (45, 55_500),
            "203/50005 تا ن خ": (5, 203_500),
            "20 تا خرید202/50000": (20, 202_500),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                item = parse_coin_group_offers(self.source(text))[0]
                self.assertEqual(
                    (item.quantity, item.price_project_thousand_toman),
                    expected,
                )

        parsed = parse_coin_group_offers(
            self.source("امام 20 تا 211 خ 20 تا 212500 ف")
        )
        self.assertEqual(
            [(item.side, item.quantity, item.price_project_thousand_toman) for item in parsed],
            [("BUY", 20, 211_000), ("SELL", 20, 212_500)],
        )
        malformed = parse_coin_group_offers(
            self.source("9 تا 217.500 ف 3 تا امام 403 و 404 2.15.500 ف")
        )
        self.assertEqual(
            [item.price_project_thousand_toman for item in malformed],
            [217_500, 215_500],
        )
        conditional_quantity = parse_coin_group_offers(
            self.source("100 تا نیم خ یکجا نهایت 2 تا حساب 104500")
        )
        self.assertEqual(
            [
                (
                    item.commodity_code,
                    item.quantity,
                    item.price_project_thousand_toman,
                )
                for item in conditional_quantity
            ],
            [("HALF_BAHAR", 100, 104_500)],
        )

    def test_full_toman_and_redundant_zero_prices_and_low_date_shorthand(self) -> None:
        cases = {
            "۴ گرمی سالم ف ۲۷.۷۰۰.۰۰۰": ("ONE_GRAM", 4, "SELL", 27_700),
            "۴ گرمی سالم ف ۲۷۷۰۰۰۰۰": ("ONE_GRAM", 4, "SELL", 27_700),
            "۲ تا ف ۱۸۸.۷۵۰.۰۰۰": (None, 2, "SELL", 188_750),
            "10 تا ربع نقدی ف۵۱۵۰۰۰": ("QUARTER_BAHAR", 10, "SELL", 51_500),
            "۴ نیم خ ۹۴.۸۰۰.۰۰۰": ("HALF_BAHAR", 4, "BUY", 94_800),
            "۳۰ تا ربع ف۵۱۹۰۰۰": ("QUARTER_BAHAR", 30, "SELL", 51_900),
            "20 تا رب پایبن بالا 80 47 ف": ("QUARTER_LOW_DATE", 20, "SELL", 47_000),
            "۱۲ تا ربع بالا ۸۰ ف نقدی ۴۶۵۰۰": ("QUARTER_LOW_DATE", 12, "SELL", 46_500),
            "۶ نیم ف ۹۵.۵۰۰.۰۰۰": ("HALF_BAHAR", 6, "SELL", 95_500),
            "۴ نیم پ خ ۹۴.۸۰۰.۰۰۰": ("HALF_LOW_DATE", 4, "BUY", 94_800),
            "۴ رب پ ف ۴۷.۰۰۰.۰۰۰": ("QUARTER_LOW_DATE", 4, "SELL", 47_000),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_coin_group_offers(self.source(text))
                self.assertEqual(len(parsed), 1)
                self.assertEqual(
                    (
                        parsed[0].commodity_code,
                        parsed[0].quantity,
                        parsed[0].side,
                        parsed[0].price_project_thousand_toman,
                    ),
                    expected,
                )

        cash = parse_coin_group_offers(self.source("10 تا ربع نقدی ف۵۱۵۰۰۰"))[0]
        self.assertEqual(cash.settlement_term, "CASH")

    def test_payment_timing_is_conditional(self) -> None:
        for text in (
            "امام ف 188900 / 5 تا حساب شب",
            "امام ف 188900 / 5 تا شب ح",
            "امام ف 188900 / 5 تا تا 9 شب",
        ):
            with self.subTest(text=text):
                parsed = parse_coin_group_offers(self.source(text))
                self.assertEqual(len(parsed), 1)
                self.assertTrue(parsed[0].is_conditional)

    def test_paper_variant_is_separate_from_physical_and_bad_text_is_ignored(self) -> None:
        parsed = parse_coin_group_offers(
            self.source("نیم بهار خرید 94,500 / 15 تا کاغذی معکوس فردایی")
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual((parsed[0].trade_form, parsed[0].settlement_term), ("PAPER_REVERSE", "TOMORROW"))
        self.assertEqual(parse_coin_group_offers(self.source("شروع معاملات امروز ساعت 10")), [])


class CoinGroupObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_market_store(Path(self.tempdir.name) / "market.sqlite3")
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_final_market_fact_has_no_text_or_private_message_identity(self) -> None:
        source = CoinGroupMessageInput(
            group_number=2,
            source_event_id="private-telegram-id-999",
            published_at_utc="2026-08-04T10:00:00Z",
            available_at_utc="2026-08-04T10:00:05Z",
            text="امام فروش 186,900 / 5 تا",
        )
        observations = coin_group_offer_observations(source)
        self.assertEqual(len(observations), 1)
        upsert_observation(self.connection, observations[0])
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT source_code, instrument, price_num, quality_state, attributes_json
            FROM market_observations
            """
        ).fetchone()
        columns = {
            item["name"] for item in self.connection.execute("PRAGMA table_info(market_observations)")
        }
        self.assertEqual(
            (row["source_code"], row["instrument"], row["price_num"], row["quality_state"]),
            ("GROUP_2", "COIN_IMAM", 186_900.0, "ELIGIBLE"),
        )
        self.assertNotIn("raw_text", columns)
        self.assertNotIn("message_id", columns)
        self.assertNotIn("private-telegram-id", row["attributes_json"])
        self.assertNotIn("امام", row["attributes_json"])
        attributes = json.loads(row["attributes_json"])
        self.assertEqual(
            attributes["field_evidence"]["settlement"],
            ["DEFAULT_TOMORROW_BOOK"],
        )
        self.assertEqual(
            attributes["field_evidence"]["instrument"],
            ["EXPLICIT_COMMODITY_TOKEN"],
        )


if __name__ == "__main__":
    unittest.main()
