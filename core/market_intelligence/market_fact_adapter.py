"""Idempotent private Market Fact consumer for the bot-side Market Store.

The receiver database is a read-only inbox.  Every observation, projection
identity, rejection record, and adapter checkpoint is committed in one
transaction in the private Market Store.  A malformed economic row can
therefore be isolated without poisoning the remaining healthy stream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping

from pydantic import ValidationError

from .market_contracts import MarketObservation, MarketStoreContractError
from .market_store import connect_market_store, initialize_market_store, upsert_observation
from .private_pipeline_contracts import MarketFactV1, content_hash, load_source_registry


ADAPTER_SCHEMA = "market_fact_adapter/1.0"
ADAPTER_VERSION = "market-fact-adapter-v1"
ADAPTER_WATERMARK_SCHEMA = "market_fact_adapter_watermark/1.0"
FEED_MODES = frozenset({"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"})
MAX_DELIVERIES_PER_CYCLE = 500
MAX_ADAPTER_STREAMS = 128
MAX_FUTURE_SKEW_SECONDS = 30
_REASON = re.compile(r"[^A-Z0-9_]+")


class MarketFactAdapterError(RuntimeError):
    """Payload-free adapter failure."""


class MarketFactMappingError(ValueError):
    """One valid wire fact cannot safely become an estimator observation."""


@dataclass(frozen=True, slots=True)
class AdapterReport:
    selected: int
    applied: int
    audit_only: int
    rejected: int
    duplicate: int


@dataclass(frozen=True, slots=True)
class FeedSelection:
    primary_store: Path
    shadow_store: Path | None
    private_capture_continues: bool = True


@dataclass(frozen=True, slots=True)
class OfferDimensions:
    offer_fact_id: str
    source_code: str
    instrument: str
    market_label: str
    side: str
    settlement: str
    trade_form: str
    offered_price: str
    price_unit: str
    currency: str
    offered_quantity: str | None
    quantity_unit: str | None


def utc_text(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def normalize_feed_mode(value: str | None) -> str:
    mode = str(value or "LEGACY").strip().upper()
    if mode not in FEED_MODES:
        raise MarketFactAdapterError("market_fact_adapter_feed_mode_invalid")
    return mode


def publish_adapter_watermark(
    path: Path | str,
    checkpoints: Mapping[str, int],
) -> bool:
    """Publish a monotonic payload-free checkpoint for receiver compaction."""

    normalized: dict[str, int] = {}
    for stream_id, sequence in checkpoints.items():
        if (
            not isinstance(stream_id, str)
            or not stream_id
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
        ):
            raise MarketFactAdapterError("market_fact_adapter_watermark_invalid")
        normalized[stream_id] = sequence
    if len(normalized) > MAX_ADAPTER_STREAMS:
        raise MarketFactAdapterError("market_fact_adapter_stream_limit_exceeded")

    target = Path(path)
    if target.exists():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MarketFactAdapterError(
                "market_fact_adapter_watermark_unreadable"
            ) from exc
        if (
            not isinstance(current, dict)
            or current.get("schema") != ADAPTER_WATERMARK_SCHEMA
            or not isinstance(current.get("streams"), dict)
        ):
            raise MarketFactAdapterError("market_fact_adapter_watermark_invalid")
        old_streams = current["streams"]
        for stream_id, old_sequence in old_streams.items():
            if (
                stream_id not in normalized
                or isinstance(old_sequence, bool)
                or not isinstance(old_sequence, int)
                or old_sequence < 0
                or normalized[stream_id] < old_sequence
            ):
                raise MarketFactAdapterError(
                    "market_fact_adapter_watermark_regression"
                )
        if old_streams == normalized:
            return False

    from .private_pipeline_foundation import atomic_json_write

    atomic_json_write(
        target,
        {
            "schema": ADAPTER_WATERMARK_SCHEMA,
            "updated_at_utc": utc_text(),
            "streams": normalized,
        },
    )
    return True


def select_estimator_feeds(
    *,
    feed_mode: str,
    legacy_store: Path | str,
    private_store: Path | str,
) -> FeedSelection:
    """Resolve an explicit, reversible estimator feed selection.

    The private receiver/capture lane is deliberately independent from this
    choice and therefore continues in every mode.
    """

    mode = normalize_feed_mode(feed_mode)
    legacy = Path(legacy_store).expanduser().resolve()
    private = Path(private_store).expanduser().resolve()
    if legacy == private:
        raise MarketFactAdapterError("market_fact_adapter_feed_paths_must_differ")
    if mode == "LEGACY":
        return FeedSelection(primary_store=legacy, shadow_store=None)
    if mode == "PRIVATE_SHADOW":
        return FeedSelection(primary_store=legacy, shadow_store=private)
    return FeedSelection(primary_store=private, shadow_store=private)


def connect_receiver_read_only(path: Path | str) -> sqlite3.Connection:
    database = Path(path)
    if not database.is_file():
        raise MarketFactAdapterError("market_fact_adapter_receiver_unavailable")
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro", uri=True, timeout=5, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("SELECT 1 FROM fact_deliveries LIMIT 1").fetchone()
        connection.execute("SELECT 1 FROM fact_checkpoints LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        try:
            connection.close()
        except UnboundLocalError:
            pass
        raise MarketFactAdapterError(
            "market_fact_adapter_receiver_unavailable"
        ) from exc
    return connection


def initialize_adapter_store(connection: sqlite3.Connection) -> None:
    initialize_market_store(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS private_fact_adapter_checkpoints (
            stream_id TEXT PRIMARY KEY,
            highest_delivery_sequence INTEGER NOT NULL CHECK(highest_delivery_sequence>=0),
            updated_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS private_fact_adapter_deliveries (
            stream_id TEXT NOT NULL,
            delivery_sequence INTEGER NOT NULL CHECK(delivery_sequence>0),
            fact_id TEXT NOT NULL,
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('APPLIED','AUDIT_ONLY','REJECTED')),
            reason_code TEXT,
            applied_at_utc TEXT NOT NULL,
            PRIMARY KEY(stream_id,delivery_sequence)
        );
        CREATE TABLE IF NOT EXISTS private_fact_adapter_status_counts (
            status TEXT PRIMARY KEY CHECK(status IN ('APPLIED','AUDIT_ONLY','REJECTED')),
            delivery_count INTEGER NOT NULL CHECK(delivery_count>=0)
        );
        INSERT OR IGNORE INTO private_fact_adapter_status_counts(status,delivery_count)
        VALUES ('APPLIED',0),('AUDIT_ONLY',0),('REJECTED',0);
        CREATE TRIGGER IF NOT EXISTS private_fact_adapter_delivery_count_insert
        AFTER INSERT ON private_fact_adapter_deliveries
        BEGIN
          UPDATE private_fact_adapter_status_counts
          SET delivery_count=delivery_count+1
          WHERE status=NEW.status;
        END;
        CREATE TABLE IF NOT EXISTS private_fact_adapter_projections (
            fact_id TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL,
            source_sequence INTEGER NOT NULL CHECK(source_sequence>0),
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            event_key BLOB NOT NULL CHECK(length(event_key)=32),
            payload_hash TEXT NOT NULL,
            quality_state TEXT,
            envelope_hash TEXT,
            status TEXT NOT NULL CHECK(status IN ('APPLIED','AUDIT_ONLY','REJECTED')),
            occurred_at_utc TEXT NOT NULL,
            available_at_utc TEXT NOT NULL,
            parsed_at_utc TEXT NOT NULL,
            transferred_at_utc TEXT NOT NULL,
            adapted_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(stream_id,source_sequence)
        );
        CREATE INDEX IF NOT EXISTS private_fact_adapter_projections_event_key_idx
        ON private_fact_adapter_projections(event_key);
        CREATE TABLE IF NOT EXISTS private_fact_adapter_projection_revisions (
            fact_id TEXT NOT NULL,
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            stream_id TEXT NOT NULL,
            source_sequence INTEGER NOT NULL CHECK(source_sequence>0),
            delivery_sequence INTEGER NOT NULL CHECK(delivery_sequence>0),
            event_key BLOB NOT NULL CHECK(length(event_key)=32),
            payload_hash TEXT NOT NULL,
            quality_state TEXT NOT NULL,
            envelope_hash TEXT NOT NULL CHECK(length(envelope_hash)=64),
            status TEXT NOT NULL CHECK(status IN ('APPLIED','AUDIT_ONLY','REJECTED')),
            occurred_at_utc TEXT NOT NULL,
            available_at_utc TEXT NOT NULL,
            parsed_at_utc TEXT NOT NULL,
            transferred_at_utc TEXT NOT NULL,
            adapted_at_utc TEXT NOT NULL,
            PRIMARY KEY(fact_id,fact_revision),
            UNIQUE(stream_id,delivery_sequence)
        );
        CREATE TABLE IF NOT EXISTS private_fact_adapter_migrations (
            migration_code TEXT PRIMARY KEY,
            completed_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS private_fact_adapter_offer_dimensions (
            offer_fact_id TEXT PRIMARY KEY,
            source_code TEXT NOT NULL,
            instrument TEXT NOT NULL,
            market_label TEXT NOT NULL,
            side TEXT NOT NULL,
            settlement TEXT NOT NULL,
            trade_form TEXT NOT NULL,
            offered_price TEXT NOT NULL,
            price_unit TEXT NOT NULL,
            currency TEXT NOT NULL,
            offered_quantity TEXT,
            quantity_unit TEXT,
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            updated_at_utc TEXT NOT NULL,
            CHECK((offered_quantity IS NULL)=(quantity_unit IS NULL))
        );
        CREATE TABLE IF NOT EXISTS private_fact_adapter_rejections (
            stream_id TEXT NOT NULL,
            delivery_sequence INTEGER NOT NULL,
            fact_id TEXT,
            fact_revision INTEGER,
            body_hash TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            rejected_at_utc TEXT NOT NULL,
            PRIMARY KEY(stream_id,delivery_sequence)
        );
        CREATE INDEX IF NOT EXISTS private_fact_adapter_rejections_time_idx
        ON private_fact_adapter_rejections(rejected_at_utc);
        """
    )
    projection_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(private_fact_adapter_projections)"
        )
    }
    if "quality_state" not in projection_columns:
        connection.execute(
            "ALTER TABLE private_fact_adapter_projections "
            "ADD COLUMN quality_state TEXT"
        )
    if "envelope_hash" not in projection_columns:
        connection.execute(
            "ALTER TABLE private_fact_adapter_projections "
            "ADD COLUMN envelope_hash TEXT"
        )
    # Existing stores predate the insert trigger. Reconcile once at owner
    # startup; steady-state health updates then read three constant-size rows
    # instead of rescanning the permanently growing delivery ledger.
    connection.execute(
        """
        UPDATE private_fact_adapter_status_counts
        SET delivery_count=(
          SELECT COUNT(*)
          FROM private_fact_adapter_deliveries
          WHERE private_fact_adapter_deliveries.status=
                private_fact_adapter_status_counts.status
        )
        """
    )
    connection.commit()


