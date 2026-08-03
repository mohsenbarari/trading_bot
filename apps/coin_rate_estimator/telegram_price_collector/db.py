from __future__ import annotations

import sqlite3
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import ExternalMarketObservation, PriceEvent, RawPost


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    cutoff_utc TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    message_count INTEGER NOT NULL DEFAULT 0,
    parsed_event_count INTEGER NOT NULL DEFAULT 0,
    newest_message_id INTEGER,
    oldest_message_id INTEGER,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS raw_posts (
    id INTEGER PRIMARY KEY,
    source_code TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    published_at_utc TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'PENDING',
    UNIQUE(source_code, message_id)
);

CREATE TABLE IF NOT EXISTS price_events (
    id INTEGER PRIMARY KEY,
    raw_post_id INTEGER NOT NULL REFERENCES raw_posts(id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL,
    instrument TEXT NOT NULL,
    market_label TEXT NOT NULL,
    settlement_term TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    event_type TEXT NOT NULL,
    side TEXT NOT NULL,
    price_value TEXT NOT NULL,
    price_num REAL NOT NULL,
    currency TEXT NOT NULL,
    price_unit TEXT NOT NULL,
    quantity_value TEXT,
    quantity_num REAL,
    quantity_unit TEXT,
    movement TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    tehran_datetime TEXT NOT NULL,
    tehran_date TEXT NOT NULL,
    tehran_minute TEXT NOT NULL,
    tehran_weekday INTEGER NOT NULL,
    tehran_weekday_name TEXT NOT NULL,
    source_datetime_text TEXT,
    parse_method TEXT NOT NULL,
    parse_confidence REAL NOT NULL,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(raw_post_id, event_index)
);

CREATE TABLE IF NOT EXISTS minute_prices (
    id INTEGER PRIMARY KEY,
    minute_utc TEXT NOT NULL,
    instrument TEXT NOT NULL,
    market_label TEXT NOT NULL,
    settlement_term TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    event_type TEXT NOT NULL,
    side TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    rebuilt_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(
        minute_utc, instrument, market_label, settlement_term,
        trade_form, event_type, side
    )
);

CREATE TABLE IF NOT EXISTS external_collection_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    requested_from_utc TEXT,
    requested_to_utc TEXT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    observation_count INTEGER NOT NULL DEFAULT 0,
    error_text TEXT
);

CREATE TABLE IF NOT EXISTS external_instruments (
    code TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    raw_currency TEXT NOT NULL,
    raw_unit TEXT NOT NULL,
    raw_fineness_value TEXT,
    raw_weight_gram_value TEXT,
    normalized_currency TEXT,
    normalized_unit TEXT,
    normalized_fineness_value TEXT,
    normalized_weight_gram_value TEXT,
    conversion_formula TEXT NOT NULL,
    UNIQUE(source, symbol)
);

CREATE TABLE IF NOT EXISTS external_market_observations (
    id INTEGER PRIMARY KEY,
    instrument_code TEXT NOT NULL REFERENCES external_instruments(code),
    observed_at_utc TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL DEFAULT 0,
    quote_kind TEXT NOT NULL,
    raw_price_value TEXT NOT NULL,
    raw_price_num REAL NOT NULL,
    normalized_price_value TEXT,
    normalized_price_num REAL,
    volume_value TEXT,
    UNIQUE(instrument_code, observed_at_utc, interval_seconds, quote_kind)
);

