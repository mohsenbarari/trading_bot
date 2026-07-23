"""Minimal normalized SQLite storage for Telegram market observations."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import blake2b
from pathlib import Path
import sqlite3
from typing import Iterable

from core.market_intelligence.telegram_collector.config import (
    TelegramSource,
    source_for_code,
)
from core.market_intelligence.telegram_collector.models import (
    CollectedMessage,
    IngestResult,
    PriceEvent,
)
from core.market_intelligence.telegram_collector.parsers import (
    parse_message,
    should_ignore_message,
)


SCHEMA_VERSION = 1
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS collector_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_checkpoints (
    source_code TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL,
    last_event_time_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_events (
    id INTEGER PRIMARY KEY,
    message_key BLOB NOT NULL CHECK(length(message_key) = 16),
    event_index INTEGER NOT NULL,
    event_time_utc TEXT NOT NULL,
    instrument TEXT NOT NULL,
    market_label TEXT NOT NULL,
    settlement_term TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    event_type TEXT NOT NULL,
    side TEXT NOT NULL,
    price_num REAL NOT NULL CHECK(price_num > 0),
    price_unit TEXT NOT NULL,
    quantity_num REAL,
    parse_confidence REAL NOT NULL
        CHECK(parse_confidence >= 0 AND parse_confidence <= 1),
    parser_version TEXT NOT NULL,
    UNIQUE(message_key, event_index)
);

CREATE INDEX IF NOT EXISTS idx_price_events_instrument_time
    ON price_events(instrument, event_time_utc);
CREATE INDEX IF NOT EXISTS idx_price_events_dimensions_time
    ON price_events(
        market_label, settlement_term, trade_form,
        event_type, side, event_time_utc
    );
"""


class TelegramStorageError(RuntimeError):
    """Raised when normalized collector storage fails closed."""


