"""Offline tests for the private melted-gold normalization rules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
)
from core.market_intelligence.market_snapshot import build_market_snapshot
from core.market_intelligence.private_gold import (
    PrivateGoldOfferInput,
    ingest_private_gold_offer,
    parse_private_gold_offer,
    private_gold_observations,
    refresh_private_gold_paper_minute,
)


class PrivateGoldParserTests(unittest.TestCase):
    def source(self, text: str, **changes: object) -> PrivateGoldOfferInput:
        values: dict[str, object] = {
            "source_event_id": "private-test-1",
            "published_at_utc": "2026-08-04T10:00:05Z",
            "available_at_utc": "2026-08-04T10:01:00Z",
            "text": text,
        }
        values.update(changes)
        return PrivateGoldOfferInput(**values)  # type: ignore[arg-type]

    def test_with_havale_without_day_is_paper_tomorrow(self) -> None:
        parsed = parse_private_gold_offer(
            self.source("80,300,000 فروش 5 تا باحواله")
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(
            (parsed.trade_form, parsed.settlement_term, parsed.paper_variant),
            ("PAPER_NORMAL", "TOMORROW", "NORMAL"),
        )

    def test_bare_day_is_paper_today_and_not_physical(self) -> None:
        parsed = parse_private_gold_offer(
            self.source("80,300,000 فروشروز 5 تا")
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(
            (parsed.trade_form, parsed.settlement_term),
            ("PAPER_NORMAL", "TODAY"),
        )

    def test_physical_markers_are_explicit_and_preserve_tomorrow(self) -> None:
        today = parse_private_gold_offer(
            self.source("80,300,000 خرید 5 تا نقد حاضر")
        )
        tomorrow = parse_private_gold_offer(
            self.source("80,300,000 فروش 5 تا بدون حواله")
        )

        self.assertEqual((today.trade_form, today.settlement_term), ("PHYSICAL", "TODAY"))
        self.assertEqual((tomorrow.trade_form, tomorrow.settlement_term), ("PHYSICAL", "TOMORROW"))

    def test_unmarked_offer_abstains_instead_of_guessing_physical(self) -> None:
        self.assertIsNone(parse_private_gold_offer(self.source("80,300,000 فروش 5 تا")))

    def test_edit_time_is_trade_time_and_price_is_explicitly_converted_to_irt(self) -> None:
        observations = private_gold_observations(
            self.source(
                "80,300,000 فروش 5 تا با حواله",
                edited_at_utc="2026-08-04T10:02:00Z",
                trade_detected_at_utc="2026-08-04T10:04:00Z",
                trade_status="FULL",
            )
        )

        self.assertEqual([item.event_type for item in observations], ["OFFER", "TRADE"])
        self.assertEqual(str(observations[0].price), "803000000")
        self.assertEqual(str(observations[1].event_time_utc), "2026-08-04T10:02:00Z")
        self.assertEqual(observations[1].quantity, 5)

    def test_partial_without_quantity_does_not_become_a_full_trade(self) -> None:
        observations = private_gold_observations(
            self.source(
                "80,300,000 فروش 5 تا با حواله",
                edited_at_utc="2026-08-04T10:02:00Z",
                trade_status="PARTIAL",
            )
        )

        self.assertEqual([item.event_type for item in observations], ["OFFER"])

    def test_edit_without_verifier_quantity_is_full_trade_by_source_convention(self) -> None:
        observations = private_gold_observations(
            self.source(
                "80,300,000 فروش 5 تا با حواله",
                edited_at_utc="2026-08-04T10:02:00Z",
            )
        )

        self.assertEqual([item.event_type for item in observations], ["OFFER", "TRADE"])
        self.assertEqual(observations[1].quantity, 5)

    def test_explicit_no_trade_overrides_an_edit(self) -> None:
        observations = private_gold_observations(
            self.source(
                "80,300,000 فروش 5 تا با حواله",
                edited_at_utc="2026-08-04T10:02:00Z",
                trade_status="NONE",
            )
        )

        self.assertEqual([item.event_type for item in observations], ["OFFER"])


class PrivateGoldMinuteAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "market.sqlite3"
        self.connection = connect_market_store(self.database)
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def _ingest(self, source_event_id: str, text: str, **changes: object) -> None:
        values: dict[str, object] = {
            "source_event_id": source_event_id,
            "published_at_utc": "2026-08-04T10:00:05Z",
            "available_at_utc": "2026-08-04T10:01:00Z",
            "text": text,
        }
        values.update(changes)
        ingest_private_gold_offer(
            self.connection,
            PrivateGoldOfferInput(**values),  # type: ignore[arg-type]
        )
        self.connection.commit()

    def test_paper_minute_weights_confirmed_trades_and_keeps_raw_facts(self) -> None:
        self._ingest("p1", "80,000,000 خرید 5 تا با حواله")
        self._ingest(
            "p2",
            "81,000,000 فروش 5 تا با حواله",
            published_at_utc="2026-08-04T10:00:20Z",
            edited_at_utc="2026-08-04T10:00:40Z",
            trade_status="FULL",
        )
        aggregate_id = refresh_private_gold_paper_minute(
            self.connection,
            settlement_term="TOMORROW",
            paper_variant="NORMAL",
            minute_utc="2026-08-04T10:00:00Z",
            available_at_utc="2026-08-04T10:01:00Z",
        )
        self.connection.commit()

        self.assertIsNotNone(aggregate_id)
        raw_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM market_observations
            WHERE source_code = 'PRIVATE_GOLD_CHANNEL'
            """
        ).fetchone()[0]
        aggregate = self.connection.execute(
            """
            SELECT price_num, attributes_json FROM market_observations
            WHERE source_code = 'PRIVATE_GOLD_PAPER_MINUTE'
            """
        ).fetchone()
        self.assertEqual(raw_count, 3)  # two offers + one edited confirmation
        self.assertEqual(aggregate["price_num"], 808_000_000.0)
        self.assertIn('"trade_count":1', aggregate["attributes_json"])
        snapshot = build_market_snapshot(
            self.connection,
            as_of_utc="2026-08-04T10:01:00Z",
        )
        self.assertEqual(
            snapshot["signals"]["PRIVATE_GOLD_PAPER_NORMAL_TOMORROW"]["latest_price"],
            808_000_000.0,
        )
        self.assertEqual(
            snapshot["signals"]["PRIVATE_GOLD_PAPER_REVERSE_TOMORROW"]["status"],
            "MISSING",
        )

    def test_conditional_paper_is_retained_but_excluded_from_normal_minute(self) -> None:
        self._ingest("normal", "80,000,000 خرید 5 تا با حواله")
        self._ingest("conditional", "60,000,000 فروش 5 تا با حواله فیش تا ساعت 12")
        aggregate_id = refresh_private_gold_paper_minute(
            self.connection,
            settlement_term="TOMORROW",
            paper_variant="NORMAL",
            minute_utc=datetime(2026, 8, 4, 10, tzinfo=timezone.utc),
            available_at_utc="2026-08-04T10:01:00Z",
        )
        self.connection.commit()

        self.assertIsNotNone(aggregate_id)
        aggregate = self.connection.execute(
            """
            SELECT price_num FROM market_observations
            WHERE source_code = 'PRIVATE_GOLD_PAPER_MINUTE'
            """
        ).fetchone()
        conditional = self.connection.execute(
            """
            SELECT is_conditional FROM market_observations
            WHERE source_code = 'PRIVATE_GOLD_CHANNEL'
              AND price_num = 600000000
            """
        ).fetchone()
        self.assertEqual(aggregate["price_num"], 800_000_000.0)
        self.assertEqual(conditional["is_conditional"], 1)

    def test_minute_quote_cannot_be_published_before_the_minute_closes(self) -> None:
        self._ingest("p1", "80,000,000 خرید 5 تا با حواله")

        with self.assertRaisesRegex(ValueError, "private_gold_minute_not_closed"):
            refresh_private_gold_paper_minute(
                self.connection,
                settlement_term="TOMORROW",
                paper_variant="NORMAL",
                minute_utc="2026-08-04T10:00:00Z",
                available_at_utc="2026-08-04T10:00:30Z",
            )


if __name__ == "__main__":
    unittest.main()
