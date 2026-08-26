from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.private_coin_processor import (
    CoinProcessorPaths,
    process_coin_spool_cycle,
)


def market_event(
    sequence: int,
    *,
    source: str,
    message_id: int,
    text: str | None,
    published: str | None,
    available: str,
    event_type: str = "message_created",
    edited: str | None = None,
) -> dict[str, object]:
    return {
        "schema": "market_channel_event",
        "schema_version": "1.0",
        "event_id": f"60000000-0000-7000-8000-{sequence:012d}",
        "event_type": event_type,
        "source": {
            "market": "coin_intelligence",
            "source_id": source,
            "source_family": (
                "TELEGRAM_PRIVATE"
                if source == "MELTED_PRIMARY_FLOW"
                else "TELEGRAM_PUBLIC"
            ),
            "parser_profile": source,
        },
        "message": {
            "message_id": str(message_id),
            "published_at_utc": published,
            "edited_at_utc": edited,
            "text": text,
            "text_sha256": (
                sha256(text.encode("utf-8")).hexdigest()
                if text is not None
                else None
            ),
            "entities": [],
            "is_forwarded": False,
        },
        "producer": {
            "available_at_utc": available,
            "is_backfill": False,
        },
    }


class MarketPipelineStage6ChannelProcessorTests(unittest.TestCase):
    def _paths(self, root: Path) -> CoinProcessorPaths:
        account1 = root / "capture" / "account1"
        account2 = root / "capture" / "account2"
        account1.mkdir(parents=True)
        account2.mkdir(parents=True)
        state = root / "state"
        state.mkdir()
        return CoinProcessorPaths(
            spool_directory=account2,
            market_spool_directory=account1,
            staging_database=state / "staging.sqlite3",
            market_database=state / "market.sqlite3",
            corpus_database=state / "corpus.sqlite3",
            feedback_database=None,
            prediction_database=None,
        )

    def _write(self, paths: CoinProcessorPaths, events: list[dict[str, object]]) -> None:
        assert paths.market_spool_directory is not None
        (paths.market_spool_directory / "events-2026-08-24.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
            encoding="utf-8",
        )

    def test_all_channels_and_private_lifecycle_project_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            events = [
                market_event(
                    1,
                    source="XAUUSD",
                    message_id=1,
                    text="4630.10",
                    published="2026-08-24T10:00:01Z",
                    available="2026-08-24T10:00:02Z",
                ),
                market_event(
                    2,
                    source="XAUUSD",
                    message_id=2,
                    text="4631.20",
                    published="2026-08-24T10:00:40Z",
                    available="2026-08-24T10:00:41Z",
                ),
                market_event(
                    3,
                    source="USD_HERAT",
                    message_id=3,
                    text="هرات فردایی 185,200 خرید",
                    published="2026-08-24T10:00:03Z",
                    available="2026-08-24T10:00:04Z",
                ),
                market_event(
                    4,
                    source="MELTED_AGGREGATE",
                    message_id=4,
                    text="#آبشده نقدی 80,000,000",
                    published="2026-08-24T10:00:05Z",
                    available="2026-08-24T10:00:06Z",
                ),
                market_event(
                    5,
                    source="MELTED_FLOW",
                    message_id=5,
                    text="79,270,000 باحواله فروش",
                    published="2026-08-24T10:00:07Z",
                    available="2026-08-24T10:00:08Z",
                ),
                market_event(
                    6,
                    source="MELTED_PRIMARY_FLOW",
                    message_id=6,
                    text="95,000,000 فروش 10 تا بدون حواله",
                    published="2026-08-24T10:00:00Z",
                    available="2026-08-24T10:00:01Z",
                ),
                market_event(
                    7,
                    source="MELTED_PRIMARY_FLOW",
                    message_id=6,
                    text="95,000,000 فروش 10 تا بدون حواله باقی 6",
                    published="2026-08-24T10:00:00Z",
                    edited="2026-08-24T10:00:40Z",
                    available="2026-08-24T10:00:41Z",
                    event_type="message_edited",
                ),
                market_event(
                    8,
                    source="MELTED_PRIMARY_FLOW",
                    message_id=6,
                    text=None,
                    published=None,
                    available="2026-08-24T10:01:50Z",
                    event_type="message_deleted",
                ),
            ]
            self._write(paths, events)
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:01Z",
            )
            self.assertEqual(
                report["stream_records"],
                {"market": 8, "coin": 0, "external": 0},
            )
            self.assertEqual(report["private_trade_outcomes"], {"PARTIAL": 1})
            market = sqlite3.connect(paths.market_database)
            market.row_factory = sqlite3.Row
            staging = sqlite3.connect(paths.staging_database)
            staging.row_factory = sqlite3.Row
            try:
                xau = market.execute(
                    "SELECT price_value FROM market_observations "
                    "WHERE source_code='XAUUSD' AND quality_state='ELIGIBLE' "
                    "ORDER BY event_time_utc"
                ).fetchall()
                private = market.execute(
                    "SELECT event_type,price_value,quantity_value FROM market_observations "
                    "WHERE source_code='PRIVATE_GOLD_CHANNEL' "
                    "AND quality_state='ELIGIBLE' ORDER BY event_type"
                ).fetchall()
                outcome_columns = {
                    row[1]
                    for row in staging.execute(
                        "PRAGMA table_info(capture_primary_trade_outcomes)"
                    )
                }
            finally:
                staging.close()
                market.close()
            self.assertEqual([row["price_value"] for row in xau], ["4630.10", "4631.20"])
            self.assertEqual(
                [(row["event_type"], row["price_value"], row["quantity_value"]) for row in private],
                [("OFFER", "95000000", "10"), ("TRADE", "95000000", "4")],
            )
            self.assertNotIn("final_price", outcome_columns)
            self.assertNotIn("final_quantity", outcome_columns)

    def test_inconsistent_revision_is_ambiguous_but_offer_stays_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            self._write(
                paths,
                [
                    market_event(
                        20,
                        source="MELTED_PRIMARY_FLOW",
                        message_id=20,
                        text="95,000,000 فروش 10 تا بدون حواله",
                        published="2026-08-24T10:00:00Z",
                        available="2026-08-24T10:00:01Z",
                    ),
                    market_event(
                        21,
                        source="MELTED_PRIMARY_FLOW",
                        message_id=20,
                        text="96,000,000 فروش 10 تا بدون حواله باقی 6",
                        published="2026-08-24T10:00:00Z",
                        edited="2026-08-24T10:00:40Z",
                        available="2026-08-24T10:00:41Z",
                        event_type="message_edited",
                    ),
                ],
            )
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-24T10:02:01Z",
            )
            self.assertEqual(report["private_trade_outcomes"], {"AMBIGUOUS": 1})
            connection = sqlite3.connect(paths.market_database)
            try:
                row = connection.execute(
                    "SELECT price_value FROM market_observations "
                    "WHERE source_code='PRIVATE_GOLD_CHANNEL' "
                    "AND event_type='OFFER' AND quality_state='ELIGIBLE'"
                ).fetchone()
                trades = connection.execute(
                    "SELECT COUNT(*) FROM market_observations "
                    "WHERE source_code='PRIVATE_GOLD_CHANNEL' "
                    "AND event_type='TRADE' AND quality_state='ELIGIBLE'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(row[0], "95000000")
            self.assertEqual(trades, 0)

    def test_public_melted_facts_are_not_kept_beyond_three_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            self._write(
                paths,
                [
                    market_event(
                        30,
                        source="MELTED_FLOW",
                        message_id=30,
                        text="79,270,000 باحواله فروش",
                        published="2026-08-24T10:00:00Z",
                        available="2026-08-24T10:00:01Z",
                    ),
                    market_event(
                        31,
                        source="XAUUSD",
                        message_id=31,
                        text="4630.10",
                        published="2026-08-24T10:00:00Z",
                        available="2026-08-24T10:00:01Z",
                    ),
                ],
            )
            report = process_coin_spool_cycle(
                paths=paths,
                mode="fixture",
                now_utc="2026-08-28T10:00:02Z",
            )
            self.assertEqual(report["temporary_public_melted_facts_purged"], 1)
            connection = sqlite3.connect(paths.market_database)
            try:
                sources = {
                    row[0]
                    for row in connection.execute(
                        "SELECT source_code FROM market_observations"
                    ).fetchall()
                }
            finally:
                connection.close()
            self.assertEqual(sources, {"XAUUSD"})


if __name__ == "__main__":
    unittest.main()
