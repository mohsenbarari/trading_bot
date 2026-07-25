from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.bundle import load_runtime_bundle
from core.market_intelligence.producer import (
    CoinSnapshotProducer,
    SnapshotProducerError,
    write_snapshot_atomic,
)
from core.market_intelligence.service import CoinIntelligenceShadowService


CUTOFF = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _stamp(second: int) -> str:
    return CUTOFF.replace(
        minute=59,
        hour=11,
        second=second,
    ).isoformat().replace("+00:00", "Z")


def _create_market_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE price_events (
                id INTEGER PRIMARY KEY,
                event_time_utc TEXT NOT NULL,
                instrument TEXT NOT NULL,
                market_label TEXT NOT NULL,
                settlement_term TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                event_type TEXT NOT NULL,
                side TEXT,
                price_num REAL NOT NULL,
                price_unit TEXT NOT NULL,
                parse_confidence REAL NOT NULL,
                quantity_num REAL
            );
            CREATE TABLE external_market_observations (
                id INTEGER PRIMARY KEY,
                observed_at_utc TEXT NOT NULL,
                instrument_code TEXT NOT NULL,
                quote_kind TEXT NOT NULL,
                normalized_price_num REAL,
                interval_seconds INTEGER NOT NULL DEFAULT 0,
                volume_value REAL
            );
            """
        )
        rows = []
        identifier = 1
        series = (
            (
                "MELTED_GOLD",
                "آبشده نقدی",
                "TODAY",
                "PHYSICAL",
                78_000_000,
                78_200_000,
            ),
            (
                "MELTED_GOLD",
                "آبشده فردایی",
                "TOMORROW",
                "PAPER",
                78_300_000,
                78_600_000,
            ),
            (
                "USD_HERAT",
                "دلار هرات نامشخص فیزیکی",
                "UNKNOWN",
                "PHYSICAL",
                100_000,
                100_200,
            ),
            (
                "USD_HERAT",
                "دلار هرات فردایی",
                "TOMORROW",
                "PAPER",
                100_400,
                100_700,
            ),
            (
                "XAUUSD",
                "اونس جهانی",
                "UNKNOWN",
                "PAPER",
                3_390,
                3_400,
            ),
            (
                "GOLD_COIN",
                "سکه نقدی",
                "UNKNOWN",
                "PHYSICAL",
                184_800_000,
                185_000_000,
            ),
            (
                "GOLD_COIN",
                "سکه حواله",
                "UNKNOWN",
                "PAPER",
                186_800_000,
                187_000_000,
            ),
        )
        for instrument, label, settlement, form, first, second in series:
            for observed_second, price in ((10, first), (50, second)):
                rows.append(
                    (
                        identifier,
                        _stamp(observed_second),
                        instrument,
                        label,
                        settlement,
                        form,
                        "OFFER",
                        "BUY",
                        price,
                        (
                            "IRT_PER_MESGHAL_750"
                            if instrument == "MELTED_GOLD"
                            else "NORMALIZED_TEST_UNIT"
                        ),
                        0.99,
                        10,
                    )
                )
                identifier += 1
        connection.executemany(
            """
            INSERT INTO price_events(
                id, event_time_utc, instrument, market_label,
                settlement_term, trade_form, event_type, side,
                price_num, price_unit, parse_confidence, quantity_num
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.executemany(
            """
            INSERT INTO external_market_observations(
                observed_at_utc, instrument_code, quote_kind,
                normalized_price_num, interval_seconds, volume_value
            ) VALUES (?, ?, ?, ?, 0, 1)
            """,
            [
                (_stamp(10), "USDT_IRT", "MID", 100_100),
                (_stamp(50), "USDT_IRT", "MID", 100_300),
                (_stamp(10), "IME_GOLD_BAR", "LAST", 78_000_000),
                (_stamp(50), "IME_GOLD_BAR", "LAST", 78_100_000),
            ],
        )
        connection.commit()
    finally:
        connection.close()


class StaticProvider:
    def __init__(self, value: dict) -> None:
        self.value = value

    def load(self) -> dict:
        return self.value


class CoinSnapshotProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = load_runtime_bundle()
        self.producer = CoinSnapshotProducer(self.bundle)

    def test_offline_snapshot_is_deterministic_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_db = Path(directory) / "market.sqlite3"
            _create_market_database(market_db)

            first = self.producer.build(
                market_db=market_db,
                as_of=CUTOFF,
            )
            second = self.producer.build(
                market_db=market_db,
                as_of=CUTOFF,
            )

        self.assertEqual(first, second)
        self.assertEqual(
            first["generated_at_utc"],
            "2026-07-23T12:00:00Z",
        )
        self.assertEqual(
            first["bundle_version"],
            self.bundle.bundle_version,
        )
        self.assertEqual(
            first["interval_contract"],
            {
                "status": "SHADOW_OPERATIONAL_BAND_ONLY",
                "operational_target_coverage": 0.8,
                "audit_target_coverage": 0.95,
                "audit_envelope_available": False,
            },
        )
        cash = first["settlements"]["CASH"]
        tomorrow = first["settlements"]["TOMORROW"]
        self.assertEqual(
            cash["inputs"]["melted_gold"]["selected_trade_form"],
            "PHYSICAL",
        )
        self.assertEqual(
            tomorrow["inputs"]["melted_gold"]["selected_trade_form"],
            "PAPER",
        )
        self.assertEqual(
            cash["inputs"]["generic_coin"]["selected_market_label"],
            "سکه نقدی",
        )
        self.assertEqual(
            cash["inputs"]["generic_coin"]["selected_settlement_term"],
            "UNKNOWN",
        )
        self.assertEqual(
            tomorrow["inputs"]["generic_coin"]["selected_market_label"],
            "سکه حواله",
        )
        self.assertEqual(
            tomorrow["inputs"]["generic_coin"]["selected_trade_form"],
            "PAPER",
        )
        self.assertEqual(
            {
                row["commodity_name"]
                for row in cash["rates"]
            },
            set(self.bundle.canonical_commodity_names),
        )
        self.assertTrue(
            all("commodity_id" not in row for row in cash["rates"])
        )
        low_date_cash = next(
            row
            for row in cash["rates"]
            if row["commodity_name"] == "بهار"
        )
        low_date_tomorrow = next(
            row
            for row in tomorrow["rates"]
            if row["commodity_name"] == "بهار"
        )
        self.assertEqual(
            low_date_cash["method"],
            "LOW_DATE_PHYSICAL_MELTED_INTRINSIC_FALLBACK",
        )
        self.assertEqual(
            low_date_tomorrow["estimated_project_price"],
            low_date_cash["estimated_project_price"],
        )
        one_gram = next(
            row
            for row in cash["rates"]
            if row["commodity_name"] == "یک گرمی"
        )
        self.assertIn(
            "LAST_TRUSTED_COIN_ANCHOR",
            one_gram["method"],
        )
        self.assertEqual(one_gram["status"], "ESTIMATED")
        self.assertIsNotNone(one_gram["estimated_project_price"])

    def test_strict_cutoff_ignores_future_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_db = Path(directory) / "market.sqlite3"
            _create_market_database(market_db)
            before = self.producer.build(
                market_db=market_db,
                as_of=CUTOFF,
            )
            connection = sqlite3.connect(market_db)
            try:
                connection.execute(
                    """
                    INSERT INTO price_events(
                        event_time_utc, instrument, market_label,
                        settlement_term, trade_form, event_type, side,
                        price_num, price_unit, parse_confidence, quantity_num
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "2026-07-23T12:05:00Z",
                        "MELTED_GOLD",
                        "آبشده نقدی",
                        "TODAY",
                        "PHYSICAL",
                        "TRADE",
                        "BUY",
                        999_000_000,
                        "IRT_PER_MESGHAL_750",
                        1.0,
                        1,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            after = self.producer.build(
                market_db=market_db,
                as_of=CUTOFF,
            )

        self.assertEqual(after, before)

    def test_external_market_table_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_db = Path(directory) / "market.sqlite3"
            _create_market_database(market_db)
            connection = sqlite3.connect(market_db)
            try:
                connection.execute(
                    "DROP TABLE external_market_observations"
                )
                connection.commit()
            finally:
                connection.close()

            snapshot = self.producer.build(
                market_db=market_db,
                as_of=CUTOFF,
            )

        self.assertEqual(
            snapshot["input_mode"],
            "OFFLINE_NORMALIZED_SQLITE",
        )
        self.assertEqual(
            snapshot["settlements"]["CASH"]["rates"][0][
                "commodity_name"
            ],
            "امام",
        )

    def test_snapshot_freshness_is_source_cutoff_not_execution_time(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_db = Path(directory) / "market.sqlite3"
            _create_market_database(market_db)
            snapshot = self.producer.build(
                market_db=market_db,
                as_of=CUTOFF,
            )
        imam = next(
            row
            for row in snapshot["settlements"]["CASH"]["rates"]
            if row["commodity_name"] == "امام"
        )
        service = CoinIntelligenceShadowService(
            snapshot_provider=StaticProvider(snapshot),
            bundle=self.bundle,
        )

        observation = service.observe_implicit_commodity(
            price=imam["estimated_project_price"],
            settlement="cash",
            current_commodity="امام",
            now=CUTOFF + timedelta(days=1),
        )

        self.assertEqual(observation.status, "STALE_INPUT")
        self.assertIsNone(observation.inferred_commodity)

    def test_naive_cutoff_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_db = Path(directory) / "market.sqlite3"
            _create_market_database(market_db)

            with self.assertRaisesRegex(
                SnapshotProducerError,
                "snapshot_as_of_timezone_required",
            ):
                self.producer.build(
                    market_db=market_db,
                    as_of=datetime(2026, 7, 23, 12, 0),
                )

    def test_atomic_writer_replaces_complete_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.json"
            write_snapshot_atomic(output, {"version": 1})
            write_snapshot_atomic(output, {"version": 2})

            payload = json.loads(output.read_text(encoding="utf-8"))
            leftovers = list(output.parent.glob(".*.tmp"))

        self.assertEqual(payload, {"version": 2})
        self.assertEqual(leftovers, [])

    def test_atomic_writer_rejects_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.json"
            target.write_text("{}", encoding="utf-8")
            output = Path(directory) / "snapshot.json"
            output.symlink_to(target)

            with self.assertRaisesRegex(
                SnapshotProducerError,
                "snapshot_destination_symlink_forbidden",
            ):
                write_snapshot_atomic(output, {"version": 2})

    def test_missing_normalized_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market_db = Path(directory) / "market.sqlite3"
            connection = sqlite3.connect(market_db)
            connection.execute(
                "CREATE TABLE price_events(id INTEGER PRIMARY KEY)"
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(
                SnapshotProducerError,
                "price_events_columns_missing",
            ):
                self.producer.build(
                    market_db=market_db,
                    as_of=CUTOFF,
                )


if __name__ == "__main__":
    unittest.main()
