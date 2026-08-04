"""Versioned, local SQLite store for normalized market observations.

It is intentionally independent from the application's PostgreSQL migrations:
PostgreSQL remains authoritative for product transactions, while this bounded
store accepts only privacy-minimized market facts.  Project events will reach
it through a durable PostgreSQL outbox in P3, not through an ORM callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Iterator

from .market_contracts import (
    MARKET_STORE_CONTRACT_VERSION,
    MarketObservation,
    MarketStoreContractError,
    NormalizedMarketObservation,
    derive_event_key,
)


MARKET_STORE_SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS market_store_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    contract_version INTEGER NOT NULL,
    initialized_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_observations (
    id INTEGER PRIMARY KEY,
    event_key BLOB NOT NULL UNIQUE CHECK(length(event_key) BETWEEN 16 AND 64),
    source_code TEXT NOT NULL,
    source_family TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    tehran_datetime TEXT NOT NULL,
    tehran_date TEXT NOT NULL,
    tehran_minute TEXT NOT NULL,
    tehran_weekday INTEGER NOT NULL CHECK(tehran_weekday BETWEEN 0 AND 6),
    instrument TEXT NOT NULL,
    market_label TEXT NOT NULL,
    settlement_term TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    event_type TEXT NOT NULL,
    side TEXT NOT NULL,
    price_value TEXT NOT NULL,
    price_num REAL NOT NULL CHECK(price_num > 0),
    price_unit TEXT NOT NULL,
    currency TEXT NOT NULL,
    quantity_value TEXT,
    quantity_num REAL,
    quantity_unit TEXT,
    parse_confidence REAL NOT NULL
        CHECK(parse_confidence >= 0 AND parse_confidence <= 1),
    parser_version TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    quality_policy_version TEXT NOT NULL,
    is_conditional INTEGER NOT NULL CHECK(is_conditional IN (0, 1)),
    attributes_json TEXT NOT NULL,
    inserted_at_utc TEXT NOT NULL,
    CHECK(
        (quantity_value IS NULL AND quantity_num IS NULL AND quantity_unit IS NULL)
        OR (quantity_value IS NOT NULL AND quantity_num > 0 AND quantity_unit IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_market_observations_dimensions_time
    ON market_observations(
        instrument, settlement_term, trade_form, event_type, side, event_time_utc
    );
CREATE INDEX IF NOT EXISTS idx_market_observations_source_available
    ON market_observations(source_code, available_at_utc);
CREATE INDEX IF NOT EXISTS idx_market_observations_quality_time
    ON market_observations(quality_state, event_time_utc);

-- This compatibility projection retains the established external-market name
-- without creating a second writable schema.  P2-D writes through the one
-- canonical MarketObservation contract and reads this view where useful.
CREATE VIEW IF NOT EXISTS external_market_observations AS
SELECT
    id,
    event_key,
    source_code,
    event_time_utc AS observed_at_utc,
    available_at_utc,
    instrument AS instrument_code,
    price_value AS normalized_price_value,
    price_num AS normalized_price_num,
    price_unit AS normalized_price_unit,
    currency AS normalized_currency,
    event_type,
    side AS quote_kind,
    quality_state,
    quality_policy_version
FROM market_observations
WHERE source_family = 'EXTERNAL_MARKET';
"""


class MarketStoreError(RuntimeError):
    """Base error for local Market Store lifecycle failures."""


class MarketStoreMigrationRequired(MarketStoreError):
    """Raised rather than guessing how a legacy SQLite schema should upgrade."""


@dataclass(frozen=True, slots=True)
class LegacyUpgradeReport:
    """Privacy-safe result of an explicit one-way legacy import."""

    imported_price_events: int
    imported_external_observations: int
    skipped_unsupported_rows: int


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _view_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
        (name,),
    ).fetchone() is not None


