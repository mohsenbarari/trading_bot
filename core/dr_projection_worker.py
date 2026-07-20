"""Apply durably received DR events in contiguous producer order."""

from __future__ import annotations

import asyncio
import base64
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Date, DateTime, LargeBinary, Numeric, Time, and_, delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from core.background_job_authority import JOB_SYNC_WORKER, assert_background_job_authority
from core.config import settings
from core.dark_standby import assert_not_dark_standby
from core.db import DrProjectionSessionLocal, verify_three_site_database_role_bindings
from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES, WEBAPP_SITES
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.writer_fencing import projection_fence_scope
from models.dr_event import (
    DrConflictQuarantine,
    DrEvent,
    DrEventReceipt,
    DrProjectionVersion,
    DrReplayNonce,
    DrStreamCheckpoint,
)
from models.database import Base


def _coerce_column_value(column, value):  # noqa: ANN001
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(column.type, Date) and not isinstance(column.type, DateTime) and isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(column.type, Time) and isinstance(value, str):
        return time.fromisoformat(value)
    if isinstance(column.type, Numeric) and isinstance(value, str):
        return Decimal(value)
    if isinstance(column.type, PG_UUID) and isinstance(value, str):
        from uuid import UUID

        return UUID(value)
    if isinstance(column.type, LargeBinary) and isinstance(value, dict):
        if set(value) == {"encoding", "value"} and value.get("encoding") == "base64":
            return base64.b64decode(str(value["value"]), validate=True)
    return value


def projection_mode(*, table_name: str, origin_site: str, destination_site: str) -> str:
    """Return legacy/generic/noop from explicit table and topology policy."""

    entry = get_sync_registry_entry(table_name)
    if entry.policy == SyncPolicy.SYNC:
        return "legacy"
    if (
        entry.policy == SyncPolicy.NO_SYNC
        and table_name in WEBAPP_DR_REPLICA_TABLES
        and origin_site in WEBAPP_SITES
        and destination_site in WEBAPP_SITES
    ):
        return "generic"
    return "noop"


def projection_version_decision(
    *,
    stored_epoch: int | None,
    stored_sequence: int | None,
    stored_origin_site: str | None,
    incoming_epoch: int,
    incoming_sequence: int,
    incoming_origin_site: str,
) -> str:
    """Apply a newer authority term, suppress stale terms, quarantine split brain."""

    if stored_epoch is None:
        return "apply"
    if incoming_epoch < stored_epoch:
        return "stale"
    if incoming_epoch > stored_epoch:
        return "apply"
    if stored_origin_site != incoming_origin_site:
        return "conflict"
    if incoming_sequence <= int(stored_sequence or 0):
        return "stale"
    return "apply"


async def _locked_projection_version(session, event: DrEvent, destination_site: str):  # noqa: ANN001
    key = (
        destination_site,
        event.origin_authority,
        event.aggregate_type,
        event.aggregate_id,
    )
    row = await session.get(DrProjectionVersion, key, with_for_update=True)
    decision = projection_version_decision(
        stored_epoch=int(row.producer_epoch) if row is not None else None,
        stored_sequence=int(row.producer_sequence) if row is not None else None,
        stored_origin_site=row.origin_physical_site if row is not None else None,
        incoming_epoch=int(event.producer_epoch),
        incoming_sequence=int(event.producer_sequence),
        incoming_origin_site=event.origin_physical_site,
    )
    return row, decision


def _store_projection_version(
    session, row: DrProjectionVersion | None, event: DrEvent, destination_site: str
) -> None:  # noqa: ANN001
    if row is None:
        row = DrProjectionVersion(
            destination_site=destination_site,
            origin_authority=event.origin_authority,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
        )
        session.add(row)
    row.origin_physical_site = event.origin_physical_site
    row.producer_epoch = int(event.producer_epoch)
    row.producer_sequence = int(event.producer_sequence)
    row.event_id = event.event_id
    row.envelope_hash = event.envelope_hash


