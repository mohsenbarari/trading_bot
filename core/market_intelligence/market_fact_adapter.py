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
from .private_pipeline_contracts import MarketFactV1, load_source_registry


ADAPTER_SCHEMA = "market_fact_adapter/1.0"
ADAPTER_VERSION = "market-fact-adapter-v1"
FEED_MODES = frozenset({"LEGACY", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"})
MAX_DELIVERIES_PER_CYCLE = 500
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
        CREATE TABLE IF NOT EXISTS private_fact_adapter_projections (
            fact_id TEXT PRIMARY KEY,
            stream_id TEXT NOT NULL,
            source_sequence INTEGER NOT NULL CHECK(source_sequence>0),
            fact_revision INTEGER NOT NULL CHECK(fact_revision>0),
            event_key BLOB NOT NULL CHECK(length(event_key)=32),
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('APPLIED','AUDIT_ONLY','REJECTED')),
            occurred_at_utc TEXT NOT NULL,
            available_at_utc TEXT NOT NULL,
            parsed_at_utc TEXT NOT NULL,
            transferred_at_utc TEXT NOT NULL,
            adapted_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(stream_id,source_sequence)
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
              payload_hash,status,occurred_at_utc,available_at_utc,parsed_at_utc,
              transferred_at_utc,adapted_at_utc,updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fact_id) DO UPDATE SET
              stream_id=excluded.stream_id,source_sequence=excluded.source_sequence,
              fact_revision=excluded.fact_revision,event_key=excluded.event_key,
              payload_hash=excluded.payload_hash,status=excluded.status,
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
    checkpoints = {
        str(row["stream_id"]): int(row["highest_delivery_sequence"])
        for row in destination.execute(
            "SELECT stream_id,highest_delivery_sequence FROM private_fact_adapter_checkpoints"
        ).fetchall()
    }
    rows = receiver.execute(
        """
        SELECT stream_id,delivery_sequence,fact_id,fact_revision,payload_hash,
               payload_json,received_at_utc
        FROM fact_deliveries
        ORDER BY received_at_utc,stream_id,delivery_sequence
        """
    ).fetchall()
    selected: list[sqlite3.Row] = []
    for row in rows:
        if int(row["delivery_sequence"]) <= checkpoints.get(str(row["stream_id"]), 0):
            continue
        selected.append(row)
        if len(selected) >= max_deliveries:
            break
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


def adapter_metrics(connection: sqlite3.Connection) -> dict[str, object]:
    checkpoints = {
        str(row["stream_id"]): int(row["highest_delivery_sequence"])
        for row in connection.execute(
            "SELECT stream_id,highest_delivery_sequence FROM private_fact_adapter_checkpoints"
        ).fetchall()
    }
    counts = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            "SELECT status,COUNT(*) AS count FROM private_fact_adapter_deliveries GROUP BY status"
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
    market = connect_market_store(market_path)
    initialize_adapter_store(market)
    from .private_pipeline_foundation import atomic_json_write

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