def connect_market_store(path: Path | str) -> sqlite3.Connection:
    """Connect to the local market store.  Call ``initialize_market_store`` once."""

    database = Path(path).expanduser()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_market_store(connection: sqlite3.Connection) -> None:
    """Initialize a fresh canonical database, or verify an existing one.

    A database containing either historical ``price_events`` layout is rejected
    until it is imported explicitly into a separate canonical destination.  It
    is unsafe to infer provenance or conversion rules inside an in-place DDL
    migration, and no legacy table is ever dropped by this function.
    """

    if _table_exists(connection, "market_store_metadata"):
        row = connection.execute(
            "SELECT schema_version, contract_version "
            "FROM market_store_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise MarketStoreError("market_store_metadata_missing_singleton")
        if int(row["schema_version"]) != MARKET_STORE_SCHEMA_VERSION:
            raise MarketStoreMigrationRequired("market_store_schema_upgrade_required")
        if int(row["contract_version"]) != MARKET_STORE_CONTRACT_VERSION:
            raise MarketStoreMigrationRequired("market_store_contract_upgrade_required")
        if not _table_exists(connection, "market_observations") or not _view_exists(
            connection,
            "external_market_observations",
        ):
            raise MarketStoreError("market_store_schema_incomplete")
        return
    if _table_exists(connection, "price_events") or _table_exists(
        connection,
        "external_market_observations",
    ):
        raise MarketStoreMigrationRequired(
            "market_store_legacy_schema_requires_explicit_import"
        )
    connection.executescript(_SCHEMA)
    connection.execute(
        """
        INSERT INTO market_store_metadata(
            singleton, schema_version, contract_version, initialized_at_utc
        ) VALUES (1, ?, ?, ?)
        """,
        (MARKET_STORE_SCHEMA_VERSION, MARKET_STORE_CONTRACT_VERSION, _utc_now()),
    )
    connection.commit()


def upsert_observation(
    connection: sqlite3.Connection,
    observation: MarketObservation,
) -> int:
    """Insert/update one fact by opaque key; callers own transaction boundaries."""

    normalized = observation.normalized()
    cursor = connection.execute(
        """
        INSERT INTO market_observations(
            event_key, source_code, source_family, event_time_utc,
            available_at_utc, tehran_datetime, tehran_date, tehran_minute,
            tehran_weekday, instrument, market_label, settlement_term,
            trade_form, event_type, side, price_value, price_num, price_unit,
            currency, quantity_value, quantity_num, quantity_unit,
            parse_confidence, parser_version, quality_state,
            quality_policy_version, is_conditional, attributes_json,
            inserted_at_utc
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(event_key) DO UPDATE SET
            source_code = excluded.source_code,
            source_family = excluded.source_family,
            event_time_utc = excluded.event_time_utc,
            available_at_utc = excluded.available_at_utc,
            tehran_datetime = excluded.tehran_datetime,
            tehran_date = excluded.tehran_date,
            tehran_minute = excluded.tehran_minute,
            tehran_weekday = excluded.tehran_weekday,
            instrument = excluded.instrument,
            market_label = excluded.market_label,
            settlement_term = excluded.settlement_term,
            trade_form = excluded.trade_form,
            event_type = excluded.event_type,
            side = excluded.side,
            price_value = excluded.price_value,
            price_num = excluded.price_num,
            price_unit = excluded.price_unit,
            currency = excluded.currency,
            quantity_value = excluded.quantity_value,
            quantity_num = excluded.quantity_num,
            quantity_unit = excluded.quantity_unit,
            parse_confidence = excluded.parse_confidence,
            parser_version = excluded.parser_version,
            quality_state = excluded.quality_state,
            quality_policy_version = excluded.quality_policy_version,
            is_conditional = excluded.is_conditional,
            attributes_json = excluded.attributes_json,
            inserted_at_utc = excluded.inserted_at_utc
        """,
        _storage_values(normalized),
    )
    return int(cursor.lastrowid or 0)


def _storage_values(observation: NormalizedMarketObservation) -> tuple[object, ...]:
    quantity_value = str(observation.quantity) if observation.quantity is not None else None
    quantity_num = float(observation.quantity) if observation.quantity is not None else None
    return (
        observation.event_key,
        observation.source_code,
        observation.source_family,
        observation.event_time_utc,
        observation.available_at_utc,
        observation.tehran_datetime,
        observation.tehran_date,
        observation.tehran_minute,
        observation.tehran_weekday,
        observation.instrument,
        observation.market_label,
        observation.settlement_term,
        observation.trade_form,
        observation.event_type,
        observation.side,
        str(observation.price),
        float(observation.price),
        observation.price_unit,
        observation.currency,
        quantity_value,
        quantity_num,
        observation.quantity_unit,
        observation.parse_confidence,
        observation.parser_version,
        observation.quality_state,
        observation.quality_policy_version,
        int(observation.is_conditional),
        observation.attributes_json,
        _utc_now(),
    )


def _legacy_connection(path: Path | str) -> sqlite3.Connection:
    database = Path(path).expanduser().resolve()
    if not database.exists():
        raise MarketStoreMigrationRequired("legacy_market_store_not_found")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _legacy_source_family(source_code: str) -> str:
    code = source_code.upper()
    if code.startswith("GROUP_") or code.startswith("ACCOUNT2_GROUP"):
        return "GROUP"
    if code.startswith("ACCOUNT1"):
        return "TELEGRAM_PRIVATE"
    return "TELEGRAM_PUBLIC"


def _legacy_trade_form(value: str) -> str:
    normalized = value.strip().upper()
    return {
        "CASH": "PHYSICAL",
        "PAPER": "PAPER_NORMAL",
        "PAPER_NORMAL": "PAPER_NORMAL",
        "PAPER_REVERSE": "PAPER_REVERSE",
        "PAPER_SWIM": "PAPER_SWIM",
        "PHYSICAL": "PHYSICAL",
        "UNKNOWN": "UNKNOWN",
    }.get(normalized, "UNKNOWN")


def _legacy_settlement(value: str) -> str:
    normalized = value.strip().upper()
    return {
        "CASH": "CASH",
        "TODAY": "TODAY",
        "TOMORROW": "TOMORROW",
        "UNKNOWN": "UNKNOWN",
    }.get(normalized, "UNKNOWN")


def _legacy_quote_side(value: str) -> str:
    normalized = value.strip().upper()
    return normalized if normalized in {"BUY", "SELL", "MID"} else "UNKNOWN"


def _legacy_price_unit(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    aliases = {
        "PROJECT_THOUSAND_TOMAN": "PROJECT_THOUSAND_TOMAN",
        "IRT_PER_COIN": "IRT_PER_COIN",
        "IRT_PER_MESGHAL_750": "IRT_PER_MESGHAL_750",
        "IRT_PER_GRAM_750": "IRT_PER_GRAM_750",
        "IRT_PER_USD": "IRT_PER_USD",
        "IRT_PER_USDT": "IRT_PER_USDT",
        "USD_PER_TROY_OUNCE": "USD_PER_TROY_OUNCE",
    }
    return aliases.get(normalized)


def _yield_legacy_price_observations(
    source: sqlite3.Connection,
) -> Iterator[MarketObservation]:
    if not (_table_exists(source, "raw_posts") and _table_exists(source, "price_events")):
        return
    rows = source.execute(
        """
        SELECT
            raw_posts.source_code AS source_code,
            price_events.raw_post_id AS raw_post_id,
            price_events.event_index AS event_index,
            price_events.instrument AS instrument,
            price_events.market_label AS market_label,
            price_events.settlement_term AS settlement_term,
            price_events.trade_form AS trade_form,
            price_events.event_type AS event_type,
            price_events.side AS side,
            price_events.price_num AS price_num,
            price_events.currency AS currency,
            price_events.price_unit AS price_unit,
            price_events.quantity_num AS quantity_num,
            price_events.quantity_unit AS quantity_unit,
            price_events.event_time_utc AS event_time_utc,
            price_events.parse_confidence AS parse_confidence,
            price_events.parser_version AS parser_version
        FROM price_events
        JOIN raw_posts ON raw_posts.id = price_events.raw_post_id
        ORDER BY price_events.id
        """
    )
    for row in rows:
        price_unit = _legacy_price_unit(row["price_unit"])
        if price_unit is None:
            continue
        yield MarketObservation(
            event_key=derive_event_key(
                "legacy-price-event-v1",
                row["source_code"],
                row["raw_post_id"],
                row["event_index"],
            ),
            source_code=row["source_code"],
            source_family=_legacy_source_family(row["source_code"]),
            event_time_utc=row["event_time_utc"],
            available_at_utc=row["event_time_utc"],
            instrument=row["instrument"],
            market_label=row["market_label"],
            settlement_term=_legacy_settlement(row["settlement_term"]),
            trade_form=_legacy_trade_form(row["trade_form"]),
            event_type=row["event_type"],
            side=_legacy_quote_side(row["side"]),
            price=row["price_num"],
            price_unit=price_unit,
            currency=row["currency"],
            quantity=row["quantity_num"],
            quantity_unit=row["quantity_unit"],
            parse_confidence=float(row["parse_confidence"]),
            parser_version=row["parser_version"],
            quality_state="ELIGIBLE",
            quality_policy_version="legacy-import-v1",
        )


def _yield_legacy_external_observations(
    source: sqlite3.Connection,
) -> Iterator[MarketObservation]:
    if not (
        _table_exists(source, "external_instruments")
        and _table_exists(source, "external_market_observations")
    ):
        return
    rows = source.execute(
        """
        SELECT
            external_market_observations.id AS observation_id,
            external_market_observations.observed_at_utc AS observed_at_utc,
            external_market_observations.quote_kind AS quote_kind,
            external_market_observations.normalized_price_num AS normalized_price_num,
            external_instruments.code AS instrument_code,
            external_instruments.source AS source_code,
            external_instruments.normalized_currency AS normalized_currency,
            external_instruments.normalized_unit AS normalized_unit
        FROM external_market_observations
        JOIN external_instruments
          ON external_instruments.code = external_market_observations.instrument_code
        ORDER BY external_market_observations.id
        """
    )
    for row in rows:
        price_unit = _legacy_price_unit(row["normalized_unit"])
        if row["normalized_price_num"] is None or price_unit is None:
            continue
        yield MarketObservation(
            event_key=derive_event_key(
                "legacy-external-observation-v1",
                row["source_code"],
                row["observation_id"],
            ),
            source_code=row["source_code"],
            source_family="EXTERNAL_MARKET",
            event_time_utc=row["observed_at_utc"],
            available_at_utc=row["observed_at_utc"],
            instrument=row["instrument_code"],
            market_label="EXTERNAL_REFERENCE",
            settlement_term="UNKNOWN",
            trade_form="UNKNOWN",
            event_type="REFERENCE",
            side=_legacy_quote_side(row["quote_kind"]),
            price=row["normalized_price_num"],
            price_unit=price_unit,
            currency=row["normalized_currency"] or "IRT",
            parse_confidence=1.0,
            parser_version="legacy-external-import-v1",
            quality_state="ELIGIBLE",
            quality_policy_version="legacy-import-v1",
        )


def upgrade_legacy_market_store(
    *,
    source_path: Path | str,
    destination_path: Path | str,
) -> LegacyUpgradeReport:
    """Explicitly copy safe legacy facts into a fresh canonical store.

    The source is opened read-only.  Unsupported/un-normalized legacy units are
    skipped rather than guessed, and raw text/IDs never appear in the target.
    The caller must retire the old store only after validating this report.
    """

    source_database = Path(source_path).expanduser().resolve()
    destination_database = Path(destination_path).expanduser().resolve()
    if source_database == destination_database:
        raise MarketStoreMigrationRequired("legacy_destination_must_be_separate")

    source = _legacy_connection(source_database)
    destination = connect_market_store(destination_database)
    try:
        initialize_market_store(destination)
        if not _table_exists(source, "price_events"):
            raise MarketStoreMigrationRequired("legacy_price_events_table_missing")
        if not _table_exists(source, "raw_posts"):
            raise MarketStoreMigrationRequired(
                "legacy_collector_schema_requires_replay"
            )
        total_price_events = int(
            source.execute(
                """
                SELECT COUNT(*)
                FROM price_events
                JOIN raw_posts ON raw_posts.id = price_events.raw_post_id
                """
            ).fetchone()[0]
        )
        total_external_observations = 0
        if _table_exists(source, "external_instruments") and _table_exists(
            source,
            "external_market_observations",
        ):
            total_external_observations = int(
                source.execute(
                    """
                    SELECT COUNT(*)
                    FROM external_market_observations
                    JOIN external_instruments
                      ON external_instruments.code =
                         external_market_observations.instrument_code
                    """
                ).fetchone()[0]
            )
        imported_price_events = 0
        imported_external_observations = 0
        for observation in _yield_legacy_price_observations(source):
            try:
                upsert_observation(destination, observation)
            except MarketStoreContractError:
                continue
            else:
                imported_price_events += 1
        for observation in _yield_legacy_external_observations(source):
            try:
                upsert_observation(destination, observation)
            except MarketStoreContractError:
                continue
            else:
                imported_external_observations += 1
        destination.commit()
        return LegacyUpgradeReport(
            imported_price_events=imported_price_events,
            imported_external_observations=imported_external_observations,
            skipped_unsupported_rows=(
                total_price_events
                + total_external_observations
                - imported_price_events
                - imported_external_observations
            ),
        )
    finally:
        source.close()
        destination.close()