async def _apply_webapp_replica_row(session, event: DrEvent) -> str:  # noqa: ANN001
    table = Base.metadata.tables.get(event.aggregate_type)
    if table is None:
        return "failed"
    payload = event.canonical_payload
    if not isinstance(payload, dict):
        return "failed"
    values = {
        column.name: _coerce_column_value(column, payload[column.name])
        for column in table.columns
        if column.name in payload
    }
    primary_keys = list(table.primary_key.columns)
    if not primary_keys or any(column.name not in values for column in primary_keys):
        return "failed"
    predicate = and_(*(column == values[column.name] for column in primary_keys))
    if event.operation == "DELETE":
        await session.execute(delete(table).where(predicate).execution_options(is_sync=True))
        return "ok"
    if event.operation not in {"INSERT", "UPDATE"}:
        return "failed"
    mutable = {key: value for key, value in values.items() if key not in {column.name for column in primary_keys}}
    statement = pg_insert(table).values(**values)
    if mutable:
        statement = statement.on_conflict_do_update(
            index_elements=primary_keys,
            set_=mutable,
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=primary_keys)
    try:
        async with session.begin_nested():
            await session.execute(statement.execution_options(is_sync=True))
    except IntegrityError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        # Cross-stream parent events can legitimately arrive after a child.
        # Rotate this stream and retry once the parent projection advances.
        return "deferred" if sqlstate == "23503" else "failed"
    return "ok"


class _ProjectionDeferred(RuntimeError):
    pass