def _quality(value: str) -> str:
    return {
        "ELIGIBLE": "ELIGIBLE",
        "REVIEW": "PENDING_REVIEW",
        "REJECTED": "REJECTED",
        "AUDIT_ONLY": "IGNORED",
    }[value]


def _source_family(source_code: str) -> str:
    source = load_source_registry().by_code().get(source_code)
    if source is None:
        raise MarketFactMappingError("market_fact_adapter_source_unknown")
    return {
        "TELEGRAM_GROUP": "GROUP",
        "TELEGRAM_PUBLIC": "TELEGRAM_PUBLIC",
        "TELEGRAM_PRIVATE": "TELEGRAM_PRIVATE",
        "EXTERNAL_API": "EXTERNAL_MARKET",
        "DERIVED": "TELEGRAM_PRIVATE",
    }.get(source.source_family, "EXTERNAL_MARKET")


def _market_label(
    *, source_code: str, instrument: str, trade_form: str
) -> str:
    if source_code in {"GROUP_1", "GROUP_2"}:
        return "GROUP_" + instrument
    if source_code in {"PRIVATE_GOLD_CHANNEL", "PRIVATE_GOLD_PAPER_MINUTE"}:
        if trade_form == "PHYSICAL":
            return "PRIVATE_GOLD_PHYSICAL"
        if trade_form.startswith("PAPER_"):
            return "PRIVATE_GOLD_" + trade_form
    if source_code == "USD_HERAT":
        return "HERAT_PHYSICAL" if trade_form == "PHYSICAL" else "HERAT_PAPER"
    if source_code == "MELTED_AGGREGATE":
        if instrument == "MELTED_GOLD_UNION":
            return "UNION_QUOTE"
        return "MELTED_PHYSICAL" if trade_form == "PHYSICAL" else "MELTED_PAPER"
    if source_code == "MELTED_FLOW":
        return "MELTED_PAPER_FLOW"
    if source_code == "XAUUSD":
        return "GLOBAL_SPOT"
    return "EXTERNAL_REFERENCE"