CREATE INDEX IF NOT EXISTS idx_raw_posts_source_time
    ON raw_posts(source_code, published_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_posts_xau_minute
    ON raw_posts(source_code, substr(published_at_utc, 1, 16))
    WHERE source_code = 'XAUUSD';
CREATE INDEX IF NOT EXISTS idx_price_events_instrument_time
    ON price_events(instrument, event_time_utc);
CREATE INDEX IF NOT EXISTS idx_price_events_dimensions_time
    ON price_events(
        market_label, settlement_term, trade_form, event_type, side, event_time_utc
    );
CREATE INDEX IF NOT EXISTS idx_external_observations_instrument_time
    ON external_market_observations(instrument_code, observed_at_utc);

CREATE VIEW IF NOT EXISTS price_events_review AS
SELECT
    raw_posts.source_code,
    price_events.event_time_utc,
    price_events.tehran_datetime,
    price_events.tehran_date,
    price_events.tehran_minute,
    price_events.tehran_weekday,
    price_events.tehran_weekday_name,
    price_events.instrument,
    price_events.market_label,
    price_events.settlement_term,
    price_events.trade_form,
    price_events.event_type,
    price_events.side,
    price_events.price_value,
    price_events.currency,
    price_events.price_unit,
    price_events.quantity_value,
    price_events.quantity_unit,
    price_events.movement,
    price_events.parse_confidence,
    raw_posts.raw_text AS source_text
FROM price_events
JOIN raw_posts ON raw_posts.id = price_events.raw_post_id
;

CREATE VIEW IF NOT EXISTS external_market_review AS
SELECT
    external_instruments.source,
    external_instruments.code AS instrument,
    external_instruments.symbol,
    observed_at_utc,
    interval_seconds,
    quote_kind,
    raw_price_value,
    external_instruments.raw_currency,
    external_instruments.raw_unit,
    external_instruments.raw_fineness_value,
    external_instruments.raw_weight_gram_value,
    normalized_price_value,
    external_instruments.normalized_currency,
    external_instruments.normalized_unit,
    external_instruments.normalized_fineness_value,
    external_instruments.normalized_weight_gram_value,
    volume_value,
    external_instruments.conversion_formula
FROM external_market_observations
JOIN external_instruments
  ON external_instruments.code = external_market_observations.instrument_code;
"""


def connect(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def reset_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP VIEW IF EXISTS external_market_review;
        DROP VIEW IF EXISTS price_events_review;
        DROP TABLE IF EXISTS external_market_observations;
        DROP TABLE IF EXISTS external_instruments;
        DROP TABLE IF EXISTS external_collection_runs;
        DROP TABLE IF EXISTS minute_prices;
        DROP TABLE IF EXISTS price_events;
        DROP TABLE IF EXISTS raw_posts;
        DROP TABLE IF EXISTS collection_runs;
        DROP TABLE IF EXISTS scrape_runs;
        DROP TABLE IF EXISTS channels;
        """
    )
    connection.executescript(SCHEMA)
    connection.commit()
    connection.execute("VACUUM")


def start_external_collection_run(
    connection: sqlite3.Connection,
    *,
    source: str,
    mode: str,
    requested_from_utc: str | None = None,
    requested_to_utc: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO external_collection_runs(
            source, mode, requested_from_utc, requested_to_utc
        ) VALUES (?, ?, ?, ?)
        """,
        (source, mode, requested_from_utc, requested_to_utc),
    )
    connection.commit()
    return int(cursor.lastrowid)


def finish_external_collection_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    observation_count: int,
    error_text: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE external_collection_runs
        SET finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            status = ?, observation_count = ?, error_text = ?
        WHERE id = ?
        """,
        (status, observation_count, error_text, run_id),
    )
    connection.commit()


