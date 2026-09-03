"""Project privacy-minimized Market Store observations into archived facts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Any, Mapping

from pydantic import TypeAdapter

from .market_fact_archive import (
    MarketFactArchiveError,
    _fact_semantic_fingerprint,
    build_and_publish_fact,
    stable_fact_id,
)
from .private_pipeline_contracts import FactPayload, content_hash, load_source_registry
from .research_archive import (
    ResearchArchiveKey,
    archive_fact_research_context,
    research_contexts_for_rows,
)
from .xau_model_input import (
    XAU_MODEL_INPUT_BUCKET_SECONDS,
    XAU_MODEL_INPUT_EXPORT_SETTLE_SECONDS,
    xau_model_input_bucket,
)


PROJECTION_VERSION = "market-fact-projection-v2-xau-model-input"
MAX_EXPORT_PER_CYCLE = 5_000
_REASON_TOKEN = re.compile(r"[^A-Z0-9_]+")
_FACT_PAYLOAD_ADAPTER = TypeAdapter(FactPayload)


class MarketFactProjectionError(RuntimeError):
    """A payload-free projection failure."""


def _normalize_projection_payload(value: Mapping[str, Any]) -> FactPayload:
    return _FACT_PAYLOAD_ADAPTER.validate_python(value)


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
        f"""
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
        CREATE TABLE IF NOT EXISTS market_fact_export_semantics (
            event_key BLOB PRIMARY KEY,
            observation_inserted_at_utc TEXT NOT NULL,
            fact_id TEXT NOT NULL CHECK(length(fact_id)=64),
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            source_sequence INTEGER NOT NULL CHECK(source_sequence>0),
            delivery_sequence INTEGER NOT NULL CHECK(delivery_sequence>0),
            payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            quality_state TEXT NOT NULL,
            semantic_fingerprint TEXT NOT NULL CHECK(length(semantic_fingerprint)=64),
            envelope_hash TEXT NOT NULL CHECK(length(envelope_hash)=64),
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_fact_export_history (
            fact_id TEXT NOT NULL CHECK(length(fact_id)=64),
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            event_key BLOB NOT NULL,
            observation_inserted_at_utc TEXT NOT NULL,
            source_sequence INTEGER NOT NULL CHECK(source_sequence>0),
            delivery_sequence INTEGER NOT NULL CHECK(delivery_sequence>0),
            payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
            quality_state TEXT NOT NULL,
            semantic_fingerprint TEXT NOT NULL CHECK(length(semantic_fingerprint)=64),
            envelope_hash TEXT NOT NULL CHECK(length(envelope_hash)=64),
            exported_at_utc TEXT NOT NULL,
            PRIMARY KEY(fact_id,fact_revision)
        );
        CREATE INDEX IF NOT EXISTS market_fact_export_history_event_idx
        ON market_fact_export_history(event_key,fact_revision);
        CREATE INDEX IF NOT EXISTS idx_market_observations_export_event_time
        ON market_observations(event_time_utc DESC,id DESC);
        CREATE TABLE IF NOT EXISTS market_xau_model_input_buckets (
            bucket_number INTEGER PRIMARY KEY,
            selected_event_key BLOB NOT NULL UNIQUE,
            selected_event_time_utc TEXT NOT NULL,
            selected_inserted_at_utc TEXT NOT NULL,
            selected_observation_id INTEGER NOT NULL CHECK(selected_observation_id>0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_xau_model_input_cursor (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            last_inserted_at_utc TEXT NOT NULL,
            last_observation_id INTEGER NOT NULL CHECK(last_observation_id>=0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS market_xau_model_input_insert_cursor_idx
        ON market_observations(
            source_code,
            (CASE WHEN instr(inserted_at_utc,'.')=0
                  THEN replace(inserted_at_utc,'Z','.000000Z')
                  ELSE inserted_at_utc END),
            id
        ) WHERE source_code='XAUUSD';
        CREATE INDEX IF NOT EXISTS market_xau_model_input_bucket_lookup_idx
        ON market_observations(
            source_code,
            (CAST(strftime('%s',event_time_utc) AS INTEGER)
             / {XAU_MODEL_INPUT_BUCKET_SECONDS}),
            event_time_utc DESC,
            id DESC
        ) WHERE source_code='XAUUSD';
        """
    )
    cursor = connection.execute(
        "SELECT 1 FROM market_xau_model_input_cursor WHERE singleton=1"
    ).fetchone()
    if cursor is None:
        # One-time, deterministic adoption of an existing raw XAU Store.  Raw
        # quotes remain intact; this small materialized index only identifies
        # the latest real quote in each established 15-second input bucket.
        connection.execute(
            f"""
            INSERT INTO market_xau_model_input_buckets(
                bucket_number,selected_event_key,selected_event_time_utc,
                selected_inserted_at_utc,selected_observation_id,updated_at_utc
            )
            SELECT bucket_number,event_key,event_time_utc,insert_order,id,
                   strftime('%Y-%m-%dT%H:%M:%SZ','now')
            FROM (
                SELECT id,event_key,event_time_utc,
                       CASE WHEN instr(inserted_at_utc,'.')=0
                            THEN replace(inserted_at_utc,'Z','.000000Z')
                            ELSE inserted_at_utc END AS insert_order,
                       CAST(strftime('%s',event_time_utc) AS INTEGER)
                           / {XAU_MODEL_INPUT_BUCKET_SECONDS} AS bucket_number,
                       ROW_NUMBER() OVER (
                           PARTITION BY CAST(strftime('%s',event_time_utc) AS INTEGER)
                                        / {XAU_MODEL_INPUT_BUCKET_SECONDS}
                           ORDER BY event_time_utc DESC,id DESC
                       ) AS bucket_rank
                FROM market_observations
                WHERE source_code='XAUUSD'
            )
            WHERE bucket_rank=1
            """
        )
        latest = connection.execute(
            """
            SELECT CASE WHEN instr(inserted_at_utc,'.')=0
                        THEN replace(inserted_at_utc,'Z','.000000Z')
                        ELSE inserted_at_utc END AS insert_order,id
            FROM market_observations
            WHERE source_code='XAUUSD'
            ORDER BY insert_order DESC,id DESC
            LIMIT 1
            """
        ).fetchone()
        connection.execute(
            """
            INSERT INTO market_xau_model_input_cursor(
                singleton,last_inserted_at_utc,last_observation_id,updated_at_utc
            ) VALUES(1,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            """,
            (
                str(latest["insert_order"]) if latest is not None else "",
                int(latest["id"]) if latest is not None else 0,
            ),
        )


def _refresh_xau_model_input_buckets(
    connection: sqlite3.Connection,
    *,
    max_rows: int = MAX_EXPORT_PER_CYCLE,
) -> int:
    """Incrementally index new/edited raw XAU rows without deleting raw data."""

    state = connection.execute(
        "SELECT last_inserted_at_utc,last_observation_id "
        "FROM market_xau_model_input_cursor WHERE singleton=1"
    ).fetchone()
    if state is None:
        raise MarketFactProjectionError("xau_model_input_cursor_missing")
    rows = connection.execute(
        """
        SELECT id,event_key,event_time_utc,
               CASE WHEN instr(inserted_at_utc,'.')=0
                    THEN replace(inserted_at_utc,'Z','.000000Z')
                    ELSE inserted_at_utc END AS insert_order
        FROM market_observations
        WHERE source_code='XAUUSD'
          AND (
                (CASE WHEN instr(inserted_at_utc,'.')=0
                      THEN replace(inserted_at_utc,'Z','.000000Z')
                      ELSE inserted_at_utc END)>?
                OR (
                    (CASE WHEN instr(inserted_at_utc,'.')=0
                          THEN replace(inserted_at_utc,'Z','.000000Z')
                          ELSE inserted_at_utc END)=?
                    AND id>?
                )
              )
        ORDER BY insert_order,id
        LIMIT ?
        """,
        (
            str(state["last_inserted_at_utc"]),
            str(state["last_inserted_at_utc"]),
            int(state["last_observation_id"]),
            max(1, int(max_rows)),
        ),
    ).fetchall()
    for row in rows:
        bucket = xau_model_input_bucket(row["event_time_utc"])
        connection.execute(
            """
            INSERT INTO market_xau_model_input_buckets(
                bucket_number,selected_event_key,selected_event_time_utc,
                selected_inserted_at_utc,selected_observation_id,updated_at_utc
            ) VALUES(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            ON CONFLICT(bucket_number) DO UPDATE SET
                selected_event_key=excluded.selected_event_key,
                selected_event_time_utc=excluded.selected_event_time_utc,
                selected_inserted_at_utc=excluded.selected_inserted_at_utc,
                selected_observation_id=excluded.selected_observation_id,
                updated_at_utc=excluded.updated_at_utc
            WHERE excluded.selected_event_time_utc>
                      market_xau_model_input_buckets.selected_event_time_utc
               OR (
                    excluded.selected_event_time_utc=
                        market_xau_model_input_buckets.selected_event_time_utc
                    AND excluded.selected_observation_id>
                        market_xau_model_input_buckets.selected_observation_id
                  )
            """,
            (
                bucket,
                row["event_key"],
                str(row["event_time_utc"]),
                str(row["insert_order"]),
                int(row["id"]),
            ),
        )
    if rows:
        latest = rows[-1]
        connection.execute(
            """
            UPDATE market_xau_model_input_cursor
            SET last_inserted_at_utc=?,last_observation_id=?,
                updated_at_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now')
            WHERE singleton=1
            """,
            (str(latest["insert_order"]), int(latest["id"])),
        )
    return len(rows)


def _pending_export_rows(
    market: sqlite3.Connection,
    *,
    max_rows: int,
    force_event_keys: tuple[bytes, ...] | None = None,
) -> list[sqlite3.Row]:
    if force_event_keys is not None:
        keys = tuple(dict.fromkeys(bytes(value) for value in force_event_keys))
        if not keys:
            return []
        if len(keys) > max_rows or any(len(value) != 32 for value in keys):
            raise MarketFactProjectionError(
                "market_fact_projection_force_scope_invalid"
            )
        placeholders = ",".join("?" for _ in keys)
        return market.execute(
            f"""
            SELECT o.*
            FROM market_observations o
            WHERE o.event_key IN ({placeholders})
            ORDER BY o.event_time_utc,
                     CASE o.event_type WHEN 'OFFER' THEN 0 WHEN 'TRADE' THEN 1 ELSE 2 END,
                     o.event_key
            """,
            keys,
        ).fetchall()
    _refresh_xau_model_input_buckets(market, max_rows=max_rows)
    eligible = f"""
        (o.source_code<>'XAUUSD' OR EXISTS (
            SELECT 1
            FROM market_xau_model_input_buckets b
            WHERE b.selected_event_key=o.event_key
              AND CAST(strftime('%s','now') AS INTEGER) >=
                  (b.bucket_number + 1) * {XAU_MODEL_INPUT_BUCKET_SECONDS}
                  + {XAU_MODEL_INPUT_EXPORT_SETTLE_SECONDS}
        ))
    """
    pending = """
        (l.event_key IS NULL
         OR l.observation_inserted_at_utc<>o.inserted_at_utc
         OR (
              l.status='SUCCESS'
              AND (
                  s.event_key IS NULL
                  OR s.observation_inserted_at_utc<>o.inserted_at_utc
              )
            )
         OR (
              l.status='REJECTED'
              AND instr(COALESCE(l.reason_code,''),'fact_payload_hash_mismatch')>0
            )
        )
    """

    def select(extra_where: str, parameters: tuple[object, ...], limit: int):
        return market.execute(
            f"""
            SELECT o.*
            FROM market_observations o
            LEFT JOIN market_fact_export_ledger l ON l.event_key=o.event_key
            LEFT JOIN market_fact_export_semantics s ON s.event_key=o.event_key
            WHERE {eligible} AND {pending} {extra_where}
            LIMIT ?
            """,
            (*parameters, limit),
        ).fetchall()

    # Coin-group facts are the primary estimator input and must not be starved
    # by the continuous public-reference stream. Offers sort ahead of trades,
    # so dependent trades follow their offer roots in this or an earlier batch.
    rows = select(
        "AND o.source_code IN ('GROUP_1','GROUP_2') "
        "ORDER BY CASE o.event_type WHEN 'OFFER' THEN 0 ELSE 1 END,"
        "o.event_time_utc DESC,o.id DESC",
        (),
        max_rows,
    )
    remaining = max_rows - len(rows)
    if remaining:
        selected = tuple(bytes(row["event_key"]) for row in rows)
        exclusion = (
            "AND o.event_key NOT IN ("
            + ",".join("?" for _ in selected)
            + ") "
            if selected
            else ""
        )
        fresh = select(
            exclusion
            + "AND o.event_time_utc >= "
            + "strftime('%Y-%m-%dT%H:%M:%SZ','now','-10 minutes') "
            + "ORDER BY o.event_time_utc DESC,o.id DESC",
            selected,
            remaining,
        )
        fresh.sort(
            key=lambda row: (
                str(row["event_time_utc"]),
                {"OFFER": 0, "TRADE": 1}.get(str(row["event_type"]), 2),
                bytes(row["event_key"]),
            )
        )
        rows.extend(fresh)
        remaining -= len(fresh)
    if remaining:
        selected = tuple(bytes(row["event_key"]) for row in rows)
        exclusion = (
            "AND o.event_key NOT IN ("
            + ",".join("?" for _ in selected)
            + ") "
            if selected
            else ""
        )
        rows.extend(
            select(
                exclusion + "ORDER BY o.id",
                selected,
                remaining,
            )
        )
    return rows


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


def observation_fact_semantics(
    market: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    source_sequence: int,
) -> tuple[str, str, str]:
    """Return payload hash, transfer quality and full semantic fingerprint."""

    source_code = str(row["source_code"])
    source = load_source_registry().by_code().get(source_code)
    if source is None or not source.transfer_to_bot:
        raise MarketFactProjectionError(
            "market_fact_projection_source_not_transferable"
        )
    payload = observation_payload(market, row)
    quality_state = _quality(str(row["quality_state"]))
    normalized_payload = _normalize_projection_payload(payload)
    payload_hash = content_hash(normalized_payload)
    fingerprint = _fact_semantic_fingerprint(
        event_key=bytes(row["event_key"]).hex(),
        origin_event_key=bytes(row["event_key"]).hex(),
        source_code=source_code,
        stream_id=source.fact_stream_id,
        source_sequence=source_sequence,
        occurred_at_utc=str(row["event_time_utc"]),
        available_at_utc=str(row["available_at_utc"]),
        parser_version=str(row["parser_version"]),
        quality_state=quality_state,
        quality_reason_codes=_reason_codes(row, _attributes(row)),
        payload=normalized_payload,
    )
    return payload_hash, quality_state, fingerprint


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
    force_event_keys: tuple[bytes, ...] | None = None,
) -> ExportReport:
    initialize_export_ledger(market)
    allowed = load_source_registry().by_code()
    rows = _pending_export_rows(
        market,
        max_rows=max_rows,
        force_event_keys=force_event_keys,
    )
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
        delivery_sequence: int | None = None
        envelope_hash: str | None = None
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
                    cursor.execute(
                        "SELECT delivery_sequence,encode(envelope_hash,'hex') "
                        "FROM market_data.market_fact_outbox "
                        "WHERE fact_id=decode(%s,'hex') AND fact_revision=%s",
                        (result.fact.fact_id, result.fact.fact_revision),
                    )
                    outbox = cursor.fetchone()
                    if (
                        outbox is None
                        or int(outbox[0]) < 1
                        or not re.fullmatch(r"[0-9a-f]{64}", str(outbox[1]))
                    ):
                        raise MarketFactProjectionError(
                            "market_fact_projection_outbox_lineage_missing"
                        )
                    delivery_sequence = int(outbox[0])
                    envelope_hash = str(outbox[1])
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
        if status == "SUCCESS":
            if delivery_sequence is None or envelope_hash is None:
                raise MarketFactProjectionError(
                    "market_fact_projection_outbox_lineage_missing"
                )
            semantic_fingerprint = _fact_semantic_fingerprint(
                event_key=result.fact.event_key,
                origin_event_key=result.fact.origin_event_key,
                source_code=result.fact.source_code,
                stream_id=result.fact.stream_id,
                source_sequence=result.fact.source_sequence,
                occurred_at_utc=result.fact.occurred_at_utc,
                available_at_utc=result.fact.available_at_utc,
                parser_version=result.fact.parser_version,
                quality_state=result.fact.quality_state,
                quality_reason_codes=result.fact.quality_reason_codes,
                payload=result.fact.payload,
            )
            market.execute(
                """
                INSERT INTO market_fact_export_semantics(
                    event_key,observation_inserted_at_utc,fact_id,fact_revision,
                    source_sequence,delivery_sequence,payload_hash,quality_state,
                    semantic_fingerprint,envelope_hash,updated_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                ON CONFLICT(event_key) DO UPDATE SET
                    observation_inserted_at_utc=excluded.observation_inserted_at_utc,
                    fact_id=excluded.fact_id,fact_revision=excluded.fact_revision,
                    source_sequence=excluded.source_sequence,
                    delivery_sequence=excluded.delivery_sequence,
                    payload_hash=excluded.payload_hash,
                    quality_state=excluded.quality_state,
                    semantic_fingerprint=excluded.semantic_fingerprint,
                    envelope_hash=excluded.envelope_hash,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    row["event_key"],
                    str(row["inserted_at_utc"]),
                    result.fact.fact_id,
                    result.fact.fact_revision,
                    result.fact.source_sequence,
                    delivery_sequence,
                    result.fact.payload_hash,
                    result.fact.quality_state,
                    semantic_fingerprint,
                    envelope_hash,
                ),
            )
            market.execute(
                """
                INSERT INTO market_fact_export_history(
                    fact_id,fact_revision,event_key,
                    observation_inserted_at_utc,source_sequence,delivery_sequence,payload_hash,
                    quality_state,semantic_fingerprint,envelope_hash,exported_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                ON CONFLICT(fact_id,fact_revision) DO UPDATE SET
                    event_key=excluded.event_key,
                    observation_inserted_at_utc=excluded.observation_inserted_at_utc,
                    source_sequence=excluded.source_sequence,
                    delivery_sequence=excluded.delivery_sequence,
                    payload_hash=excluded.payload_hash,
                    quality_state=excluded.quality_state,
                    semantic_fingerprint=excluded.semantic_fingerprint,
                    envelope_hash=excluded.envelope_hash,
                    exported_at_utc=excluded.exported_at_utc
                """,
                (
                    result.fact.fact_id,
                    result.fact.fact_revision,
                    row["event_key"],
                    str(row["inserted_at_utc"]),
                    result.fact.source_sequence,
                    delivery_sequence,
                    result.fact.payload_hash,
                    result.fact.quality_state,
                    semantic_fingerprint,
                    envelope_hash,
                ),
            )
        else:
            market.execute(
                "DELETE FROM market_fact_export_semantics WHERE event_key=?",
                (row["event_key"],),
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