def _offer_from_row(row: sqlite3.Row) -> OfferDimensions:
    return OfferDimensions(
        offer_fact_id=str(row["offer_fact_id"]),
        source_code=str(row["source_code"]),
        instrument=str(row["instrument"]),
        market_label=str(row["market_label"]),
        side=str(row["side"]),
        settlement=str(row["settlement"]),
        trade_form=str(row["trade_form"]),
        offered_price=str(row["offered_price"]),
        price_unit=str(row["price_unit"]),
        currency=str(row["currency"]),
        offered_quantity=(
            str(row["offered_quantity"])
            if row["offered_quantity"] is not None
            else None
        ),
        quantity_unit=(
            str(row["quantity_unit"]) if row["quantity_unit"] is not None else None
        ),
    )


def _load_offer(connection: sqlite3.Connection, offer_fact_id: str) -> OfferDimensions:
    row = connection.execute(
        "SELECT * FROM private_fact_adapter_offer_dimensions WHERE offer_fact_id=?",
        (offer_fact_id,),
    ).fetchone()
    if row is None:
        raise MarketFactMappingError("market_fact_adapter_offer_reference_missing")
    return _offer_from_row(row)


def _observation(
    fact: MarketFactV1,
    *,
    delivery_sequence: int,
    destination: sqlite3.Connection,
) -> tuple[MarketObservation | None, OfferDimensions | None, str]:
    payload = fact.payload
    common: dict[str, Any] = {
        "event_key": bytes.fromhex(fact.event_key),
        "source_code": fact.source_code,
        "source_family": _source_family(fact.source_code),
        "event_time_utc": fact.occurred_at_utc,
        "available_at_utc": fact.available_at_utc,
        "parse_confidence": 1.0 if fact.quality_state == "ELIGIBLE" else 0.65,
        "parser_version": fact.parser_version,
        "quality_state": _quality(fact.quality_state),
        "quality_policy_version": ADAPTER_VERSION,
        "is_conditional": "CONDITIONAL_MARKET_FACT" in fact.quality_reason_codes,
        "attributes": {
            "transfer_fact_id": fact.fact_id,
            "fact_revision": fact.fact_revision,
            "delivery_sequence": delivery_sequence,
            "quality_reason_codes": list(fact.quality_reason_codes),
        },
    }
    offer_dimensions: OfferDimensions | None = None
    if payload.kind == "COIN_OFFER":
        label = _market_label(
            source_code=fact.source_code,
            instrument=payload.instrument,
            trade_form=payload.trade_form,
        )
        offer_dimensions = OfferDimensions(
            offer_fact_id=fact.fact_id,
            source_code=fact.source_code,
            instrument=payload.instrument,
            market_label=label,
            side=payload.side,
            settlement=payload.settlement,
            trade_form=payload.trade_form,
            offered_price=payload.offered_price_value,
            price_unit=payload.price_unit,
            currency="TOMAN",
            offered_quantity=payload.quantity_value,
            quantity_unit=payload.quantity_unit,
        )
        return (
            MarketObservation(
                instrument=payload.instrument,
                market_label=label,
                settlement_term=payload.settlement,
                trade_form=payload.trade_form,
                event_type="OFFER",
                side=payload.side,
                price=payload.offered_price_value,
                price_unit=payload.price_unit,
                currency="TOMAN",
                quantity=payload.quantity_value,
                quantity_unit=payload.quantity_unit,
                **common,
            ),
            offer_dimensions,
            "APPLIED",
        )
    if payload.kind == "COIN_TRADE":
        if payload.outcome not in {"CONFIRMED_FULL", "CONFIRMED_PARTIAL"}:
            return None, None, "AUDIT_ONLY"
        root = _load_offer(destination, payload.offer_fact_id)
        if root.source_code != fact.source_code or root.price_unit != payload.price_unit:
            raise MarketFactMappingError("market_fact_adapter_offer_reference_mismatch")
        return (
            MarketObservation(
                instrument=root.instrument,
                market_label=root.market_label,
                settlement_term=root.settlement,
                trade_form=root.trade_form,
                event_type="TRADE",
                side=root.side,
                price=payload.agreed_price_value,
                price_unit=payload.price_unit,
                currency="TOMAN",
                quantity=payload.agreed_quantity_value,
                quantity_unit=payload.quantity_unit,
                attributes={
                    **common["attributes"],
                    "root_offer_fact_id": payload.offer_fact_id,
                    "trade_outcome": payload.outcome,
                },
                **{key: value for key, value in common.items() if key != "attributes"},
            ),
            None,
            "APPLIED",
        )
    if payload.kind == "PRIVATE_GOLD_OFFER":
        label = _market_label(
            source_code=fact.source_code,
            instrument=payload.instrument,
            trade_form=payload.trade_form,
        )
        offer_dimensions = OfferDimensions(
            offer_fact_id=fact.fact_id,
            source_code=fact.source_code,
            instrument=payload.instrument,
            market_label=label,
            side=payload.side,
            settlement=payload.settlement,
            trade_form=payload.trade_form,
            offered_price=payload.offered_price_value,
            price_unit=payload.price_unit,
            currency="TOMAN",
            offered_quantity=payload.quantity_value,
            quantity_unit=payload.quantity_unit,
        )
        return (
            MarketObservation(
                instrument=payload.instrument,
                market_label=label,
                settlement_term=payload.settlement,
                trade_form=payload.trade_form,
                event_type="OFFER",
                side=payload.side,
                price=payload.offered_price_value,
                price_unit=payload.price_unit,
                currency="TOMAN",
                quantity=payload.quantity_value,
                quantity_unit=payload.quantity_unit,
                attributes={
                    **common["attributes"],
                    "lifetime_seconds": payload.lifetime_seconds,
                },
                **{key: value for key, value in common.items() if key != "attributes"},
            ),
            offer_dimensions,
            "APPLIED",
        )
    if payload.kind == "PRIVATE_GOLD_OUTCOME":
        if payload.outcome in {"NO_TRADE", "AMBIGUOUS"}:
            return None, None, "AUDIT_ONLY"
        root = _load_offer(destination, payload.offer_fact_id)
        if root.source_code != fact.source_code:
            raise MarketFactMappingError("market_fact_adapter_offer_reference_mismatch")
        quantity = payload.executed_quantity_value
        if quantity is None and payload.outcome == "FULL":
            quantity = root.offered_quantity
        if quantity is None and payload.remaining_quantity_value is not None:
            if root.offered_quantity is None:
                raise MarketFactMappingError(
                    "market_fact_adapter_executed_quantity_unavailable"
                )
            try:
                executed = Decimal(root.offered_quantity) - Decimal(
                    payload.remaining_quantity_value
                )
            except InvalidOperation as exc:
                raise MarketFactMappingError(
                    "market_fact_adapter_executed_quantity_invalid"
                ) from exc
            if executed <= 0:
                raise MarketFactMappingError(
                    "market_fact_adapter_executed_quantity_invalid"
                )
            quantity = format(executed, "f")
        if quantity is None:
            raise MarketFactMappingError(
                "market_fact_adapter_executed_quantity_unavailable"
            )
        return (
            MarketObservation(
                instrument=root.instrument,
                market_label=root.market_label,
                settlement_term=root.settlement,
                trade_form=root.trade_form,
                event_type="TRADE",
                side=root.side,
                price=root.offered_price,
                price_unit=root.price_unit,
                currency=root.currency,
                quantity=quantity,
                quantity_unit=payload.quantity_unit or root.quantity_unit,
                attributes={
                    **common["attributes"],
                    "root_offer_fact_id": payload.offer_fact_id,
                    "trade_outcome": payload.outcome,
                    "remaining_quantity": payload.remaining_quantity_value,
                },
                **{key: value for key, value in common.items() if key != "attributes"},
            ),
            None,
            "APPLIED",
        )
    if payload.kind == "EXTERNAL_QUOTE":
        side = {"BID": "BUY", "ASK": "SELL", "MID": "MID", "LAST": "UNKNOWN"}[
            payload.quote_kind
        ]
        source_family = _source_family(fact.source_code)
        event_type = "QUOTE" if source_family == "TELEGRAM_PUBLIC" else "REFERENCE"
        return (
            MarketObservation(
                instrument=payload.instrument,
                market_label=_market_label(
                    source_code=fact.source_code,
                    instrument=payload.instrument,
                    trade_form="NOT_APPLICABLE",
                ),
                settlement_term="SPOT",
                trade_form="NOT_APPLICABLE",
                event_type=event_type,
                side=side,
                price=payload.price_value,
                price_unit=payload.price_unit,
                currency=payload.currency,
                attributes={
                    **common["attributes"],
                    "quote_kind": payload.quote_kind,
                },
                **{key: value for key, value in common.items() if key != "attributes"},
            ),
            None,
            "APPLIED",
        )
    if payload.kind == "OBSERVATION":
        return (
            MarketObservation(
                instrument=payload.instrument,
                market_label=_market_label(
                    source_code=fact.source_code,
                    instrument=payload.instrument,
                    trade_form=payload.trade_form,
                ),
                settlement_term=payload.settlement,
                trade_form=payload.trade_form,
                event_type=payload.event_type,
                side=payload.side,
                price=payload.price_value,
                price_unit=payload.price_unit,
                currency=payload.currency,
                quantity=payload.quantity_value,
                quantity_unit=payload.quantity_unit,
                **common,
            ),
            None,
            "APPLIED",
        )
    raise MarketFactMappingError("market_fact_adapter_payload_unsupported")


