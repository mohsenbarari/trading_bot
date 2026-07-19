"""Apply durably received DR events in contiguous producer order."""

from __future__ import annotations

import asyncio
import base64
from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Date, DateTime, LargeBinary, Numeric, Time, and_, delete, select, update
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


async def apply_next_dr_projection() -> str:
    """Apply one next-contiguous event; return applied/idle/deferred/quarantined."""

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
            next_sequence = int(checkpoint.contiguous_applied_sequence) + 1
            receipt = await session.scalar(
                select(DrEventReceipt).where(
                    DrEventReceipt.destination_site == identity.physical_site,
                    DrEventReceipt.origin_physical_site == checkpoint.origin_physical_site,
                    DrEventReceipt.producer_epoch == checkpoint.producer_epoch,
                    DrEventReceipt.producer_sequence == next_sequence,
                    DrEventReceipt.status == "received",
                ).with_for_update()
            )
            if receipt is None:
                # Rotate a temporarily blocked stream behind other origins so
                # a cross-stream FK dependency cannot starve the whole site.
                checkpoint.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return "deferred"
            event = await session.get(DrEvent, receipt.event_id)
            if event is None or event.envelope_hash != receipt.envelope_hash:
                session.add(
                    DrConflictQuarantine(
                        quarantine_id=str(uuid4()),
                        destination_site=identity.physical_site,
                        origin_physical_site=checkpoint.origin_physical_site,
                        producer_epoch=checkpoint.producer_epoch,
                        producer_sequence=next_sequence,
                        event_id=receipt.event_id,
                        reason="stored_event_receipt_hash_mismatch",
                        expected_hash=receipt.envelope_hash,
                        received_hash=event.envelope_hash if event is not None else "0" * 64,
                        evidence={},
                    )
                )
                receipt.status = "quarantined"
                await session.commit()
                return "quarantined"

            projection_version, version_decision = await _locked_projection_version(
                session, event, identity.physical_site
            )
            if version_decision == "conflict":
                session.add(
                    DrConflictQuarantine(
                        quarantine_id=str(uuid4()),
                        destination_site=identity.physical_site,
                        origin_physical_site=checkpoint.origin_physical_site,
                        producer_epoch=checkpoint.producer_epoch,
                        producer_sequence=next_sequence,
                        event_id=event.event_id,
                        reason="same_authority_epoch_multiple_sites",
                        expected_hash=projection_version.envelope_hash,
                        received_hash=event.envelope_hash,
                        evidence={
                            "aggregate_type": event.aggregate_type,
                            "aggregate_id": event.aggregate_id,
                            "stored_origin_site": projection_version.origin_physical_site,
                        },
                    )
                )
                receipt.status = "quarantined"
                await session.commit()
                return "quarantined"

            mode = projection_mode(
                table_name=event.aggregate_type,
                origin_site=event.origin_physical_site,
                destination_site=identity.physical_site,
            )
            if version_decision == "stale":
                result = "ignored"
            elif mode == "noop":
                result = "ignored"
            elif mode == "generic":
                result = await _apply_webapp_replica_row(session, event)
            else:
                from api.routers.sync import _apply_item, _parse_item

                try:
                    record_id = int(event.aggregate_db_id) if event.aggregate_db_id is not None else event.aggregate_id
                except (TypeError, ValueError):
                    record_id = event.aggregate_id
                wire_item = {
                    "table": event.aggregate_type,
                    "operation": event.operation,
                    "id": record_id,
                    "data": event.canonical_payload,
                }
                parsed = _parse_item(wire_item)
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
                checkpoint_key = (
                    checkpoint.destination_site,
                    checkpoint.origin_physical_site,
                    checkpoint.producer_epoch,
                )
                await session.rollback()
                async with DrProjectionSessionLocal() as defer_session:
                    await defer_session.execute(
                        update(DrStreamCheckpoint)
                        .where(
                            DrStreamCheckpoint.destination_site == checkpoint_key[0],
                            DrStreamCheckpoint.origin_physical_site == checkpoint_key[1],
                            DrStreamCheckpoint.producer_epoch == checkpoint_key[2],
                        )
                        .values(updated_at=datetime.now(timezone.utc))
                        .execution_options(is_sync=True)
                    )
                    await defer_session.commit()
                return "deferred"
            if result not in {"ok", "ignored"}:
                session.add(
                    DrConflictQuarantine(
                        quarantine_id=str(uuid4()),
                        destination_site=identity.physical_site,
                        origin_physical_site=checkpoint.origin_physical_site,
                        producer_epoch=checkpoint.producer_epoch,
                        producer_sequence=next_sequence,
                        event_id=event.event_id,
                        reason="business_projection_rejected",
                        expected_hash=event.envelope_hash,
                        received_hash=event.envelope_hash,
                        evidence={"aggregate_type": event.aggregate_type, "operation": event.operation},
                    )
                )
                receipt.status = "quarantined"
                await session.commit()
                return "quarantined"
            receipt.status = "applied"
            receipt.applied_at = datetime.now(timezone.utc)
            checkpoint.contiguous_applied_sequence = next_sequence
            if version_decision == "apply":
                _store_projection_version(
                    session, projection_version, event, identity.physical_site
                )
            await session.commit()
            return "applied"


async def dr_projection_loop() -> None:
    assert_not_dark_standby("dr_projection_worker")
    assert_background_job_authority(JOB_SYNC_WORKER)
    if not settings.dr_event_protocol_enabled:
        raise RuntimeError("DR projection worker requires DR_EVENT_PROTOCOL_ENABLED=true")
    await verify_three_site_database_role_bindings()
    while True:
        result = await apply_next_dr_projection()
        await asyncio.sleep(0.05 if result == "applied" else 1.0)


if __name__ == "__main__":
    asyncio.run(dr_projection_loop())