class _ProjectionRejected(RuntimeError):
    def __init__(self, reason: str, evidence: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or {}


async def _load_complete_transaction_group(session, checkpoint, destination_site: str):  # noqa: ANN001
    from core.dr_delivery_worker import event_envelope
    from core.dr_event_protocol import destination_transaction_hash

    next_sequence = int(checkpoint.contiguous_applied_sequence) + 1
    first_receipt = await session.scalar(
        select(DrEventReceipt).where(
            DrEventReceipt.destination_site == destination_site,
            DrEventReceipt.origin_physical_site == checkpoint.origin_physical_site,
            DrEventReceipt.producer_epoch == checkpoint.producer_epoch,
            DrEventReceipt.producer_sequence == next_sequence,
            DrEventReceipt.status == "received",
        ).with_for_update()
    )
    if first_receipt is None:
        raise _ProjectionDeferred()
    first_event = await session.get(DrEvent, first_receipt.event_id)
    if first_event is None or first_event.envelope_hash != first_receipt.envelope_hash:
        raise _ProjectionRejected("stored_event_receipt_hash_mismatch")
    if int(first_event.protocol_version) == 1:
        return [(first_receipt, first_event)]
    try:
        first_stream = dict(first_event.destination_streams[destination_site])
    except (KeyError, TypeError, ValueError) as exc:
        raise _ProjectionRejected("destination_transaction_metadata_missing") from exc
    if int(first_stream.get("transaction_position") or 0) != 1:
        raise _ProjectionRejected("transaction_group_does_not_start_at_position_one")
    expected_size = int(first_stream.get("transaction_size") or 0)
    transaction_id = str(first_stream.get("transaction_id") or "")
    rows = (
        await session.execute(
            select(DrEventReceipt, DrEvent)
            .join(DrEvent, DrEvent.event_id == DrEventReceipt.event_id)
            .where(
                DrEventReceipt.destination_site == destination_site,
                DrEventReceipt.origin_physical_site == checkpoint.origin_physical_site,
                DrEventReceipt.producer_epoch == checkpoint.producer_epoch,
                DrEvent.transaction_id == transaction_id,
                DrEventReceipt.status == "received",
            )
            .with_for_update(of=DrEventReceipt)
        )
    ).all()
    if len(rows) < expected_size:
        raise _ProjectionDeferred()
    if len(rows) != expected_size:
        raise _ProjectionRejected("transaction_group_cardinality_mismatch")
    try:
        rows = sorted(
            rows,
            key=lambda row: int(row[1].destination_streams[destination_site]["transaction_position"]),
        )
        streams = [event.destination_streams[destination_site] for _receipt, event in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise _ProjectionRejected("destination_transaction_metadata_missing") from exc
    positions = [int(stream["transaction_position"]) for stream in streams]
    sequences = [int(receipt.producer_sequence) for receipt, _event in rows]
    signed_sequences = [int(stream["sequence"]) for stream in streams]
    if positions != list(range(1, expected_size + 1)) or sequences != list(
        range(next_sequence, next_sequence + expected_size)
    ) or signed_sequences != sequences:
        raise _ProjectionRejected("transaction_group_order_mismatch")
    envelopes = [event_envelope(event) for _receipt, event in rows]
    group_hash = destination_transaction_hash(
        envelopes, destination_site=destination_site
    )
    if any(stream["transaction_hash"] != group_hash for stream in streams):
        raise _ProjectionRejected("transaction_group_hash_mismatch")
    return rows


async def _apply_projection_event(session, event: DrEvent, destination_site: str) -> str:
    projection_version, version_decision = await _locked_projection_version(
        session, event, destination_site
    )
    if version_decision == "conflict":
        raise _ProjectionRejected(
            "same_authority_epoch_multiple_sites",
            {
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "stored_origin_site": projection_version.origin_physical_site,
            },
        )
    mode = projection_mode(
        table_name=event.aggregate_type,
        origin_site=event.origin_physical_site,
        destination_site=destination_site,
    )
    if version_decision == "stale" or mode == "noop":
        result = "ignored"
    elif mode == "generic":
        result = await _apply_webapp_replica_row(session, event)
    else:
        from api.routers.sync import _apply_item, _parse_item

        try:
            record_id = int(event.aggregate_db_id) if event.aggregate_db_id is not None else event.aggregate_id
        except (TypeError, ValueError):
            record_id = event.aggregate_id
        parsed = _parse_item(
            {
                "table": event.aggregate_type,
                "operation": event.operation,
                "id": record_id,
                "data": event.canonical_payload,
            }
        )
        if parsed is None:
            result = "failed"
        else:
            table, operation, model, data, parsed_record_id = parsed
            result = await _apply_item(
                session,
                table,
                operation,
                parsed_record_id,
                data,
                model,
                [],
                [],
                source_server=("foreign" if event.origin_authority == "foreign" else "iran"),
            )
    if result == "deferred":
        raise _ProjectionDeferred()
    if result not in {"ok", "ignored"}:
        raise _ProjectionRejected(
            "business_projection_rejected",
            {"aggregate_type": event.aggregate_type, "operation": event.operation},
        )
    if version_decision == "apply":
        _store_projection_version(session, projection_version, event, destination_site)
    return result


async def apply_next_dr_projection() -> str:
    """Apply one complete source transaction in exactly one destination commit."""

    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_site and not identity.is_bot_site:
        raise RuntimeError("DR projection requires a fixed physical site")
    with projection_fence_scope(source="dr_projection_worker"):
        async with DrProjectionSessionLocal() as session:
            checkpoint = await session.scalar(
                select(DrStreamCheckpoint)
                .where(
                    DrStreamCheckpoint.destination_site == identity.physical_site,
                    DrStreamCheckpoint.contiguous_applied_sequence
                    < DrStreamCheckpoint.contiguous_received_sequence,
                )
                .order_by(
                    DrStreamCheckpoint.updated_at,
                    DrStreamCheckpoint.origin_physical_site,
                    DrStreamCheckpoint.producer_epoch,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if checkpoint is None:
                return "idle"
            try:
                group = await _load_complete_transaction_group(
                    session, checkpoint, identity.physical_site
                )
            except _ProjectionDeferred:
                checkpoint.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return "deferred"
            except _ProjectionRejected as exc:
                # At least the first receipt exists. Quarantine it and block
                # the stream until an operator resolves the corrupt group.
                next_sequence = int(checkpoint.contiguous_applied_sequence) + 1
                receipt = await session.scalar(
                    select(DrEventReceipt).where(
                        DrEventReceipt.destination_site == identity.physical_site,
                        DrEventReceipt.origin_physical_site == checkpoint.origin_physical_site,
                        DrEventReceipt.producer_epoch == checkpoint.producer_epoch,
                        DrEventReceipt.producer_sequence == next_sequence,
                    ).with_for_update()
                )
                if receipt is not None:
                    receipt.status = "quarantined"
                    session.add(
                        DrConflictQuarantine(
                            quarantine_id=str(uuid4()),
                            destination_site=identity.physical_site,
                            origin_physical_site=checkpoint.origin_physical_site,
                            producer_epoch=checkpoint.producer_epoch,
                            producer_sequence=next_sequence,
                            event_id=receipt.event_id,
                            reason=exc.reason,
                            expected_hash=receipt.envelope_hash,
                            received_hash=receipt.envelope_hash,
                            evidence=exc.evidence,
                        )
                    )
                await session.commit()
                return "quarantined"

            rejected: _ProjectionRejected | None = None
            deferred = False
            try:
                async with session.begin_nested():
                    for _receipt, event in group:
                        await _apply_projection_event(session, event, identity.physical_site)
            except _ProjectionDeferred:
                deferred = True
            except _ProjectionRejected as exc:
                rejected = exc
            if deferred:
                checkpoint.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return "deferred"
            if rejected is not None:
                for receipt, event in group:
                    receipt.status = "quarantined"
                    session.add(
                        DrConflictQuarantine(
                            quarantine_id=str(uuid4()),
                            destination_site=identity.physical_site,
                            origin_physical_site=event.origin_physical_site,
                            producer_epoch=event.producer_epoch,
                            producer_sequence=event.producer_sequence,
                            event_id=event.event_id,
                            reason=rejected.reason,
                            expected_hash=event.envelope_hash,
                            received_hash=event.envelope_hash,
                            evidence={"transaction_id": event.transaction_id, **rejected.evidence},
                        )
                    )
                await session.commit()
                return "quarantined"
            applied_at = datetime.now(timezone.utc)
            for receipt, _event in group:
                receipt.status = "applied"
                receipt.applied_at = applied_at
            checkpoint.contiguous_applied_sequence = int(group[-1][0].producer_sequence)
            await session.commit()
            return "applied"


async def cleanup_expired_replay_nonces(*, now: datetime | None = None) -> int:
    """Delete expired replay keys in bounded batches after a safety grace."""

    current = now or datetime.now(timezone.utc)
    grace = max(
        int(settings.dr_sync_request_max_age_seconds) * 2,
        int(settings.dr_replay_nonce_retention_seconds),
        60,
    )
    cutoff = current - timedelta(seconds=grace)
    with projection_fence_scope(source="dr_nonce_retention"):
        async with DrProjectionSessionLocal() as session:
            keys = (
                await session.execute(
                    select(DrReplayNonce.key_id, DrReplayNonce.nonce)
                    .where(DrReplayNonce.expires_at < cutoff)
                    .order_by(DrReplayNonce.expires_at)
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            ).all()
            if not keys:
                return 0
            key_pairs = [tuple(row) for row in keys]
            await session.execute(
                delete(DrReplayNonce)
                .where(
                    tuple_(DrReplayNonce.key_id, DrReplayNonce.nonce).in_(key_pairs)
                )
                .execution_options(is_sync=True)
            )
            await session.commit()
            return len(keys)


async def dr_projection_loop() -> None:
    assert_not_dark_standby("dr_projection_worker")
    assert_background_job_authority(JOB_SYNC_WORKER)
    if not settings.dr_event_protocol_enabled:
        raise RuntimeError("DR projection worker requires DR_EVENT_PROTOCOL_ENABLED=true")
    await verify_three_site_database_role_bindings()
    next_retention_at = 0.0
    while True:
        loop_now = asyncio.get_running_loop().time()
        if loop_now >= next_retention_at:
            await cleanup_expired_replay_nonces()
            next_retention_at = loop_now + 60.0
        result = await apply_next_dr_projection()
        await asyncio.sleep(0.05 if result == "applied" else 1.0)


if __name__ == "__main__":
    asyncio.run(dr_projection_loop())
