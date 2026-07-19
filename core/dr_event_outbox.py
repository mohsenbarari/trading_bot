"""Fail-closed ORM boundary for the immutable three-site DR event stream.

The legacy product-sync listeners cover only the tables and payloads understood
by the two-site protocol.  Three-site recovery needs a durable event for every
mapped business-row mutation, including WebApp-local Messenger/session state.
Mapper ``after_*`` hooks are used deliberately: they run in the same database
transaction and in the dependency order selected by SQLAlchemy's unit of work.
Any event append failure aborts the business transaction.
"""

from __future__ import annotations

import base64
from datetime import date, datetime, time
from decimal import Decimal
import enum
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from core.config import settings
from core.dr_event_protocol import append_local_dr_event, canonical_json_bytes
from core.dr_data_policy import canonical_dr_row_payload
from core.sync_outbox_guard import statement_write_target_table
from core.sync_registry import SyncPolicy, get_sync_registry_entry, sync_registry_entries
from models.database import Base


class DrEventOutboxError(RuntimeError):
    """Raised when a business mutation could bypass immutable DR recording."""


_REGISTERED = False
_INTERNAL_TABLES = frozenset(
    {
        "change_log",
        "dr_conflict_quarantine",
        "dr_database_runtime",
        "dr_effect_outbox",
        "dr_blob_manifests",
        "dr_file_intents",
        "dr_blob_deliveries",
        "dr_blob_receipts",
        "dr_recovery_manifests",
        "dr_durability_state",
        "dr_event_deliveries",
        "dr_event_receipts",
        "dr_events",
        "dr_producer_cursors",
        "dr_projection_table_allowlist",
        "dr_projection_field_allowlist",
        "dr_projection_versions",
        "dr_replay_nonces",
        "dr_stream_checkpoints",
        "sync_apply_watermarks",
        "sync_blocks",
        "user_counter_event_receipts",
        "webapp_writer_state",
        "webapp_writer_transitions",
        "webapp_writer_witness_receipts",
        "webapp_writer_witness_state",
    }
)


def _enabled() -> bool:
    return bool(getattr(settings, "dr_event_protocol_enabled", False))


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return _json_value(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise DrEventOutboxError(f"unsupported DR column value type: {type(value).__name__}")


def _table_policy(table_name: str) -> SyncPolicy:
    if table_name in _INTERNAL_TABLES:
        return SyncPolicy.INTERNAL_BOOKKEEPING
    try:
        return get_sync_registry_entry(table_name).policy
    except KeyError as exc:
        raise DrEventOutboxError(
            f"mapped table {table_name!r} lacks an explicit three-site data policy"
        ) from exc


def _row_payload(target: Any) -> dict[str, Any]:
    state = sa_inspect(target)
    payload = {
        column.name: _json_value(getattr(target, attribute.key))
        for attribute in state.mapper.column_attrs
        for column in attribute.columns[:1]
    }
    return canonical_dr_row_payload(state.mapper.local_table.name, payload)


def _record_identity(target: Any) -> str:
    state = sa_inspect(target)
    values = [_json_value(getattr(target, column.key)) for column in state.mapper.primary_key]
    if not values or any(value is None for value in values):
        raise DrEventOutboxError("DR event cannot be recorded without a complete primary key")
    identity = str(values[0]) if len(values) == 1 else canonical_json_bytes(values).decode("utf-8")
    if len(identity) <= 255:
        return identity
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _has_column_changes(target: Any) -> bool:
    state = sa_inspect(target)
    return any(state.attrs[attribute.key].history.has_changes() for attribute in state.mapper.column_attrs)


def _projection_or_sync_apply(connection: Any) -> bool:
    from core.writer_fencing import current_projection_fence_context

    projection = current_projection_fence_context()
    marked_sync = bool((connection.get_execution_options() or {}).get("is_sync"))
    if marked_sync and projection is None and bool(getattr(settings, "dr_event_protocol_strict", False)):
        raise DrEventOutboxError("is_sync cannot grant a strict DR projection capability")
    return projection is not None or marked_sync


def _record(mapper: Any, connection: Any, target: Any, operation: str) -> None:
    if not _enabled() or _projection_or_sync_apply(connection):
        return
    table_name = mapper.local_table.name
    policy = _table_policy(table_name)
    if policy == SyncPolicy.INTERNAL_BOOKKEEPING:
        return
    if policy == SyncPolicy.NO_SYNC:
        # Only the closed WebApp-to-WebApp replica set belongs on the DR event
        # plane. Bot-local and runtime-only rows must never be transported or
        # create permanent quarantine noise at another trust boundary.
        from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES
        from core.runtime_identity import resolve_runtime_identity

        identity = resolve_runtime_identity(settings)
        if table_name not in WEBAPP_DR_REPLICA_TABLES or not identity.is_webapp_authority:
            return
    if operation == "UPDATE" and not _has_column_changes(target):
        return
    payload = _row_payload(target)
    append_local_dr_event(
        connection,
        table_name=table_name,
        record_id=_record_identity(target),
        operation=operation,
        data=payload,
        change_log_id=None,
    )


def _after_insert(mapper: Any, connection: Any, target: Any) -> None:
    _record(mapper, connection, target, "INSERT")


def _after_update(mapper: Any, connection: Any, target: Any) -> None:
    _record(mapper, connection, target, "UPDATE")


def _after_delete(mapper: Any, connection: Any, target: Any) -> None:
    _record(mapper, connection, target, "DELETE")


def _guard_bulk_or_raw_write(orm_execute_state: Any) -> None:
    if not _enabled():
        return
    execution_options = getattr(orm_execute_state, "execution_options", {}) or {}
    table_name = statement_write_target_table(getattr(orm_execute_state, "statement", None))
    if execution_options.get("is_sync"):
        from core.writer_fencing import current_projection_fence_context

        if (
            current_projection_fence_context() is None
            and bool(getattr(settings, "dr_event_protocol_strict", False))
        ):
            raise DrEventOutboxError("is_sync cannot bypass strict DR projection fencing")
        return
    if execution_options.get("dr_internal_write"):
        if table_name not in _INTERNAL_TABLES:
            raise DrEventOutboxError("dr_internal_write is restricted to DR bookkeeping tables")
        return
    if table_name is None or _table_policy(table_name) == SyncPolicy.INTERNAL_BOOKKEEPING:
        return
    raise DrEventOutboxError(
        f"bulk/raw mutation of {table_name} would bypass immutable three-site DR recording"
    )


def register_dr_event_outbox_listener() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    # Propagation keeps the invariant valid for all current and future mappers.
    event.listen(Base, "after_insert", _after_insert, propagate=True)
    event.listen(Base, "after_update", _after_update, propagate=True)
    event.listen(Base, "after_delete", _after_delete, propagate=True)
    event.listen(Session, "do_orm_execute", _guard_bulk_or_raw_write)
    _REGISTERED = True


def assert_dr_registry_complete() -> None:
    """Fail startup when a mapped business table has no deliberate policy."""

    known = set(sync_registry_entries(include_planned=False)) | set(_INTERNAL_TABLES)
    missing = set(Base.metadata.tables) - known
    if missing:
        raise DrEventOutboxError(
            "three-site registry is incomplete: " + ",".join(sorted(missing))
        )
