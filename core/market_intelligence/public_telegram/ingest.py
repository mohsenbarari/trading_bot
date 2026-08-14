"""Idempotent bridge from an in-memory public message to the Market Store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from core.market_intelligence.market_contracts import (
    MarketObservation,
    derive_event_key,
    normalize_utc,
)
from core.market_intelligence.market_store import (
    advance_source_checkpoint,
    observation_event_time,
    upsert_observation,
)
from core.market_intelligence.herat_price_normalization import (
    HERAT_LOOKBACK_SECONDS,
    HERAT_TEMPORAL_RANGE_VERSION,
    normalize_herat_price,
)

from .parser import PARSER_VERSION, parse_public_message, should_ignore_public_message
from .sources import source_for_code


@dataclass(frozen=True, slots=True)
class PublicTelegramMessage:
    """Transient input only; its text and public ID never enter fact rows."""

    message_id: int
    published_at_utc: datetime | str
    text: str
    available_at_utc: datetime | str | None = None
    is_forwarded: bool = False


@dataclass(frozen=True, slots=True)
class PublicIngestResult:
    source_code: str
    event_count: int
    ignored: bool
    compact_bucket_replaced: bool = False
    compact_older_message_ignored: bool = False
    linked_melted_flow_trades: int = 0


def _available_time(message: PublicTelegramMessage) -> datetime | str:
    return message.available_at_utc or datetime.now(timezone.utc)


def _event_key(
    *,
    source_code: str,
    message_id: int,
    event_index: int,
    event_time_utc: str,
    compact_latest_per_minute: bool,
) -> bytes:
    if compact_latest_per_minute:
        return derive_event_key(
            "public-telegram-compact-v1",
            source_code,
            event_time_utc[:16],
            event_index,
        )
    return derive_event_key(
        "public-telegram-message-v1",
        source_code,
        message_id,
        event_index,
    )


def _link_melted_flow_trade_sides(
    connection: sqlite3.Connection,
    *,
    maximum_offer_age_seconds: int = 180,
) -> int:
    """Use a strictly prior matching offer to enrich side-less paper trades."""

    trades = connection.execute(
        """
        SELECT id, price_num, settlement_term, event_time_utc
        FROM market_observations
        WHERE source_code = 'MELTED_FLOW'
          AND instrument = 'MELTED_GOLD_FLOW'
          AND event_type = 'TRADE'
          AND side = 'UNKNOWN'
          AND quality_state = 'ELIGIBLE'
        ORDER BY event_time_utc, id
        """
    ).fetchall()
    linked = 0
    for trade in trades:
        offer = connection.execute(
            """
            SELECT side,
                   (julianday(?) - julianday(event_time_utc)) * 86400.0
                       AS age_seconds
            FROM market_observations
            WHERE source_code = 'MELTED_FLOW'
              AND instrument = 'MELTED_GOLD_FLOW'
              AND event_type = 'OFFER'
              AND side IN ('BUY', 'SELL')
              AND price_num = ?
              AND settlement_term = ?
              AND trade_form = 'PAPER_NORMAL'
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
            continue
        age_seconds = float(offer["age_seconds"])
        confidence = 0.97 if age_seconds <= 60 else (0.93 if age_seconds <= 120 else 0.85)
        connection.execute(
            """
            UPDATE market_observations
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
        linked += 1
    return linked


def ingest_public_message(
    connection: sqlite3.Connection,
    *,
    source_code: str,
    message: PublicTelegramMessage,
) -> PublicIngestResult:
    """Normalize one approved public message without retaining raw content.

    The caller owns the outer SQLite transaction.  The checkpoint advances for
    ignored messages too, preventing endless replay of known-unusable posts.
    """

    source = source_for_code(source_code)
    if message.message_id <= 0:
        raise ValueError("public_message_id_must_be_positive")
    event_time_utc = normalize_utc(
        message.published_at_utc,
        field_name="public_message_published_at_utc",
    )
    available_at_utc = normalize_utc(
        _available_time(message),
        field_name="public_message_available_at_utc",
    )
    if available_at_utc < event_time_utc:
        raise ValueError("public_message_available_before_event")
    if should_ignore_public_message(
        source.code,
        message.text,
        is_forwarded=message.is_forwarded,
    ):
        advance_source_checkpoint(
            connection,
            source_code=source.code,
            message_id=message.message_id,
            event_time_utc=event_time_utc,
        )
        return PublicIngestResult(source.code, event_count=0, ignored=True)

    parsed_events = parse_public_message(source.code, message.text)
    compact_replaced = False
    compact_older_message_ignored = False
    stored = 0
    for index, parsed in enumerate(parsed_events):
        normalized_price = parsed.price
        normalized_confidence = parsed.parse_confidence
        parser_version = PARSER_VERSION
        if parsed.instrument == "USD_HERAT":
            prior = connection.execute(
                """
                SELECT price_value
                FROM market_observations
                WHERE source_code = ?
                  AND instrument = 'USD_HERAT'
                  AND settlement_term = ?
                  AND trade_form = ?
                  AND quality_state = 'ELIGIBLE'
                  AND event_time_utc < ?
                  AND available_at_utc <= ?
                  AND (julianday(?) - julianday(event_time_utc)) * 86400.0
                      BETWEEN 0 AND ?
                ORDER BY event_time_utc DESC, id DESC
                LIMIT 32
                """,
                (
                    source.code,
                    parsed.settlement_term,
                    parsed.trade_form,
                    event_time_utc,
                    available_at_utc,
                    event_time_utc,
                    HERAT_LOOKBACK_SECONDS,
                ),
            ).fetchall()
            decision = normalize_herat_price(
                parsed.price,
                strictly_prior_same_book_prices=reversed(
                    [row["price_value"] for row in prior]
                ),
            )
            if decision.adjusted:
                normalized_price = decision.price
                normalized_confidence = min(parsed.parse_confidence, 0.96)
                parser_version += "+" + HERAT_TEMPORAL_RANGE_VERSION
        event_key = _event_key(
            source_code=source.code,
            message_id=message.message_id,
            event_index=index,
            event_time_utc=event_time_utc,
            compact_latest_per_minute=source.compact_latest_per_minute,
        )
        existing_time = observation_event_time(connection, event_key)
        if existing_time is not None and existing_time > event_time_utc:
            compact_older_message_ignored = True
            continue
        compact_replaced = compact_replaced or existing_time is not None
        upsert_observation(
            connection,
            MarketObservation(
                event_key=event_key,
                source_code=source.code,
                source_family="TELEGRAM_PUBLIC",
                event_time_utc=event_time_utc,
                available_at_utc=available_at_utc,
                instrument=parsed.instrument,
                market_label=parsed.market_label,
                settlement_term=parsed.settlement_term,
                trade_form=parsed.trade_form,
                event_type=parsed.event_type,
                side=parsed.side,
                price=normalized_price,
                price_unit=parsed.price_unit,
                currency=parsed.currency,
                quantity=parsed.quantity,
                quantity_unit=parsed.quantity_unit,
                parse_confidence=normalized_confidence,
                parser_version=parser_version,
                quality_state="ELIGIBLE",
                quality_policy_version="public-market-v1",
                attributes=parsed.attributes or {},
            ),
        )
        stored += 1
    advance_source_checkpoint(
        connection,
        source_code=source.code,
        message_id=message.message_id,
        event_time_utc=event_time_utc,
    )
    linked = _link_melted_flow_trade_sides(connection) if source.code == "MELTED_FLOW" else 0
    return PublicIngestResult(
        source_code=source.code,
        event_count=stored,
        ignored=stored == 0,
        compact_bucket_replaced=compact_replaced,
        compact_older_message_ignored=compact_older_message_ignored,
        linked_melted_flow_trades=linked,
    )