def _validate_fact_time(fact: MarketFactV1) -> None:
    now = datetime.now(timezone.utc)
    if any(
        (timestamp - now).total_seconds() > MAX_FUTURE_SKEW_SECONDS
        for timestamp in (
            fact.occurred_at_utc,
            fact.available_at_utc,
            fact.persisted_at_utc,
        )
    ):
        raise MarketFactMappingError("market_fact_adapter_timestamp_in_future")


def _store_offer(
    connection: sqlite3.Connection,
    offer: OfferDimensions,
    *,
    fact_revision: int,
) -> None:
    connection.execute(
        """
        INSERT INTO private_fact_adapter_offer_dimensions(
          offer_fact_id,source_code,instrument,market_label,side,settlement,
          trade_form,offered_price,price_unit,currency,offered_quantity,
          quantity_unit,fact_revision,updated_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(offer_fact_id) DO UPDATE SET
          source_code=excluded.source_code,instrument=excluded.instrument,
          market_label=excluded.market_label,side=excluded.side,
          settlement=excluded.settlement,trade_form=excluded.trade_form,
          offered_price=excluded.offered_price,price_unit=excluded.price_unit,
          currency=excluded.currency,offered_quantity=excluded.offered_quantity,
          quantity_unit=excluded.quantity_unit,fact_revision=excluded.fact_revision,
          updated_at_utc=excluded.updated_at_utc
        WHERE excluded.fact_revision>=private_fact_adapter_offer_dimensions.fact_revision
        """,
        (
            offer.offer_fact_id,
            offer.source_code,
            offer.instrument,
            offer.market_label,
            offer.side,
            offer.settlement,
            offer.trade_form,
            offer.offered_price,
            offer.price_unit,
            offer.currency,
            offer.offered_quantity,
            offer.quantity_unit,
            fact_revision,
            utc_text(),
        ),
    )


