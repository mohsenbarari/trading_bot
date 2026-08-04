"""First-pass private coin-group parsing tests; no raw inputs are persisted."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_groups import (
    CoinGroupMessageInput,
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
            ("IMAM", 186_900, 5, "SELL", "CASH", "PHYSICAL", "PENDING_REVIEW"),
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

    def test_year_403_404_and_thursday_cashier_offers_are_ignored(self) -> None:
        self.assertEqual(
            parse_coin_group_offers(self.source("امام 1404 فروش 186,900 / 5 تا")),
            [],
        )
        self.assertEqual(
            parse_coin_group_offers(self.source("امام فروش 186,900 / 5 تا پنجشنبه")),
            [],
        )
        mixed = parse_coin_group_offers(
            self.source("امام 1404 فروش 186,900 / 5 تا\nنیم بهار فروش 94,500 / 5 تا")
        )
        self.assertEqual([item.commodity_code for item in mixed], ["HALF_BAHAR"])

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
            ("GROUP_2", "COIN_IMAM", 186_900.0, "PENDING_REVIEW"),
        )
        self.assertNotIn("raw_text", columns)
        self.assertNotIn("message_id", columns)
        self.assertNotIn("private-telegram-id", row["attributes_json"])
        self.assertNotIn("امام", row["attributes_json"])


if __name__ == "__main__":
    unittest.main()