def upsert_external_observations(
    connection: sqlite3.Connection,
    observations: Iterable[ExternalMarketObservation],
) -> int:
    rows = list(observations)
    connection.executemany(
        """
        INSERT INTO external_instruments(
            code, source, symbol, raw_currency, raw_unit,
            raw_fineness_value, raw_weight_gram_value,
            normalized_currency, normalized_unit,
            normalized_fineness_value, normalized_weight_gram_value,
            conversion_formula
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            source = excluded.source,
            symbol = excluded.symbol,
            raw_currency = excluded.raw_currency,
            raw_unit = excluded.raw_unit,
            raw_fineness_value = excluded.raw_fineness_value,
            raw_weight_gram_value = excluded.raw_weight_gram_value,
            normalized_currency = excluded.normalized_currency,
            normalized_unit = excluded.normalized_unit,
            normalized_fineness_value = excluded.normalized_fineness_value,
            normalized_weight_gram_value = excluded.normalized_weight_gram_value,
            conversion_formula = excluded.conversion_formula
        """,
        [
            (
                row.instrument,
                row.source,
                row.symbol,
                row.raw_currency,
                row.raw_unit,
                _decimal_text(row.raw_fineness),
                _decimal_text(row.raw_weight_gram),
                row.normalized_currency,
                row.normalized_unit,
                _decimal_text(row.normalized_fineness),
                _decimal_text(row.normalized_weight_gram),
                row.conversion_formula,
            )
            for row in rows
        ],
    )
    connection.executemany(
        """
        INSERT INTO external_market_observations(
            instrument_code, observed_at_utc, interval_seconds,
            quote_kind, raw_price_value, raw_price_num,
            normalized_price_value, normalized_price_num,
            volume_value
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(instrument_code, observed_at_utc, interval_seconds, quote_kind)
        DO UPDATE SET
            raw_price_value = excluded.raw_price_value,
            raw_price_num = excluded.raw_price_num,
            normalized_price_value = excluded.normalized_price_value,
            normalized_price_num = excluded.normalized_price_num,
            volume_value = excluded.volume_value
        """,
        [
            (
                row.instrument,
                row.observed_at_utc,
                row.interval_seconds,
                row.quote_kind,
                _decimal_text(row.raw_price),
                float(row.raw_price),
                _decimal_text(row.normalized_price),
                float(row.normalized_price) if row.normalized_price is not None else None,
                _decimal_text(row.volume),
            )
            for row in rows
        ],
    )
    connection.commit()
    return len(rows)


def start_scrape_run(connection: sqlite3.Connection, source_code: str, cutoff_utc: str) -> int:
    cursor = connection.execute(
        "INSERT INTO collection_runs(source_code, cutoff_utc) VALUES (?, ?)",
        (source_code, cutoff_utc),
    )
    connection.commit()
    return int(cursor.lastrowid)


