"""Safety tests for the P1 canonical Market Store contract."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.market_intelligence.market_contracts import (
    MarketObservation,
    MarketStoreContractError,
    derive_event_key,
)
from core.market_intelligence.market_store import (
    MarketStoreMigrationRequired,
    connect_market_store,
    initialize_market_store,
    upgrade_legacy_market_store,
    upsert_observation,
)


def _observation(**overrides: object) -> MarketObservation:
    values: dict[str, object] = {
        "event_key": derive_event_key("test", "one"),
        "source_code": "GROUP_1",
        "source_family": "GROUP",
        "event_time_utc": "2026-08-04T05:35:30Z",
        "available_at_utc": "2026-08-04T05:35:31Z",
        "instrument": "COIN_IMAM",
        "market_label": "COIN_MARKET",
        "settlement_term": "CASH",
        "trade_form": "PHYSICAL",
        "event_type": "OFFER",
        "side": "BUY",
        "price": "186900",
        "price_unit": "PROJECT_THOUSAND_TOMAN",
        "currency": "IRT",
        "quantity": "5",
        "quantity_unit": "PIECE",
        "parse_confidence": 0.98,
        "parser_version": "group-parser-v3",
        "quality_state": "ELIGIBLE",
        "quality_policy_version": "quality-v2",
        "attributes": {"agreement_price_changed": False},
    }
    values.update(overrides)
    return MarketObservation(**values)  # type: ignore[arg-type]


class MarketStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.database = Path(self._tmpdir.name) / "market.sqlite3"
        self.connection = connect_market_store(self.database)
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self._tmpdir.cleanup()

    def test_persists_tehran_dimensions_and_opaque_deduplication(self) -> None:
        upsert_observation(self.connection, _observation())
        upsert_observation(
            self.connection,
            _observation(price="187000", available_at_utc="2026-08-04T05:36:01Z"),
        )
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT event_key, price_value, tehran_datetime, tehran_date,
                   tehran_minute, tehran_weekday, source_family
            FROM market_observations
            """
        ).fetchone()
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
            1,
        )
        self.assertEqual(row["price_value"], "187000")
        self.assertEqual(row["tehran_datetime"], "2026-08-04T09:05:30+03:30")
        self.assertEqual(row["tehran_date"], "2026-08-04")
        self.assertEqual(row["tehran_minute"], "09:05")
        self.assertEqual(row["tehran_weekday"], 1)
        self.assertEqual(row["source_family"], "GROUP")
        self.assertEqual(len(row["event_key"]), 32)

    def test_rejects_naive_time_private_attributes_and_unit_mismatch(self) -> None:
        with self.assertRaisesRegex(
            MarketStoreContractError,
            "event_time_utc_timezone_required",
        ):
            _observation(event_time_utc="2026-08-04T05:35:30").normalized()
        with self.assertRaisesRegex(
            MarketStoreContractError,
            "attributes_contains_private_identity",
        ):
            _observation(attributes={"raw_text": "never store me"}).normalized()
        with self.assertRaisesRegex(
            MarketStoreContractError,
            "instrument_price_unit_mismatch",
        ):
            _observation(
                instrument="MELTED_GOLD_FLOW",
                price_unit="TOMAN_PER_GRAM_750",
            ).normalized()
        with self.assertRaisesRegex(
            MarketStoreContractError,
            "event_key_opaque_digest_required",
        ):
            _observation(event_key=b"short").normalized()
        with self.assertRaisesRegex(
            MarketStoreContractError,
            "is_conditional_boolean_required",
        ):
            _observation(is_conditional="false").normalized()

    def test_external_projection_is_a_view_over_the_one_canonical_table(self) -> None:
        upsert_observation(
            self.connection,
            _observation(
                event_key=derive_event_key("external", "herat"),
                source_code="HERAT_FEED",
                source_family="EXTERNAL_MARKET",
                instrument="USD_HERAT",
                market_label="EXTERNAL_REFERENCE",
                settlement_term="UNKNOWN",
                trade_form="UNKNOWN",
                event_type="REFERENCE",
                side="MID",
                price="895000",
                price_unit="TOMAN_PER_USD",
                quantity=None,
                quantity_unit=None,
            ),
        )
        self.connection.commit()

        schema_row = self.connection.execute(
            """
            SELECT type FROM sqlite_master
            WHERE name = 'external_market_observations'
            """
        ).fetchone()
        view_row = self.connection.execute(
            """
            SELECT instrument_code, normalized_price_value, quote_kind
            FROM external_market_observations
            """
        ).fetchone()
        self.assertEqual(schema_row["type"], "view")
        self.assertEqual(view_row["instrument_code"], "USD_HERAT")
        self.assertEqual(view_row["normalized_price_value"], "895000")
        self.assertEqual(view_row["quote_kind"], "MID")

    def test_spot_ounce_has_explicit_non_trade_dimensions(self) -> None:
        normalized = _observation(
            event_key=derive_event_key("external", "xauusd"),
            source_code="XAUUSD_FEED",
            source_family="TELEGRAM_PUBLIC",
            instrument="XAUUSD",
            market_label="GLOBAL_SPOT",
            settlement_term="SPOT",
            trade_form="NOT_APPLICABLE",
            event_type="QUOTE",
            side="MID",
            price="4538.39",
            price_unit="USD_PER_TROY_OUNCE",
            currency="USD",
            quantity=None,
            quantity_unit=None,
        ).normalized()
        self.assertEqual(normalized.settlement_term, "SPOT")
        self.assertEqual(normalized.trade_form, "NOT_APPLICABLE")

    def test_legacy_schema_never_auto_upgrades_in_place(self) -> None:
        self.connection.close()
        legacy_path = Path(self._tmpdir.name) / "legacy.sqlite3"
        legacy = sqlite3.connect(legacy_path)
        legacy.execute("CREATE TABLE price_events (id INTEGER PRIMARY KEY)")
        legacy.commit()
        legacy.close()

        connection = connect_market_store(legacy_path)
        try:
            with self.assertRaisesRegex(
                MarketStoreMigrationRequired,
                "explicit_import",
            ):
                initialize_market_store(connection)
        finally:
            connection.close()
        self.connection = connect_market_store(self.database)


class LegacyMarketStoreUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.source = root / "legacy.sqlite3"
        self.destination = root / "canonical.sqlite3"
        self._create_legacy_fixture(self.source)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @staticmethod
    def _create_legacy_fixture(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE raw_posts (
                id INTEGER PRIMARY KEY,
                source_code TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                published_at_utc TEXT NOT NULL,
                raw_text TEXT NOT NULL
            );
            CREATE TABLE price_events (
                id INTEGER PRIMARY KEY,
                raw_post_id INTEGER NOT NULL,
                event_index INTEGER NOT NULL,
                instrument TEXT NOT NULL,
                market_label TEXT NOT NULL,
                settlement_term TEXT NOT NULL,
                trade_form TEXT NOT NULL,
                event_type TEXT NOT NULL,
                side TEXT NOT NULL,
                price_num REAL NOT NULL,
                currency TEXT NOT NULL,
                price_unit TEXT NOT NULL,
                quantity_num REAL,
                quantity_unit TEXT,
                event_time_utc TEXT NOT NULL,
                parse_confidence REAL NOT NULL,
                parser_version TEXT NOT NULL
            );
            CREATE TABLE external_instruments (
                code TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                normalized_currency TEXT,
                normalized_unit TEXT
            );
            CREATE TABLE external_market_observations (
                id INTEGER PRIMARY KEY,
                instrument_code TEXT NOT NULL,
                observed_at_utc TEXT NOT NULL,
                quote_kind TEXT NOT NULL,
                normalized_price_num REAL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO raw_posts(id, source_code, message_id, published_at_utc, raw_text)
            VALUES (1, 'GROUP_1', 1234, '2026-08-04T05:35:30Z', 'private raw text')
            """
        )
        connection.execute(
            """
            INSERT INTO price_events(
                id, raw_post_id, event_index, instrument, market_label,
                settlement_term, trade_form, event_type, side, price_num,
                currency, price_unit, quantity_num, quantity_unit,
                event_time_utc, parse_confidence, parser_version
            ) VALUES (
                1, 1, 0, 'COIN_IMAM', 'COIN_MARKET', 'CASH', 'PHYSICAL',
                'OFFER', 'BUY', 186900, 'IRT', 'PROJECT_THOUSAND_TOMAN',
                5, 'PIECE', '2026-08-04T05:35:30Z', 0.97, 'legacy-v1'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO external_instruments(
                code, source, normalized_currency, normalized_unit
            ) VALUES ('USD_HERAT', 'HERAT_FEED', 'TOMAN', 'TOMAN_PER_USD')
            """
        )
        connection.execute(
            """
            INSERT INTO external_market_observations(
                id, instrument_code, observed_at_utc, quote_kind, normalized_price_num
            ) VALUES (1, 'USD_HERAT', '2026-08-04T05:35:31Z', 'MID', 895000)
            """
        )
        connection.commit()
        connection.close()

    def test_explicit_upgrade_is_read_only_for_source_and_strips_raw_identity(self) -> None:
        report = upgrade_legacy_market_store(
            source_path=self.source,
            destination_path=self.destination,
        )
        self.assertEqual(report.imported_price_events, 1)
        self.assertEqual(report.imported_external_observations, 1)
        self.assertEqual(report.skipped_unsupported_rows, 0)

        source = sqlite3.connect(self.source)
        destination = sqlite3.connect(self.destination)
        try:
            self.assertEqual(source.execute("SELECT raw_text FROM raw_posts").fetchone()[0], "private raw text")
            target_tables = {
                row[0]
                for row in destination.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("market_observations", target_tables)
            self.assertNotIn("raw_posts", target_tables)
            self.assertEqual(
                destination.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
                2,
            )
            self.assertEqual(
                destination.execute("SELECT COUNT(*) FROM external_market_observations").fetchone()[0],
                1,
            )
        finally:
            source.close()
            destination.close()

    def test_destination_must_be_separate(self) -> None:
        with self.assertRaisesRegex(
            MarketStoreMigrationRequired,
            "destination_must_be_separate",
        ):
            upgrade_legacy_market_store(
                source_path=self.source,
                destination_path=self.source,
            )