def _advance(
    connection: sqlite3.Connection,
    *,
    stream_id: str,
    sequence: int,
    fact: MarketFactV1 | None,
    body_hash: str,
    status: str,
    reason_code: str | None,
    transferred_at_utc: str,
) -> None:
    fact_id = fact.fact_id if fact else None
    revision = fact.fact_revision if fact else None
    payload_hash = fact.payload_hash if fact else body_hash
    connection.execute(
        "INSERT INTO private_fact_adapter_deliveries VALUES(?,?,?,?,?,?,?,?)",
        (
            stream_id,
            sequence,
            fact_id or body_hash,
            revision or 1,
            payload_hash,
            status,
            reason_code,
            utc_text(),
        ),
    )
    if fact is not None:
        connection.execute(
            """
            INSERT INTO private_fact_adapter_projections(
              fact_id,stream_id,source_sequence,fact_revision,event_key,
              payload_hash,quality_state,envelope_hash,status,
              occurred_at_utc,available_at_utc,parsed_at_utc,
              transferred_at_utc,adapted_at_utc,updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fact_id) DO UPDATE SET
              stream_id=excluded.stream_id,source_sequence=excluded.source_sequence,
              fact_revision=excluded.fact_revision,event_key=excluded.event_key,
              payload_hash=excluded.payload_hash,
              quality_state=excluded.quality_state,
              envelope_hash=excluded.envelope_hash,status=excluded.status,
              occurred_at_utc=excluded.occurred_at_utc,
              available_at_utc=excluded.available_at_utc,
              parsed_at_utc=excluded.parsed_at_utc,
              transferred_at_utc=excluded.transferred_at_utc,
              adapted_at_utc=excluded.adapted_at_utc,
              updated_at_utc=excluded.updated_at_utc
            WHERE excluded.fact_revision>=private_fact_adapter_projections.fact_revision
            """,
            (
                fact.fact_id,
                stream_id,
                fact.source_sequence,
                fact.fact_revision,
                bytes.fromhex(fact.event_key),
                fact.payload_hash,
                fact.quality_state,
                content_hash(fact.model_dump(mode="json")),
                status,
                utc_text(fact.occurred_at_utc),
                utc_text(fact.available_at_utc),
                utc_text(fact.persisted_at_utc),
                transferred_at_utc,
                utc_text(),
                utc_text(),
            ),
        )
        connection.execute(
            """
            INSERT INTO private_fact_adapter_projection_revisions(
              fact_id,fact_revision,stream_id,source_sequence,
              delivery_sequence,event_key,payload_hash,quality_state,
              envelope_hash,status,
              occurred_at_utc,available_at_utc,parsed_at_utc,
              transferred_at_utc,adapted_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fact_id,fact_revision) DO UPDATE SET
              stream_id=excluded.stream_id,
              source_sequence=excluded.source_sequence,
              delivery_sequence=excluded.delivery_sequence,
              event_key=excluded.event_key,payload_hash=excluded.payload_hash,
              quality_state=excluded.quality_state,
              envelope_hash=excluded.envelope_hash,
              status=excluded.status,occurred_at_utc=excluded.occurred_at_utc,
              available_at_utc=excluded.available_at_utc,
              parsed_at_utc=excluded.parsed_at_utc,
              transferred_at_utc=excluded.transferred_at_utc,
              adapted_at_utc=excluded.adapted_at_utc
            """,
            (
                fact.fact_id,
                fact.fact_revision,
                stream_id,
                fact.source_sequence,
                sequence,
                bytes.fromhex(fact.event_key),
                fact.payload_hash,
                fact.quality_state,
                content_hash(fact.model_dump(mode="json")),
                status,
                utc_text(fact.occurred_at_utc),
                utc_text(fact.available_at_utc),
                utc_text(fact.persisted_at_utc),
                transferred_at_utc,
                utc_text(),
            ),
        )
    connection.execute(
        """
        INSERT INTO private_fact_adapter_checkpoints VALUES(?,?,?)
        ON CONFLICT(stream_id) DO UPDATE SET
          highest_delivery_sequence=excluded.highest_delivery_sequence,
          updated_at_utc=excluded.updated_at_utc
        """,
        (stream_id, sequence, utc_text()),
    )


def _reason_code(error: BaseException) -> str:
    token = _REASON.sub("_", str(error).upper()).strip("_")[:95]
    if not token or not token[0].isalpha():
        return "MARKET_FACT_ADAPTER_MAPPING_REJECTED"
    return token


def _retire_previous_applied_observation(
    connection: sqlite3.Connection,
    *,
    fact: MarketFactV1,
    delivery_sequence: int,
) -> bool:
    """Make an older economic projection non-model-visible on AUDIT_ONLY.

    A later revision can change a previously confirmed trade/outcome into an
    audit-only disposition.  Updating adapter lineage without retiring the
    prior Market Store row would leave stale economics eligible indefinitely.
    """

    previous = connection.execute(
        "SELECT fact_revision,event_key,status FROM private_fact_adapter_projections "
        "WHERE fact_id=?",
        (fact.fact_id,),
    ).fetchone()
    if previous is None or str(previous["status"]) not in {
        "APPLIED",
        "AUDIT_ONLY",
    }:
        return False
    if int(previous["fact_revision"]) >= fact.fact_revision:
        raise MarketFactMappingError(
            "market_fact_adapter_audit_revision_not_newer"
        )
    event_key = bytes(previous["event_key"])
    if event_key.hex() != fact.event_key:
        raise MarketFactMappingError(
            "market_fact_adapter_audit_event_key_mismatch"
        )
    locations: list[tuple[str, sqlite3.Row]] = []
    for table in ("market_observations", "market_observations_archive"):
        row = connection.execute(
            f"SELECT event_time_utc,quality_state,attributes_json "
            f"FROM {table} WHERE event_key=?",
            (event_key,),
        ).fetchone()
        if row is not None:
            locations.append((table, row))
    if str(previous["status"]) == "AUDIT_ONLY" and not locations:
        # A fact which has always been audit-only correctly has no economic
        # observation.  There is nothing to refresh in that case.
        return False
    if len(locations) != 1:
        raise MarketFactMappingError(
            "market_fact_adapter_previous_observation_missing"
            if not locations
            else "market_fact_adapter_previous_observation_duplicated"
        )
    table, row = locations[0]
    try:
        attributes = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError) as exc:
        raise MarketFactMappingError(
            "market_fact_adapter_previous_observation_invalid"
        ) from exc
    if not isinstance(attributes, dict):
        raise MarketFactMappingError(
            "market_fact_adapter_previous_observation_invalid"
        )
    if str(previous["status"]) == "AUDIT_ONLY" and (
        str(row["quality_state"]).upper() != "IGNORED"
        or attributes.get("transfer_fact_id") != fact.fact_id
        or attributes.get("adapter_disposition") != "AUDIT_ONLY"
    ):
        raise MarketFactMappingError(
            "market_fact_adapter_previous_audit_observation_invalid"
        )
    attributes.update(
        {
            "transfer_fact_id": fact.fact_id,
            "fact_revision": fact.fact_revision,
            "delivery_sequence": delivery_sequence,
            "quality_reason_codes": list(fact.quality_reason_codes),
            "adapter_disposition": "AUDIT_ONLY",
            "resolution_reason": "FACT_REVISION_AUDIT_ONLY",
        }
    )
    available = max(
        str(row["event_time_utc"]),
        utc_text(fact.available_at_utc),
    )
    connection.execute(
        f"""
        UPDATE {table}
        SET available_at_utc=?,parse_confidence=0,
            parser_version=?,quality_state='IGNORED',
            quality_policy_version=?,attributes_json=?,inserted_at_utc=?
        WHERE event_key=?
        """,
        (
            available,
            fact.parser_version,
            ADAPTER_VERSION,
            json.dumps(attributes, sort_keys=True, separators=(",", ":")),
            utc_text(),
            event_key,
        ),
    )
    return True