def finish_scrape_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    message_count: int,
    parsed_event_count: int,
    newest_message_id: int | None,
    oldest_message_id: int | None,
    error_text: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE collection_runs
        SET finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            status = ?, message_count = ?, parsed_event_count = ?,
            newest_message_id = ?, oldest_message_id = ?, error_text = ?
        WHERE id = ?
        """,
        (
            status,
            message_count,
            parsed_event_count,
            newest_message_id,
            oldest_message_id,
            error_text,
            run_id,
        ),
    )
    connection.commit()


def upsert_raw_post(
    connection: sqlite3.Connection,
    *,
    source_code: str,
    post: RawPost,
) -> int:
    if source_code == "XAUUSD":
        same_minute = connection.execute(
            """
            SELECT id FROM raw_posts
            WHERE source_code = 'XAUUSD'
              AND substr(published_at_utc, 1, 16) = substr(?, 1, 16)
            """,
            (post.published_at_utc,),
        ).fetchone()
        if same_minute is not None:
            connection.execute(
                """
                UPDATE raw_posts
                SET message_id = ?, published_at_utc = ?, raw_text = ?
                WHERE id = ?
                """,
                (
                    post.message_id,
                    post.published_at_utc,
                    post.raw_text,
                    int(same_minute["id"]),
                ),
            )
            return int(same_minute["id"])
    connection.execute(
        """
        INSERT INTO raw_posts(
            source_code, message_id, published_at_utc, raw_text
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_code, message_id) DO UPDATE SET
            published_at_utc = excluded.published_at_utc,
            raw_text = excluded.raw_text
        """,
        (
            source_code,
            post.message_id,
            post.published_at_utc,
            post.raw_text,
        ),
    )
    row = connection.execute(
        "SELECT id FROM raw_posts WHERE source_code = ? AND message_id = ?",
        (source_code, post.message_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Could not resolve raw post row after upsert")
    return int(row["id"])


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _tehran_time_parts(event_time_utc: str) -> tuple[str, str, str, int, str]:
    parsed = datetime.fromisoformat(event_time_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(ZoneInfo("Asia/Tehran"))
    weekday_names = (
        "دوشنبه",
        "سه‌شنبه",
        "چهارشنبه",
        "پنج‌شنبه",
        "جمعه",
        "شنبه",
        "یکشنبه",
    )
    return (
        local.isoformat(timespec="seconds"),
        local.date().isoformat(),
        local.strftime("%H:%M"),
        local.isoweekday(),
        weekday_names[local.weekday()],
    )


def replace_price_events(
    connection: sqlite3.Connection,
    *,
    raw_post_id: int,
    event_time_utc: str,
    events: Iterable[PriceEvent],
    empty_status: str = "UNMATCHED",
) -> int:
    event_list = list(events)
    tehran_datetime, tehran_date, tehran_minute, weekday, weekday_name = (
        _tehran_time_parts(event_time_utc)
    )
    connection.execute("DELETE FROM price_events WHERE raw_post_id = ?", (raw_post_id,))
    raw_post_exists = connection.execute(
        "SELECT 1 FROM raw_posts WHERE id = ?", (raw_post_id,)
    ).fetchone()
    if raw_post_exists is None:
        raise RuntimeError("Raw post does not exist")

    for index, event in enumerate(event_list):
        connection.execute(
            """
            INSERT INTO price_events(
                raw_post_id, event_index, instrument, market_label,
                settlement_term, trade_form, event_type, side,
                price_value, price_num, currency, price_unit,
                quantity_value, quantity_num, quantity_unit, movement,
                event_time_utc, tehran_datetime, tehran_date, tehran_minute,
                tehran_weekday, tehran_weekday_name, source_datetime_text, parse_method,
                parse_confidence, parser_version
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                raw_post_id,
                index,
                event.instrument,
                event.market_label,
                event.settlement_term,
                event.trade_form,
                event.event_type,
                event.side,
                _decimal_text(event.price),
                float(event.price),
                event.currency,
                event.price_unit,
                _decimal_text(event.quantity),
                float(event.quantity) if event.quantity is not None else None,
                event.quantity_unit,
                event.movement,
                event_time_utc,
                tehran_datetime,
                tehran_date,
                tehran_minute,
                weekday,
                weekday_name,
                event.source_datetime_text,
                event.parse_method,
                event.parse_confidence,
                event.parser_version,
            ),
        )

    connection.execute(
        "UPDATE raw_posts SET parse_status = ? WHERE id = ?",
        ("PARSED" if event_list else empty_status, raw_post_id),
    )
    return len(event_list)


