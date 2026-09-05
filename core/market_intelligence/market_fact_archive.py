"""Atomic PostgreSQL archive + outbox publication for curated Market Facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Mapping

from pydantic import TypeAdapter

from .private_pipeline_contracts import (
    FactPayload,
    MarketFactV1,
    content_hash,
    load_source_registry,
)


_FACT_PAYLOAD_ADAPTER = TypeAdapter(FactPayload)


def _normalize_fact_payload(
    payload: FactPayload | Mapping[str, Any],
) -> FactPayload:
    return _FACT_PAYLOAD_ADAPTER.validate_python(payload)


class MarketFactArchiveError(RuntimeError):
    """A content-free archive failure."""


@dataclass(frozen=True, slots=True)
class PublishedFact:
    fact: MarketFactV1
    delivery_sequence: int | None
    changed: bool


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketFactArchiveError("market_fact_archive_timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def stable_fact_id(*, source_code: str, event_key: str, fact_kind: str) -> str:
    material = "\0".join(
        ("market-fact-id/1.0", source_code, event_key, fact_kind)
    ).encode("ascii")
    return sha256(material).hexdigest()


def _next_source_sequence(cursor, stream_id: str) -> int:
    cursor.execute(
        """
        INSERT INTO market_data.stream_sequences(stream_id,last_sequence,updated_at_utc)
        VALUES(%s,1,clock_timestamp())
        ON CONFLICT(stream_id) DO UPDATE SET
            last_sequence=market_data.stream_sequences.last_sequence+1,
            updated_at_utc=excluded.updated_at_utc
        RETURNING last_sequence
        """,
        (stream_id,),
    )
    return int(cursor.fetchone()[0])


def _next_delivery_sequence(cursor, stream_id: str) -> int:
    # The advisory transaction lock makes MAX+1 safe without introducing a
    # second sequence table whose state could drift from the durable outbox.
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (stream_id,))
    cursor.execute(
        "SELECT COALESCE(MAX(delivery_sequence),0)+1 "
        "FROM market_data.market_fact_outbox WHERE stream_id=%s",
        (stream_id,),
    )
    return int(cursor.fetchone()[0])


def _retention(source_code: str, persisted: datetime) -> tuple[str, datetime | None]:
    source = load_source_registry().by_code().get(source_code)
    if source is None or not source.transfer_to_bot:
        raise MarketFactArchiveError("market_fact_archive_source_not_transferable")
    if source.permanent_archive:
        return "PERMANENT", None
    return "LIVE_3D", persisted + timedelta(days=3)


def _fact_semantic_fingerprint(
    *,
    event_key: str,
    origin_event_key: str,
    source_code: str,
    stream_id: str,
    source_sequence: int,
    occurred_at_utc: datetime | str,
    available_at_utc: datetime | str,
    parser_version: str,
    quality_state: str,
    quality_reason_codes: tuple[str, ...],
    payload: FactPayload | Mapping[str, Any],
) -> str:
    """Hash every revision-bearing field, not only the economic payload.

    ``payload_hash`` intentionally remains the wire contract's hash of the
    validated payload.  Revision identity is wider: a parser/quality/time
    correction must still emit a new immutable revision even when the
    economic payload is byte-for-byte unchanged.
    """

    normalized_payload = _normalize_fact_payload(payload)
    return content_hash(
        {
            "event_key": str(event_key),
            "origin_event_key": str(origin_event_key),
            "source_code": str(source_code),
            "stream_id": str(stream_id),
            "source_sequence": int(source_sequence),
            "occurred_at_utc": _utc(occurred_at_utc).isoformat(),
            "available_at_utc": _utc(available_at_utc).isoformat(),
            "parser_version": str(parser_version),
            "quality_state": str(quality_state),
            "quality_reason_codes": list(quality_reason_codes),
            "payload_hash": content_hash(normalized_payload),
            "payload": normalized_payload.model_dump(mode="json"),
        }
    )


def _write_projection(cursor, fact: MarketFactV1) -> None:
    payload = fact.payload
    fact_id = bytes.fromhex(fact.fact_id)
    kind = payload.kind
    if kind == "COIN_OFFER":
        cursor.execute(
            """
            INSERT INTO market_data.coin_offers(
                fact_id,group_code,instrument,side,settlement,trade_form,
                offered_price,price_unit,offered_quantity,quantity_unit,lifecycle_state
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
            ON CONFLICT(fact_id) DO UPDATE SET
                group_code=excluded.group_code,instrument=excluded.instrument,
                side=excluded.side,settlement=excluded.settlement,
                trade_form=excluded.trade_form,offered_price=excluded.offered_price,
                price_unit=excluded.price_unit,
                offered_quantity=excluded.offered_quantity,
                quantity_unit=excluded.quantity_unit
            """,
            (
                fact_id,
                payload.group_code,
                payload.instrument,
                payload.side,
                payload.settlement,
                payload.trade_form,
                payload.offered_price_value,
                payload.price_unit,
                payload.quantity_value,
                payload.quantity_unit,
            ),
        )
    elif kind == "COIN_TRADE":
        cursor.execute(
            """
            INSERT INTO market_data.coin_trade_outcomes(
                fact_id,offer_fact_id,outcome,agreed_price,price_unit,
                agreed_quantity,quantity_unit,confirmed_at_utc
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(fact_id) DO UPDATE SET
                offer_fact_id=excluded.offer_fact_id,outcome=excluded.outcome,
                agreed_price=excluded.agreed_price,price_unit=excluded.price_unit,
                agreed_quantity=excluded.agreed_quantity,
                quantity_unit=excluded.quantity_unit,
                confirmed_at_utc=excluded.confirmed_at_utc
            """,
            (
                fact_id,
                bytes.fromhex(payload.offer_fact_id),
                payload.outcome,
                payload.agreed_price_value,
                payload.price_unit,
                payload.agreed_quantity_value,
                payload.quantity_unit,
                fact.occurred_at_utc
                if payload.outcome in {"CONFIRMED_FULL", "CONFIRMED_PARTIAL"}
                else None,
            ),
        )
    elif kind == "PRIVATE_GOLD_OFFER":
        cursor.execute(
            """
            INSERT INTO market_data.private_gold_offers(
                fact_id,instrument,side,settlement,trade_form,offered_price,
                price_unit,offered_quantity,quantity_unit,expires_at_utc
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(fact_id) DO UPDATE SET
                side=excluded.side,settlement=excluded.settlement,
                trade_form=excluded.trade_form,offered_price=excluded.offered_price,
                offered_quantity=excluded.offered_quantity,
                quantity_unit=excluded.quantity_unit,
                expires_at_utc=excluded.expires_at_utc
            """,
            (
                fact_id,
                payload.instrument,
                payload.side,
                payload.settlement,
                payload.trade_form,
                payload.offered_price_value,
                payload.price_unit,
                payload.quantity_value,
                payload.quantity_unit,
                fact.occurred_at_utc + timedelta(seconds=payload.lifetime_seconds),
            ),
        )
    elif kind == "PRIVATE_GOLD_OUTCOME":
        cursor.execute(
            """
            INSERT INTO market_data.private_gold_outcomes(
                fact_id,offer_fact_id,outcome,executed_quantity,
                remaining_quantity,quantity_unit,evidenced_at_utc
            ) VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(fact_id) DO UPDATE SET
                offer_fact_id=excluded.offer_fact_id,outcome=excluded.outcome,
                executed_quantity=excluded.executed_quantity,
                remaining_quantity=excluded.remaining_quantity,
                quantity_unit=excluded.quantity_unit,
                evidenced_at_utc=excluded.evidenced_at_utc
            """,
            (
                fact_id,
                bytes.fromhex(payload.offer_fact_id),
                payload.outcome,
                payload.executed_quantity_value,
                payload.remaining_quantity_value,
                payload.quantity_unit,
                fact.occurred_at_utc,
            ),
        )


def build_and_publish_fact(
    connection,
    *,
    event_key: str,
    origin_event_key: str,
    source_code: str,
    occurred_at_utc: datetime | str,
    available_at_utc: datetime | str,
    parser_version: str,
    quality_state: str,
    quality_reason_codes: tuple[str, ...],
    payload: FactPayload | Mapping[str, Any],
) -> PublishedFact:
    """Commit a current fact revision and its outbox item atomically.

    Callers may wrap multiple invocations in one outer ``with connection``
    transaction.  A repeated identical projection is a no-op; a changed
    projection keeps the logical fact/source sequence and emits a new delivery
    sequence for the next immutable revision.
    """

    source = load_source_registry().by_code().get(source_code)
    if source is None:
        raise MarketFactArchiveError("market_fact_archive_source_unknown")
    # Hash the validated contract representation, not a potentially sparse
    # caller mapping.  Pydantic materializes optional ``None`` fields when it
    # builds MarketFactV1; hashing the sparse input first made REVIEW/REJECTED
    # coin trades fail the fact_payload_hash invariant.
    normalized_payload = _normalize_fact_payload(payload)
    payload_kind = str(normalized_payload.kind)
    fact_id = stable_fact_id(
        source_code=source_code,
        event_key=event_key,
        fact_kind=payload_kind,
    )
    payload_digest = content_hash(normalized_payload)
    occurred = _utc(occurred_at_utc)
    available = _utc(available_at_utc)
    persisted = datetime.now(timezone.utc)
    if available < occurred or persisted < available:
        raise MarketFactArchiveError("market_fact_archive_time_order_invalid")

    from psycopg2.extras import Json

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_sequence,fact_revision,encode(payload_hash,'hex'),
                   encode(event_key,'hex'),stream_id,
                   encode(origin_event_key,'hex'),source_code,occurred_at_utc,
                   available_at_utc,persisted_at_utc,parser_version,
                   quality_state,quality_reason_codes,payload
            FROM market_data.market_facts
            WHERE fact_id=decode(%s,'hex')
            FOR UPDATE
            """,
            (fact_id,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            existing_payload = _normalize_fact_payload(existing[13])
            existing_payload_digest = content_hash(existing_payload)
            if existing_payload_digest != str(existing[2]):
                raise MarketFactArchiveError(
                    "market_fact_archive_stored_payload_hash_mismatch"
                )
            existing_fingerprint = _fact_semantic_fingerprint(
                event_key=str(existing[3]),
                origin_event_key=str(existing[5]),
                source_code=str(existing[6]),
                stream_id=str(existing[4]),
                source_sequence=int(existing[0]),
                occurred_at_utc=existing[7],
                available_at_utc=existing[8],
                parser_version=str(existing[10]),
                quality_state=str(existing[11]),
                quality_reason_codes=tuple(existing[12] or ()),
                payload=existing_payload,
            )
            incoming_fingerprint = _fact_semantic_fingerprint(
                event_key=event_key,
                origin_event_key=origin_event_key,
                source_code=source_code,
                stream_id=source.fact_stream_id,
                source_sequence=int(existing[0]),
                occurred_at_utc=occurred,
                available_at_utc=available,
                parser_version=parser_version,
                quality_state=quality_state,
                quality_reason_codes=quality_reason_codes,
                payload=normalized_payload,
            )
        else:
            existing_fingerprint = incoming_fingerprint = None
        if existing is not None and existing_fingerprint == incoming_fingerprint:
            fact = MarketFactV1(
                contract="market_fact/1.0",
                fact_id=fact_id,
                event_key=str(existing[3]),
                origin_event_key=str(existing[5]),
                source_code=str(existing[6]),
                stream_id=str(existing[4]),
                source_sequence=int(existing[0]),
                occurred_at_utc=existing[7],
                available_at_utc=existing[8],
                persisted_at_utc=existing[9],
                schema_version="1.0",
                parser_version=str(existing[10]),
                fact_revision=int(existing[1]),
                quality_state=str(existing[11]),
                quality_reason_codes=tuple(existing[12] or ()),
                payload_hash=str(existing[2]),
                payload=existing_payload,
            )
            # A fact and its materialized projection normally commit in the
            # same PostgreSQL transaction.  If an operator restore or an old
            # partial import retained the fact but lost its projection,
            # idempotent replay must repair that dependency before a child
            # trade can be written.  The UPSERT is projection-only: it emits
            # no revision and no duplicate outbox delivery.
            _write_projection(cursor, fact)
            return PublishedFact(fact=fact, delivery_sequence=None, changed=False)
        if existing is None:
            source_sequence = _next_source_sequence(cursor, source.fact_stream_id)
            fact_revision = 1
        else:
            if str(existing[3]) != event_key or str(existing[4]) != source.fact_stream_id:
                raise MarketFactArchiveError("market_fact_archive_identity_conflict")
            source_sequence = int(existing[0])
            fact_revision = int(existing[1]) + 1
        fact = MarketFactV1(
            contract="market_fact/1.0",
            fact_id=fact_id,
            event_key=event_key,
            origin_event_key=origin_event_key,
            source_code=source_code,
            stream_id=source.fact_stream_id,
            source_sequence=source_sequence,
            occurred_at_utc=occurred,
            available_at_utc=available,
            persisted_at_utc=persisted,
            schema_version="1.0",
            parser_version=parser_version,
            fact_revision=fact_revision,
            quality_state=quality_state,
            quality_reason_codes=quality_reason_codes,
            payload_hash=payload_digest,
            payload=normalized_payload,
        )
        retention_class, purge_after = _retention(source_code, persisted)
        envelope = fact.model_dump(mode="json")
        if existing is None:
            cursor.execute(
                """
                INSERT INTO market_data.market_facts(
                    fact_id,event_key,origin_event_key,source_code,stream_id,
                    source_sequence,occurred_at_utc,available_at_utc,persisted_at_utc,
                    parser_version,fact_revision,fact_kind,quality_state,
                    quality_reason_codes,payload_hash,payload,retention_class,purge_after_utc
                ) VALUES(
                    decode(%s,'hex'),decode(%s,'hex'),decode(%s,'hex'),%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,decode(%s,'hex'),%s,%s,%s
                )
                """,
                (
                    fact.fact_id,
                    fact.event_key,
                    fact.origin_event_key,
                    fact.source_code,
                    fact.stream_id,
                    fact.source_sequence,
                    fact.occurred_at_utc,
                    fact.available_at_utc,
                    fact.persisted_at_utc,
                    fact.parser_version,
                    fact.fact_revision,
                    fact.payload.kind,
                    fact.quality_state,
                    list(fact.quality_reason_codes),
                    fact.payload_hash,
                    Json(fact.payload.model_dump(mode="json")),
                    retention_class,
                    purge_after,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE market_data.market_facts SET
                    occurred_at_utc=%s,available_at_utc=%s,
                    persisted_at_utc=%s,parser_version=%s,
                    fact_revision=%s,quality_state=%s,quality_reason_codes=%s,
                    payload_hash=decode(%s,'hex'),payload=%s
                WHERE fact_id=decode(%s,'hex')
                """,
                (
                    fact.occurred_at_utc,
                    fact.available_at_utc,
                    fact.persisted_at_utc,
                    fact.parser_version,
                    fact.fact_revision,
                    fact.quality_state,
                    list(fact.quality_reason_codes),
                    fact.payload_hash,
                    Json(fact.payload.model_dump(mode="json")),
                    fact.fact_id,
                ),
            )
        cursor.execute(
            """
            INSERT INTO market_data.market_fact_revisions(
                fact_id,fact_revision,parser_version,quality_state,payload_hash,payload
            ) VALUES(decode(%s,'hex'),%s,%s,%s,decode(%s,'hex'),%s)
            """,
            (
                fact.fact_id,
                fact.fact_revision,
                fact.parser_version,
                fact.quality_state,
                fact.payload_hash,
                Json(fact.payload.model_dump(mode="json")),
            ),
        )
        _write_projection(cursor, fact)
        delivery_sequence = _next_delivery_sequence(cursor, fact.stream_id)
        cursor.execute(
            """
            INSERT INTO market_data.market_fact_outbox(
                stream_id,delivery_sequence,fact_id,fact_revision,envelope,envelope_hash
            ) VALUES(%s,%s,decode(%s,'hex'),%s,%s,decode(%s,'hex'))
            """,
            (
                fact.stream_id,
                delivery_sequence,
                fact.fact_id,
                fact.fact_revision,
                Json(envelope),
                content_hash(envelope),
            ),
        )
    return PublishedFact(
        fact=fact,
        delivery_sequence=delivery_sequence,
        changed=True,
    )