def apply_received_delivery(
    destination: sqlite3.Connection,
    row: sqlite3.Row,
) -> str:
    stream_id = str(row["stream_id"])
    sequence = int(row["delivery_sequence"])
    payload_json = str(row["payload_json"])
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    checkpoint_row = destination.execute(
        "SELECT highest_delivery_sequence FROM private_fact_adapter_checkpoints "
        "WHERE stream_id=?",
        (stream_id,),
    ).fetchone()
    checkpoint = int(checkpoint_row[0]) if checkpoint_row else 0
    if sequence <= checkpoint:
        existing = destination.execute(
            "SELECT fact_id,fact_revision,payload_hash FROM private_fact_adapter_deliveries "
            "WHERE stream_id=? AND delivery_sequence=?",
            (stream_id, sequence),
        ).fetchone()
        if existing is None:
            raise MarketFactAdapterError("market_fact_adapter_checkpoint_inconsistent")
        if (
            str(existing["fact_id"]) != str(row["fact_id"])
            or int(existing["fact_revision"]) != int(row["fact_revision"])
            or str(existing["payload_hash"]) != str(row["payload_hash"])
        ):
            raise MarketFactAdapterError("market_fact_adapter_delivery_conflict")
        return "DUPLICATE"
    if row["payload_compacted_at_utc"] is not None:
        raise MarketFactAdapterError(
            "market_fact_adapter_payload_compacted_before_checkpoint"
        )
    if sequence != checkpoint + 1:
        raise MarketFactAdapterError("market_fact_adapter_sequence_gap")

    fact: MarketFactV1 | None = None
    try:
        fact = MarketFactV1.model_validate_json(payload_json)
        if (
            fact.fact_id != str(row["fact_id"])
            or fact.fact_revision != int(row["fact_revision"])
            or fact.payload_hash != str(row["payload_hash"])
            or fact.stream_id != stream_id
        ):
            raise MarketFactMappingError("market_fact_adapter_receiver_row_mismatch")
        _validate_fact_time(fact)
        destination.execute("BEGIN IMMEDIATE")
        observation, offer, status = _observation(
            fact, delivery_sequence=sequence, destination=destination
        )
        if observation is not None:
            observation.normalized()
            upsert_observation(destination, observation)
        elif status == "AUDIT_ONLY":
            _retire_previous_applied_observation(
                destination,
                fact=fact,
                delivery_sequence=sequence,
            )
        if offer is not None:
            _store_offer(destination, offer, fact_revision=fact.fact_revision)
        _advance(
            destination,
            stream_id=stream_id,
            sequence=sequence,
            fact=fact,
            body_hash=body_hash,
            status=status,
            reason_code=None,
            transferred_at_utc=str(row["received_at_utc"]),
        )
        destination.commit()
        return status
    except (ValidationError, MarketFactMappingError, MarketStoreContractError, ValueError) as exc:
        destination.rollback()
        reason = _reason_code(exc)
        destination.execute("BEGIN IMMEDIATE")
        destination.execute(
            "INSERT INTO private_fact_adapter_rejections VALUES(?,?,?,?,?,?,?)",
            (
                stream_id,
                sequence,
                fact.fact_id if fact else None,
                fact.fact_revision if fact else None,
                body_hash,
                reason,
                utc_text(),
            ),
        )
        _advance(
            destination,
            stream_id=stream_id,
            sequence=sequence,
            fact=fact,
            body_hash=body_hash,
            status="REJECTED",
            reason_code=reason,
            transferred_at_utc=str(row["received_at_utc"]),
        )
        destination.commit()
        return "REJECTED"
    except BaseException:
        destination.rollback()
        raise


def run_adapter_cycle(
    receiver: sqlite3.Connection,
    destination: sqlite3.Connection,
    *,
    max_deliveries: int = MAX_DELIVERIES_PER_CYCLE,
) -> AdapterReport:
    if not 1 <= max_deliveries <= 5_000:
        raise MarketFactAdapterError("market_fact_adapter_batch_limit_invalid")
    _backfill_projection_revision_history(receiver, destination)
    _retire_stale_audit_only_observations(destination)
    checkpoints = {
        str(row["stream_id"]): int(row["highest_delivery_sequence"])
        for row in destination.execute(
            "SELECT stream_id,highest_delivery_sequence FROM private_fact_adapter_checkpoints"
        ).fetchall()
    }
    selected = _select_pending_deliveries(
        receiver,
        checkpoints=checkpoints,
        max_deliveries=max_deliveries,
    )
    counts = {"APPLIED": 0, "AUDIT_ONLY": 0, "REJECTED": 0, "DUPLICATE": 0}
    for row in selected:
        counts[apply_received_delivery(destination, row)] += 1
    return AdapterReport(
        selected=len(selected),
        applied=counts["APPLIED"],
        audit_only=counts["AUDIT_ONLY"],
        rejected=counts["REJECTED"],
        duplicate=counts["DUPLICATE"],
    )