def infer_naghdp_trade_sides(
    connection: sqlite3.Connection,
    *,
    raw_post_id: int | None = None,
    max_age_seconds: int = 180,
) -> dict[str, int]:
    """Link side-less NaghdP trades to the latest matching explicit offer."""
    parameters: list[object] = []
    raw_post_clause = ""
    if raw_post_id is not None:
        raw_post_clause = "AND trade.raw_post_id = ?"
        parameters.append(raw_post_id)

    trades = connection.execute(
        f"""
        SELECT trade.id, trade.raw_post_id, trade.price_num,
               trade.settlement_term, trade.event_time_utc
        FROM price_events trade
        JOIN raw_posts trade_post ON trade_post.id = trade.raw_post_id
        WHERE trade_post.source_code = 'MELTED_FLOW'
          AND trade.instrument = 'MELTED_GOLD_FLOW'
          AND trade.event_type = 'TRADE'
          AND trade.side = 'UNKNOWN'
          {raw_post_clause}
        ORDER BY trade.event_time_utc, trade.id
        """,
        parameters,
    ).fetchall()

    matched = 0
    unresolved = 0
    for trade in trades:
        offer = connection.execute(
            """
            SELECT offer.id, offer.side,
                   (julianday(?) - julianday(offer.event_time_utc)) * 86400.0
                       AS age_seconds
            FROM price_events offer
            JOIN raw_posts offer_post ON offer_post.id = offer.raw_post_id
            WHERE offer_post.source_code = 'MELTED_FLOW'
              AND offer.instrument = 'MELTED_GOLD_FLOW'
              AND offer.event_type = 'OFFER'
              AND offer.side IN ('BUY', 'SELL')
              AND offer.price_num = ?
              AND offer.settlement_term = ?
              AND offer.event_time_utc <= ?
              AND (julianday(?) - julianday(offer.event_time_utc)) * 86400.0
                    BETWEEN -0.001 AND ?
            ORDER BY offer.event_time_utc DESC, offer.id DESC
            LIMIT 1
            """,
            (
                trade["event_time_utc"],
                trade["price_num"],
                trade["settlement_term"],
                trade["event_time_utc"],
                trade["event_time_utc"],
                max_age_seconds,
            ),
        ).fetchone()
        if offer is None:
            unresolved += 1
            continue

        age_seconds = max(0.0, float(offer["age_seconds"]))
        confidence = 0.97 if age_seconds <= 60 else (0.93 if age_seconds <= 120 else 0.85)
        connection.execute(
            """
            UPDATE price_events
            SET side = ?,
                parse_method = 'RULE_CONTEXT',
                parse_confidence = ?,
                parser_version = CASE
                    WHEN instr(parser_version, '+offer-link-v1') > 0
                        THEN parser_version
                    ELSE parser_version || '+offer-link-v1'
                END
            WHERE id = ?
            """,
            (offer["side"], confidence, trade["id"]),
        )
        matched += 1

    return {
        "examined": len(trades),
        "matched": matched,
        "unresolved": unresolved,
    }


def rebuild_minute_prices(connection: sqlite3.Connection) -> int:
    connection.execute("DELETE FROM minute_prices")
    connection.execute(
        """
        INSERT INTO minute_prices(
            minute_utc, instrument, market_label, settlement_term, trade_form,
            event_type, side, open, high, low, close, sample_count
        )
        WITH ranked AS (
            SELECT
                substr(event_time_utc, 1, 16) || ':00Z' AS minute_utc,
                instrument,
                market_label,
                settlement_term,
                trade_form,
                event_type,
                side,
                FIRST_VALUE(price_num) OVER sample_asc AS open,
                MAX(price_num) OVER sample_all AS high,
                MIN(price_num) OVER sample_all AS low,
                FIRST_VALUE(price_num) OVER sample_desc AS close,
                COUNT(*) OVER sample_all AS sample_count,
                ROW_NUMBER() OVER sample_asc AS row_number
            FROM price_events
            WINDOW
                sample_all AS (
                    PARTITION BY substr(event_time_utc, 1, 16), instrument,
                        market_label, settlement_term, trade_form, event_type, side
                ),
                sample_asc AS (
                    PARTITION BY substr(event_time_utc, 1, 16), instrument,
                        market_label, settlement_term, trade_form, event_type, side
                    ORDER BY event_time_utc ASC, id ASC
                ),
                sample_desc AS (
                    PARTITION BY substr(event_time_utc, 1, 16), instrument,
                        market_label, settlement_term, trade_form, event_type, side
                    ORDER BY event_time_utc DESC, id DESC
                )
        )
        SELECT
            minute_utc, instrument, market_label, settlement_term, trade_form,
            event_type, side, open, high, low, close, sample_count
        FROM ranked
        WHERE row_number = 1
        """
    )
    row = connection.execute("SELECT COUNT(*) AS count FROM minute_prices").fetchone()
    connection.commit()
    return int(row["count"] if row else 0)
