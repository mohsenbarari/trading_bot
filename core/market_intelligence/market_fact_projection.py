"""Project privacy-minimized Market Store observations into archived facts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Any, Mapping

from .market_fact_archive import (
    MarketFactArchiveError,
    build_and_publish_fact,
    stable_fact_id,
)
from .private_pipeline_contracts import load_source_registry
from .research_archive import (
    ResearchArchiveKey,
    archive_fact_research_context,
    research_contexts_for_rows,
)


PROJECTION_VERSION = "market-fact-projection-v1"
MAX_EXPORT_PER_CYCLE = 5_000
_REASON_TOKEN = re.compile(r"[^A-Z0-9_]+")


class MarketFactProjectionError(RuntimeError):
    """A payload-free projection failure."""


@dataclass(frozen=True, slots=True)
class ExportReport:
    selected: int
    published: int
    unchanged: int
    rejected: int
    research_contexts_required: int = 0
    research_contexts_archived: int = 0
    research_contexts_unavailable: int = 0


def initialize_export_ledger(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_fact_export_ledger (
            event_key BLOB PRIMARY KEY,
            observation_inserted_at_utc TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('SUCCESS','REJECTED')),
            fact_id TEXT,
            fact_revision INTEGER,
            reason_code TEXT,
            attempts INTEGER NOT NULL CHECK(attempts > 0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS market_fact_export_retry_idx
        ON market_fact_export_ledger(status,observation_inserted_at_utc);
        """
    )


def _pending_export_rows(
    market: sqlite3.Connection,
    *,
    max_rows: int,
) -> list[sqlite3.Row]:
    return market.execute(
        """
        SELECT o.*
        FROM market_observations o
        LEFT JOIN market_fact_export_ledger l ON l.event_key=o.event_key
        WHERE l.event_key IS NULL
           OR l.observation_inserted_at_utc<>o.inserted_at_utc
           OR (
                l.status='REJECTED'
                AND instr(COALESCE(l.reason_code,''),'fact_payload_hash_mismatch')>0
              )
        ORDER BY o.event_time_utc,
                 CASE o.event_type WHEN 'OFFER' THEN 0 WHEN 'TRADE' THEN 1 ELSE 2 END,
                 o.event_key
        LIMIT ?
        """,
        (max_rows,),
    ).fetchall()


def _quality(value: str) -> str:
    return {
        "ELIGIBLE": "ELIGIBLE",
        "PENDING_REVIEW": "REVIEW",
        "AMBIGUOUS": "REVIEW",
        "REJECTED": "REJECTED",
        "IGNORED": "AUDIT_ONLY",
    }.get(value.upper(), "REJECTED")