def _backfill_projection_revision_history(
    receiver: sqlite3.Connection,
    destination: sqlite3.Connection,
) -> None:
    """One-time upgrade of the durable per-revision adapter lineage.

    Older adapter stores retained every terminal delivery but only the latest
    projection.  The receiver still holds each seven-day fact envelope; use it
    once to reconstruct the missing value-free revision identity before any
    new delivery advances the checkpoint.  Already-compacted legacy rows are
    left absent; a cutoff-scoped promotion audit will fail closed if one is
    still required for the release being proved.
    """

    migration = "projection-revision-history-v1"
    if destination.execute(
        "SELECT 1 FROM private_fact_adapter_migrations WHERE migration_code=?",
        (migration,),
    ).fetchone() is not None:
        return
    deliveries = destination.execute(
        "SELECT stream_id,delivery_sequence,fact_id,fact_revision,payload_hash,status "
        "FROM private_fact_adapter_deliveries ORDER BY stream_id,delivery_sequence"
    ).fetchall()
    destination.execute("BEGIN IMMEDIATE")
    try:
        for delivery in deliveries:
            if destination.execute(
                "SELECT 1 FROM private_fact_adapter_projection_revisions "
                "WHERE fact_id=? AND fact_revision=?",
                (str(delivery["fact_id"]), int(delivery["fact_revision"])),
            ).fetchone() is not None:
                continue
            received = receiver.execute(
                "SELECT payload_json,payload_compacted_at_utc,received_at_utc "
                "FROM fact_deliveries WHERE stream_id=? AND delivery_sequence=? "
                "AND fact_id=? AND fact_revision=? AND payload_hash=?",
                (
                    str(delivery["stream_id"]),
                    int(delivery["delivery_sequence"]),
                    str(delivery["fact_id"]),
                    int(delivery["fact_revision"]),
                    str(delivery["payload_hash"]),
                ),
            ).fetchone()
            if (
                received is None
            ):
                raise MarketFactAdapterError(
                    "market_fact_adapter_revision_history_unrecoverable"
                )
            if received["payload_compacted_at_utc"] is not None:
                continue
            if not str(received["payload_json"] or ""):
                raise MarketFactAdapterError(
                    "market_fact_adapter_revision_history_unrecoverable"
                )
            try:
                fact = MarketFactV1.model_validate_json(str(received["payload_json"]))
            except ValidationError as exc:
                rejection = destination.execute(
                    "SELECT body_hash FROM private_fact_adapter_rejections "
                    "WHERE stream_id=? AND delivery_sequence=?",
                    (
                        str(delivery["stream_id"]),
                        int(delivery["delivery_sequence"]),
                    ),
                ).fetchone()
                if (
                    str(delivery["status"]) == "REJECTED"
                    and rejection is not None
                    and str(rejection["body_hash"])
                    == str(delivery["payload_hash"])
                ):
                    continue
                raise MarketFactAdapterError(
                    "market_fact_adapter_revision_history_invalid"
                ) from exc
            if (
                fact.fact_id != str(delivery["fact_id"])
                or fact.fact_revision != int(delivery["fact_revision"])
                or fact.payload_hash != str(delivery["payload_hash"])
                or fact.stream_id != str(delivery["stream_id"])
            ):
                raise MarketFactAdapterError(
                    "market_fact_adapter_revision_history_conflict"
                )
            destination.execute(
                """
                INSERT INTO private_fact_adapter_projection_revisions(
                  fact_id,fact_revision,stream_id,source_sequence,
                  delivery_sequence,event_key,payload_hash,quality_state,
                  envelope_hash,status,
                  occurred_at_utc,available_at_utc,parsed_at_utc,
                  transferred_at_utc,adapted_at_utc
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fact.fact_id,
                    fact.fact_revision,
                    fact.stream_id,
                    fact.source_sequence,
                    int(delivery["delivery_sequence"]),
                    bytes.fromhex(fact.event_key),
                    fact.payload_hash,
                    fact.quality_state,
                    content_hash(fact.model_dump(mode="json")),
                    str(delivery["status"]),
                    utc_text(fact.occurred_at_utc),
                    utc_text(fact.available_at_utc),
                    utc_text(fact.persisted_at_utc),
                    str(received["received_at_utc"]),
                    utc_text(),
                ),
            )
            destination.execute(
                "UPDATE private_fact_adapter_projections "
                "SET quality_state=?,envelope_hash=? "
                "WHERE fact_id=? AND fact_revision=?",
                (
                    fact.quality_state,
                    content_hash(fact.model_dump(mode="json")),
                    fact.fact_id,
                    fact.fact_revision,
                ),
            )
        destination.execute(
            "INSERT INTO private_fact_adapter_migrations VALUES(?,?)",
            (migration, utc_text()),
        )
        destination.commit()
    except BaseException:
        destination.rollback()
        raise


def _retire_stale_audit_only_observations(
    destination: sqlite3.Connection,
) -> None:
    """Repair observations left eligible by pre-retirement adapter releases.

    The wire projection is the durable authority for the latest fact revision.
    Releases predating ``_retire_previous_applied_observation`` advanced that
    lineage to ``AUDIT_ONLY`` but could leave the older economic observation
    eligible.  Reconcile that bounded mismatch once during upgrade; future
    revisions are retired synchronously in ``apply_received_delivery``.
    """

    # v2 also refreshes metadata when several consecutive revisions remain
    # AUDIT_ONLY; v1 only handled the initial APPLIED -> AUDIT_ONLY edge.
    migration = "retire-stale-audit-only-observations-v2"
    destination.execute("BEGIN IMMEDIATE")
    try:
        if destination.execute(
            "SELECT 1 FROM private_fact_adapter_migrations WHERE migration_code=?",
            (migration,),
        ).fetchone() is not None:
            destination.commit()
            return
        projections = destination.execute(
            "SELECT fact_id,fact_revision,event_key,available_at_utc "
            "FROM private_fact_adapter_projections WHERE status='AUDIT_ONLY' "
            "ORDER BY fact_id"
        ).fetchall()
        for projection in projections:
            event_key = bytes(projection["event_key"])
            locations: list[tuple[str, sqlite3.Row]] = []
            for table in ("market_observations", "market_observations_archive"):
                row = destination.execute(
                    f"SELECT event_time_utc,quality_state,attributes_json "
                    f"FROM {table} WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if row is not None:
                    locations.append((table, row))
            # A fact that was audit-only from its first revision correctly has
            # no economic observation to retire.
            if not locations:
                continue
            if len(locations) != 1:
                raise MarketFactAdapterError(
                    "market_fact_adapter_audit_observation_duplicated"
                )
            table, row = locations[0]
            try:
                attributes = json.loads(str(row["attributes_json"] or "{}"))
            except (TypeError, ValueError) as exc:
                raise MarketFactAdapterError(
                    "market_fact_adapter_audit_observation_invalid"
                ) from exc
            if not isinstance(attributes, dict):
                raise MarketFactAdapterError(
                    "market_fact_adapter_audit_observation_invalid"
                )
            existing_fact_id = str(attributes.get("transfer_fact_id") or "")
            if existing_fact_id and existing_fact_id != str(projection["fact_id"]):
                raise MarketFactAdapterError(
                    "market_fact_adapter_audit_observation_fact_mismatch"
                )
            revision = int(projection["fact_revision"])
            if (
                str(row["quality_state"]).upper() == "IGNORED"
                and int(attributes.get("fact_revision") or 0) >= revision
            ):
                continue
            revision_row = destination.execute(
                "SELECT delivery_sequence FROM "
                "private_fact_adapter_projection_revisions "
                "WHERE fact_id=? AND fact_revision=?",
                (str(projection["fact_id"]), revision),
            ).fetchone()
            attributes.update(
                {
                    "transfer_fact_id": str(projection["fact_id"]),
                    "fact_revision": revision,
                    "adapter_disposition": "AUDIT_ONLY",
                    "resolution_reason": "FACT_REVISION_AUDIT_ONLY",
                    "quality_reason_codes": ["FACT_REVISION_AUDIT_ONLY"],
                }
            )
            if revision_row is not None:
                attributes["delivery_sequence"] = int(
                    revision_row["delivery_sequence"]
                )
            available = max(
                str(row["event_time_utc"]),
                str(projection["available_at_utc"]),
            )
            destination.execute(
                f"""
                UPDATE {table}
                SET available_at_utc=?,parse_confidence=0,
                    quality_state='IGNORED',quality_policy_version=?,
                    attributes_json=?,inserted_at_utc=?
                WHERE event_key=?
                """,
                (
                    available,
                    ADAPTER_VERSION,
                    json.dumps(
                        attributes, sort_keys=True, separators=(",", ":")
                    ),
                    utc_text(),
                    event_key,
                ),
            )
        destination.execute(
            "INSERT INTO private_fact_adapter_migrations VALUES(?,?)",
            (migration, utc_text()),
        )
        destination.commit()
    except BaseException:
        destination.rollback()
        raise


def _select_pending_deliveries(
    receiver: sqlite3.Connection,
    *,
    checkpoints: Mapping[str, int],
    max_deliveries: int,
) -> list[sqlite3.Row]:
    """Select a bounded, causal batch without sorting the whole inbox.

    ``fact_deliveries`` can grow permanently while the adapter is stopped.
    Sorting every payload by receipt time therefore spills into SQLite's temp
    directory and can exhaust the deliberately small container tmpfs.  The
    primary key already gives an efficient contiguous sequence per stream.
    Fetch a bounded prefix from each stream, then merge only those prefixes in
    memory while never exposing sequence N+1 before sequence N.
    """

    stream_ids = [
        str(row["stream_id"])
        for row in receiver.execute(
            "SELECT stream_id FROM fact_checkpoints ORDER BY stream_id"
        ).fetchall()
    ]
    if len(stream_ids) > MAX_ADAPTER_STREAMS:
        raise MarketFactAdapterError("market_fact_adapter_stream_limit_exceeded")
    cursors: dict[str, sqlite3.Cursor] = {}
    heads: dict[str, sqlite3.Row] = {}
    next_sequences: dict[str, int] = {}
    heap: list[tuple[str, str, int]] = []
    selected: list[sqlite3.Row] = []
    try:
        for stream_id in stream_ids:
            cursor = receiver.execute(
                """
                SELECT stream_id,delivery_sequence,fact_id,fact_revision,payload_hash,
                       payload_json,received_at_utc,payload_compacted_at_utc
                FROM fact_deliveries
                WHERE stream_id=? AND delivery_sequence>?
                ORDER BY delivery_sequence
                LIMIT ?
                """,
                (stream_id, int(checkpoints.get(stream_id, 0)), max_deliveries),
            )
            cursors[stream_id] = cursor
            head = cursor.fetchone()
            if head is None:
                continue
            heads[stream_id] = head
            next_sequences[stream_id] = int(checkpoints.get(stream_id, 0)) + 1
            heapq.heappush(
                heap,
                (
                    str(head["received_at_utc"]),
                    stream_id,
                    int(head["delivery_sequence"]),
                ),
            )
        while heap and len(selected) < max_deliveries:
            _received_at, stream_id, sequence = heapq.heappop(heap)
            row = heads[stream_id]
            expected = next_sequences[stream_id]
            if sequence != expected or int(row["delivery_sequence"]) != expected:
                raise MarketFactAdapterError("market_fact_adapter_sequence_gap")
            selected.append(row)
            next_sequences[stream_id] = expected + 1
            following = cursors[stream_id].fetchone()
            if following is not None:
                heads[stream_id] = following
                heapq.heappush(
                    heap,
                    (
                        str(following["received_at_utc"]),
                        stream_id,
                        int(following["delivery_sequence"]),
                    ),
                )
    finally:
        for cursor in cursors.values():
            cursor.close()
    return selected


def adapter_metrics(connection: sqlite3.Connection) -> dict[str, object]:
    checkpoints = {
        str(row["stream_id"]): int(row["highest_delivery_sequence"])
        for row in connection.execute(
            "SELECT stream_id,highest_delivery_sequence FROM private_fact_adapter_checkpoints"
        ).fetchall()
    }
    counts = {
        str(row["status"]): int(row["delivery_count"])
        for row in connection.execute(
            "SELECT status,delivery_count FROM private_fact_adapter_status_counts"
        ).fetchall()
    }
    return {
        "streams": checkpoints,
        "applied_count": counts.get("APPLIED", 0),
        "audit_only_count": counts.get("AUDIT_ONLY", 0),
        "rejected_count": counts.get("REJECTED", 0),
    }


def run_market_fact_adapter_service(
    *,
    role: str,
    mode: str,
    release_sha: str,
    state_directory: Path,
    stop: threading.Event,
) -> int:
    if role != "market-store-adapter" or mode != "live":
        raise MarketFactAdapterError("market_fact_adapter_role_or_mode_invalid")
    feed_mode = normalize_feed_mode(os.environ.get("MARKET_PIPELINE_FEED_MODE"))
    market_path = Path(
        os.environ.get(
            "MARKET_PIPELINE_MARKET_STORE_PATH",
            "/var/lib/market-data/market-store/market-store.sqlite",
        )
    )
    receiver_path = Path(
        os.environ.get(
            "MARKET_PIPELINE_RECEIVER_DB_PATH",
            "/var/lib/market-data/receiver/market-fact-receiver/market-fact-receiver.sqlite3",
        )
    )
    receiver_watermark_path = receiver_path.with_name(
        "adapter-consumption-watermark.json"
    )
    market = connect_market_store(market_path)
    initialize_adapter_store(market)
    from .private_pipeline_foundation import atomic_json_write

    # Validate the persisted watermark before reading any inbox payload.  A
    # Market Store restored behind a previously compacted watermark must stop
    # for explicit recovery instead of treating redacted rows as malformed.
    initial_metrics = adapter_metrics(market)
    publish_adapter_watermark(receiver_watermark_path, initial_metrics["streams"])
    started = utc_text()
    try:
        while not stop.is_set():
            report = AdapterReport(0, 0, 0, 0, 0)
            if feed_mode != "LEGACY":
                receiver = connect_receiver_read_only(receiver_path)
                try:
                    report = run_adapter_cycle(receiver, market)
                finally:
                    receiver.close()
            metrics = adapter_metrics(market)
            publish_adapter_watermark(receiver_watermark_path, metrics["streams"])
            atomic_json_write(
                state_directory / "health.json",
                {
                    "schema": ADAPTER_SCHEMA,
                    "role": role,
                    "mode": mode,
                    "release_sha": release_sha,
                    "pid": os.getpid(),
                    "started_at_utc": started,
                    "updated_at_utc": utc_text(),
                    "status": "live-ready",
                    "durable_write": True,
                    "feed_mode": feed_mode,
                    "consuming_private_facts": feed_mode != "LEGACY",
                    "last_cycle": asdict(report),
                    **metrics,
                },
            )
            stop.wait(0.25 if report.selected else 1.0)
    finally:
        market.close()
    return 0


__all__ = [
    "ADAPTER_SCHEMA",
    "ADAPTER_VERSION",
    "AdapterReport",
    "FeedSelection",
    "MarketFactAdapterError",
    "apply_received_delivery",
    "connect_receiver_read_only",
    "initialize_adapter_store",
    "normalize_feed_mode",
    "run_adapter_cycle",
    "run_market_fact_adapter_service",
    "select_estimator_feeds",
]