def _utc_timestamp(value: str | datetime) -> str:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        )
    except ValueError as exc:
        raise TelegramStorageError(
            "telegram_message_timestamp_invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TelegramStorageError(
            "telegram_message_timestamp_timezone_required"
        )
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def connect(path: Path | str) -> sqlite3.Connection:
    database = Path(path).expanduser()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    row = connection.execute(
        "SELECT schema_version FROM collector_metadata WHERE singleton = 1"
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO collector_metadata(singleton, schema_version)
            VALUES (1, ?)
            """,
            (SCHEMA_VERSION,),
        )
    elif int(row["schema_version"]) != SCHEMA_VERSION:
        raise TelegramStorageError(
            "telegram_collector_schema_version_unsupported"
        )
    connection.commit()


def _message_key(
    *,
    source: TelegramSource,
    message_id: int,
    event_time_utc: str,
) -> bytes:
    if message_id <= 0:
        raise TelegramStorageError("telegram_message_id_invalid")
    logical_id = (
        event_time_utc[:16]
        if source.compact_per_minute
        else str(message_id)
    )
    digest = blake2b(digest_size=16, person=b"coin-tg-msg-v1")
    digest.update(source.code.encode("ascii"))
    digest.update(b"\0")
    digest.update(logical_id.encode("ascii"))
    return digest.digest()


def read_checkpoint(
    connection: sqlite3.Connection,
    source_code: str,
) -> int | None:
    source = source_for_code(source_code)
    row = connection.execute(
        """
        SELECT last_message_id
        FROM collector_checkpoints
        WHERE source_code = ?
        """,
        (source.code,),
    ).fetchone()
    return int(row["last_message_id"]) if row is not None else None


def _advance_checkpoint(
    connection: sqlite3.Connection,
    *,
    source: TelegramSource,
    message_id: int,
    event_time_utc: str,
) -> None:
    now = _utc_timestamp(datetime.now(timezone.utc))
    connection.execute(
        """
        INSERT INTO collector_checkpoints(
            source_code, last_message_id, last_event_time_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(source_code) DO UPDATE SET
            last_message_id = MAX(
                collector_checkpoints.last_message_id,
                excluded.last_message_id
            ),
            last_event_time_utc = CASE
                WHEN excluded.last_message_id >=
                     collector_checkpoints.last_message_id
                THEN excluded.last_event_time_utc
                ELSE collector_checkpoints.last_event_time_utc
            END,
            updated_at_utc = excluded.updated_at_utc
        """,
        (source.code, message_id, event_time_utc, now),
    )


def _insert_events(
    connection: sqlite3.Connection,
    *,
    message_key: bytes,
    event_time_utc: str,
    events: Iterable[PriceEvent],
) -> int:
    rows = list(events)
    for index, event in enumerate(rows):
        connection.execute(
            """
            INSERT INTO price_events(
                message_key, event_index, event_time_utc,
                instrument, market_label, settlement_term, trade_form,
                event_type, side, price_num, price_unit, quantity_num,
                parse_confidence, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_key,
                index,
                event_time_utc,
                event.instrument,
                event.market_label,
                event.settlement_term,
                event.trade_form,
                event.event_type,
                event.side,
                float(event.price),
                event.price_unit,
                (
                    float(event.quantity)
                    if event.quantity is not None
                    else None
                ),
                event.parse_confidence,
                event.parser_version,
            ),
        )
    return len(rows)


def ingest_message(
    connection: sqlite3.Connection,
    *,
    source_code: str,
    message: CollectedMessage,
) -> IngestResult:
    """Parse and replace one message without retaining its text or public ID."""
    source = source_for_code(source_code)
    event_time_utc = _utc_timestamp(message.published_at_utc)
    message_key = _message_key(
        source=source,
        message_id=message.message_id,
        event_time_utc=event_time_utc,
    )
    ignored = should_ignore_message(
        source.username,
        message.text,
        is_forwarded=message.is_forwarded,
    )
    events = (
        []
        if ignored
        else parse_message(source.username, message.text)
    )

    compact_replaced = False
    if source.compact_per_minute and events:
        existing = connection.execute(
            """
            SELECT MAX(event_time_utc) AS latest
            FROM price_events
            WHERE message_key = ?
            """,
            (message_key,),
        ).fetchone()
        latest = str(existing["latest"]) if existing["latest"] else None
        if latest is not None and latest > event_time_utc:
            _advance_checkpoint(
                connection,
                source=source,
                message_id=message.message_id,
                event_time_utc=event_time_utc,
            )
            return IngestResult(
                source_code=source.code,
                parsed_event_count=0,
                ignored=True,
            )
        compact_replaced = latest is not None
        connection.execute(
            "DELETE FROM price_events WHERE message_key = ?",
            (message_key,),
        )
    elif not source.compact_per_minute:
        connection.execute(
            "DELETE FROM price_events WHERE message_key = ?",
            (message_key,),
        )

    count = _insert_events(
        connection,
        message_key=message_key,
        event_time_utc=event_time_utc,
        events=events,
    )
    _advance_checkpoint(
        connection,
        source=source,
        message_id=message.message_id,
        event_time_utc=event_time_utc,
    )
    return IngestResult(
        source_code=source.code,
        parsed_event_count=count,
        ignored=ignored or count == 0,
        compact_bucket_replaced=compact_replaced,
    )


def infer_melted_flow_trade_sides(
    connection: sqlite3.Connection,
    *,
    maximum_offer_age_seconds: int = 180,
) -> dict[str, int]:
    """Link side-less NaghdP trades to a strictly earlier matching offer."""
    if maximum_offer_age_seconds <= 0:
        raise ValueError("maximum_offer_age_seconds_must_be_positive")
    trades = connection.execute(
        """
        SELECT id, price_num, settlement_term, event_time_utc
        FROM price_events
        WHERE instrument = 'MELTED_GOLD_FLOW'
          AND event_type = 'TRADE'
          AND side = 'UNKNOWN'
        ORDER BY event_time_utc, id
        """
    ).fetchall()
    matched = 0
    unresolved = 0
    for trade in trades:
        offer = connection.execute(
            """
            SELECT side,
                   (julianday(?) - julianday(event_time_utc)) * 86400.0
                       AS age_seconds
            FROM price_events
            WHERE instrument = 'MELTED_GOLD_FLOW'
              AND event_type = 'OFFER'
              AND side IN ('BUY', 'SELL')
              AND price_num = ?
              AND settlement_term = ?
              AND event_time_utc < ?
              AND (julianday(?) - julianday(event_time_utc)) * 86400.0
                    BETWEEN 0 AND ?
            ORDER BY event_time_utc DESC, id DESC
            LIMIT 1
            """,
            (
                trade["event_time_utc"],
                trade["price_num"],
                trade["settlement_term"],
                trade["event_time_utc"],
                trade["event_time_utc"],
                maximum_offer_age_seconds,
            ),
        ).fetchone()
        if offer is None:
            unresolved += 1
            continue
        age_seconds = max(0.0, float(offer["age_seconds"]))
        confidence = (
            0.97
            if age_seconds <= 60
            else (0.93 if age_seconds <= 120 else 0.85)
        )
        connection.execute(
            """
            UPDATE price_events
            SET side = ?, parse_confidence = ?,
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