def _reason_codes(row: sqlite3.Row, attributes: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    if _quality(str(row["quality_state"])) != "ELIGIBLE":
        candidate = str(attributes.get("resolution_reason") or row["quality_state"])
        token = _REASON_TOKEN.sub("_", candidate.upper()).strip("_")[:95]
        if token and (token[0].isalpha()):
            values.append(token)
    if bool(row["is_conditional"]):
        values.append("CONDITIONAL_MARKET_FACT")
    return tuple(dict.fromkeys(values))


def _attributes(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError) as exc:
        raise MarketFactProjectionError("market_fact_projection_attributes_invalid") from exc
    if not isinstance(value, dict):
        raise MarketFactProjectionError("market_fact_projection_attributes_invalid")
    return value


def _root_offer_fact_id(row: sqlite3.Row, attributes: Mapping[str, Any]) -> str:
    event_key = str(attributes.get("root_offer_event_key") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", event_key):
        raise MarketFactProjectionError("market_fact_projection_offer_reference_missing")
    return stable_fact_id(
        source_code=str(row["source_code"]),
        event_key=event_key,
        fact_kind=(
            "COIN_OFFER"
            if str(row["source_code"]).startswith("GROUP_")
            else "PRIVATE_GOLD_OFFER"
        ),
    )


def _require_root_offer_observation(
    market: sqlite3.Connection,
    row: sqlite3.Row,
    attributes: Mapping[str, Any],
) -> None:
    event_key = str(attributes.get("root_offer_event_key") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", event_key):
        raise MarketFactProjectionError("market_fact_projection_offer_reference_missing")
    root = market.execute(
        "SELECT source_code,event_type FROM market_observations WHERE event_key=?",
        (bytes.fromhex(event_key),),
    ).fetchone()
    if (
        root is None
        or str(root["source_code"]) != str(row["source_code"])
        or str(root["event_type"]) != "OFFER"
    ):
        raise MarketFactProjectionError(
            "market_fact_projection_offer_dependency_missing"
        )


def _root_quantity(
    market: sqlite3.Connection,
    attributes: Mapping[str, Any],
) -> str | None:
    key = str(attributes.get("root_offer_event_key") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    row = market.execute(
        "SELECT quantity_value FROM market_observations WHERE event_key=?",
        (bytes.fromhex(key),),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def observation_payload(
    market: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    source = str(row["source_code"])
    event_type = str(row["event_type"])
    attributes = _attributes(row)
    quantity = str(row["quantity_value"]) if row["quantity_value"] is not None else None
    quantity_unit = str(row["quantity_unit"]) if row["quantity_unit"] is not None else None
    if source in {"GROUP_1", "GROUP_2"} and event_type == "OFFER":
        return {
            "kind": "COIN_OFFER",
            "group_code": int(source[-1]),
            "instrument": str(row["instrument"]),
            "side": str(row["side"]),
            "settlement": str(row["settlement_term"]),
            "trade_form": str(row["trade_form"]),
            "offered_price_value": str(row["price_value"]),
            "price_unit": str(row["price_unit"]),
            "quantity_value": quantity,
            "quantity_unit": quantity_unit,
        }
    if source in {"GROUP_1", "GROUP_2"} and event_type == "TRADE":
        _require_root_offer_observation(market, row, attributes)
        quality = _quality(str(row["quality_state"]))
        if quality == "ELIGIBLE":
            root_quantity = _root_quantity(market, attributes)
            outcome = "CONFIRMED_FULL"
            if root_quantity is not None and quantity is not None:
                from decimal import Decimal

                if Decimal(quantity) < Decimal(root_quantity):
                    outcome = "CONFIRMED_PARTIAL"
            return {
                "kind": "COIN_TRADE",
                "offer_fact_id": _root_offer_fact_id(row, attributes),
                "outcome": outcome,
                "agreed_price_value": str(row["price_value"]),
                "price_unit": str(row["price_unit"]),
                "agreed_quantity_value": quantity,
                "quantity_unit": quantity_unit,
            }
        return {
            "kind": "COIN_TRADE",
            "offer_fact_id": _root_offer_fact_id(row, attributes),
            "outcome": "AMBIGUOUS" if quality == "REVIEW" else "REJECTED",
        }
    if source == "PRIVATE_GOLD_CHANNEL" and event_type == "OFFER":
        return {
            "kind": "PRIVATE_GOLD_OFFER",
            "instrument": "MELTED_GOLD_PRIVATE",
            "side": str(row["side"]),
            "settlement": str(row["settlement_term"]),
            "trade_form": str(row["trade_form"]),
            "offered_price_value": str(row["price_value"]),
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "quantity_value": quantity,
            "quantity_unit": quantity_unit,
            "lifetime_seconds": 120,
        }
    if source == "PRIVATE_GOLD_CHANNEL" and event_type == "TRADE":
        _require_root_offer_observation(market, row, attributes)
        remaining = attributes.get("remaining_quantity")
        offer_quantity = attributes.get("offer_quantity")
        outcome = "FULL"
        if remaining not in {None, 0, "0"}:
            outcome = "PARTIAL"
        executed = quantity
        if executed is None and offer_quantity is not None and remaining is not None:
            from decimal import Decimal

            executed = str(Decimal(str(offer_quantity)) - Decimal(str(remaining)))
        return {
            "kind": "PRIVATE_GOLD_OUTCOME",
            "offer_fact_id": _root_offer_fact_id(row, attributes),
            "outcome": outcome,
            "executed_quantity_value": executed,
            "remaining_quantity_value": str(remaining) if remaining is not None else None,
            "quantity_unit": quantity_unit,
        }
    if source in {
        "XAUUSD",
        "WALLEX_PUBLIC_API",
        "BINANCE_PAXG_PUBLIC_API",
    }:
        side = str(row["side"])
        return {
            "kind": "EXTERNAL_QUOTE",
            "instrument": str(row["instrument"]),
            "quote_kind": {"BUY": "BID", "SELL": "ASK", "MID": "MID"}.get(
                side, "LAST"
            ),
            "price_value": str(row["price_value"]),
            "price_unit": str(row["price_unit"]),
            "currency": str(row["currency"]),
        }
    return {
        "kind": "OBSERVATION",
        "instrument": str(row["instrument"]),
        "event_type": event_type,
        "side": str(row["side"]),
        "settlement": str(row["settlement_term"]),
        "trade_form": str(row["trade_form"]),
        "price_value": str(row["price_value"]),
        "price_unit": str(row["price_unit"]),
        "currency": str(row["currency"]),
        "quantity_value": quantity,
        "quantity_unit": quantity_unit,
    }


def export_market_store_facts(
    market: sqlite3.Connection,
    archive_connection,
    *,
    max_rows: int = MAX_EXPORT_PER_CYCLE,
    capture_staging: sqlite3.Connection | None = None,
    research_key: ResearchArchiveKey | None = None,
) -> ExportReport:
    initialize_export_ledger(market)
    allowed = load_source_registry().by_code()
    rows = _pending_export_rows(market, max_rows=max_rows)
    research_sources = {
        "GROUP_1",
        "GROUP_2",
        "PRIVATE_GOLD_CHANNEL",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
    }
    research_required = sum(
        str(row["source_code"]) in research_sources for row in rows
    )
    contexts = (
        research_contexts_for_rows(capture_staging, rows)
        if capture_staging is not None and research_key is not None
        else {}
    )
    research_archived = 0
    published = unchanged = rejected = 0
    for index, row in enumerate(rows):
        event_key = bytes(row["event_key"]).hex()
        source_code = str(row["source_code"])
        reason: str | None = None
        fact_id: str | None = None
        revision: int | None = None
        try:
            source = allowed.get(source_code)
            if source is None or not source.transfer_to_bot:
                raise MarketFactProjectionError("market_fact_projection_source_not_transferable")
            payload = observation_payload(market, row)
            with archive_connection.cursor() as cursor:
                savepoint = f"market_fact_projection_{index}"
                cursor.execute(f"SAVEPOINT {savepoint}")
                try:
                    result = build_and_publish_fact(
                        archive_connection,
                        event_key=event_key,
                        origin_event_key=event_key,
                        source_code=source_code,
                        occurred_at_utc=str(row["event_time_utc"]),
                        available_at_utc=str(row["available_at_utc"]),
                        parser_version=str(row["parser_version"]),
                        quality_state=_quality(str(row["quality_state"])),
                        quality_reason_codes=_reason_codes(row, _attributes(row)),
                        payload=payload,
                    )
                    context = contexts.get(bytes(row["event_key"]))
                    if context is not None and research_key is not None:
                        archive_fact_research_context(
                            cursor,
                            fact_id=result.fact.fact_id,
                            fact_revision=result.fact.fact_revision,
                            context=context,
                            key=research_key,
                        )
                        research_archived += 1
                except BaseException:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    raise
                finally:
                    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
            fact_id = result.fact.fact_id
            revision = result.fact.fact_revision
            published += int(result.changed)
            unchanged += int(not result.changed)
            status = "SUCCESS"
        except (MarketFactProjectionError, MarketFactArchiveError, ValueError) as exc:
            reason = str(exc)[:96]
            status = "REJECTED"
            rejected += 1
        market.execute(
            """
            INSERT INTO market_fact_export_ledger(
                event_key,observation_inserted_at_utc,status,fact_id,fact_revision,
                reason_code,attempts,updated_at_utc
            ) VALUES(?,?,?,?,?,?,1,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            ON CONFLICT(event_key) DO UPDATE SET
                observation_inserted_at_utc=excluded.observation_inserted_at_utc,
                status=excluded.status,fact_id=excluded.fact_id,
                fact_revision=excluded.fact_revision,reason_code=excluded.reason_code,
                attempts=market_fact_export_ledger.attempts+1,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                row["event_key"],
                str(row["inserted_at_utc"]),
                status,
                fact_id,
                revision,
                reason,
            ),
        )
    return ExportReport(
        selected=len(rows),
        published=published,
        unchanged=unchanged,
        rejected=rejected,
        research_contexts_required=research_required,
        research_contexts_archived=research_archived,
        research_contexts_unavailable=max(0, research_required - research_archived),
    )
