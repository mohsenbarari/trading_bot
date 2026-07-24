"""Transaction-bound fencing for WebApp-authoritative mutations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterator, Mapping

from sqlalchemy import event, inspect as sa_inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from core.runtime_identity import RuntimeIdentity
from core.config import settings
from core.webapp_writer_control import WriterStateSnapshot, snapshot_is_local_active
from core.writer_lease_clock import lease_clock_reasons
from core.sync_field_policy import SyncFieldAction, sync_field_policy_entries
from core.sync_outbox_guard import raw_sql_is_provably_read_only, statement_write_target_table
from core.sync_registry import SyncPolicy, sync_registry_entries


class WriterFenceError(RuntimeError):
    """Raised when a WebApp-authoritative transaction has lost writer ownership."""


@dataclass(frozen=True)
class WriterFenceContext:
    physical_site: str
    writer_epoch: int
    transition_id: str
    witness_lease_id: str | None
    require_witness_lease: bool
    source: str


@dataclass(frozen=True)
class ProjectionFenceContext:
    source: str
    allowed_tables: frozenset[str]
    allowed_fields: Mapping[str, frozenset[str]]


_writer_fence_context: ContextVar[WriterFenceContext | None] = ContextVar(
    "webapp_writer_fence_context",
    default=None,
)
_projection_fence_context: ContextVar[ProjectionFenceContext | None] = ContextVar(
    "webapp_projection_fence_context",
    default=None,
)
_listener_registered = False
_WRITE_SEEN_KEY = "_three_site_authoritative_write_seen"


LOCAL_PROJECTION_TABLES = frozenset(
    {
        "sync_apply_watermarks", "sync_blocks", "chats", "chat_members",
        "user_counter_event_receipts", "dr_events", "dr_event_receipts",
        "dr_event_deliveries", "dr_stream_checkpoints", "dr_conflict_quarantine",
        "dr_replay_nonces", "dr_effect_outbox",
        "dr_effect_fanouts",
        "dr_producer_cursors", "dr_projection_versions",
        "dr_blob_manifests", "dr_file_intents", "dr_blob_deliveries",
        "dr_blob_receipts", "dr_recovery_manifests",
    }
)
CONTROL_TABLES = frozenset(
    {"dr_durability_state", "webapp_writer_state", "webapp_writer_transitions"}
)


def _is_control_session(session: Session) -> bool:
    return bool(getattr(session, "info", {}).get("three_site_db_role") == "control")


def _is_projection_session(session: Session) -> bool:
    return bool(getattr(session, "info", {}).get("three_site_db_role") == "projection")


def current_writer_fence_context() -> WriterFenceContext | None:
    return _writer_fence_context.get()


def current_projection_fence_context() -> ProjectionFenceContext | None:
    return _projection_fence_context.get()


def _projection_allowlist() -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    from models.database import Base
    from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES

    sync_tables = {
        table_name
        for table_name, entry in sync_registry_entries(include_planned=False).items()
        if entry.policy == SyncPolicy.SYNC
    }
    allowed_tables = frozenset(
        sync_tables | set(LOCAL_PROJECTION_TABLES) | set(WEBAPP_DR_REPLICA_TABLES)
    )
    forbidden_fields: dict[str, set[str]] = {}
    for (table_name, field_name), entry in sync_field_policy_entries().items():
        if (
            table_name not in WEBAPP_DR_REPLICA_TABLES
            and entry.action in {SyncFieldAction.DROP, SyncFieldAction.HASH}
        ):
            forbidden_fields.setdefault(table_name, set()).add(field_name)
    allowed_fields: dict[str, frozenset[str]] = {}
    for table_name in allowed_tables:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        allowed_fields[table_name] = frozenset(
            column.name
            for column in table.columns
            if column.name not in forbidden_fields.get(table_name, set())
        )
    return allowed_tables, allowed_fields


@contextmanager
def projection_fence_scope(*, source: str) -> Iterator[ProjectionFenceContext]:
    if current_writer_fence_context() is not None:
        raise WriterFenceError("projection capability cannot be nested in a writer capability")
    allowed_tables, allowed_fields = _projection_allowlist()
    context = ProjectionFenceContext(
        source=source,
        allowed_tables=allowed_tables,
        allowed_fields=allowed_fields,
    )
    token: Token = _projection_fence_context.set(context)
    try:
        yield context
    finally:
        _projection_fence_context.reset(token)


@contextmanager
def writer_fence_scope(
    identity: RuntimeIdentity,
    snapshot: WriterStateSnapshot,
    *,
    source: str,
    require_witness_lease: bool = False,
) -> Iterator[WriterFenceContext]:
    if current_projection_fence_context() is not None:
        raise WriterFenceError("writer capability cannot be nested in a projection capability")
    active, reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=require_witness_lease,
    )
    if not active:
        raise WriterFenceError("writer preflight rejected: " + ",".join(reasons))
    context = WriterFenceContext(
        physical_site=identity.physical_site,
        writer_epoch=snapshot.writer_epoch,
        transition_id=snapshot.transition_id,
        witness_lease_id=snapshot.witness_lease_id,
        require_witness_lease=require_witness_lease,
        source=source,
    )
    token: Token = _writer_fence_context.set(context)
    try:
        yield context
    finally:
        _writer_fence_context.reset(token)


def _strict_webapp_fencing_enabled() -> bool:
    if not bool(getattr(settings, "three_site_dr_enabled", False)):
        return False
    identity = RuntimeIdentity(
        logical_authority=str(getattr(settings, "logical_authority", "") or ""),
        physical_site=str(getattr(settings, "physical_site", "") or ""),
        legacy_server_mode=str(getattr(settings, "server_mode", "") or ""),
        compatibility_inferred=False,
    )
    return identity.is_webapp_authority and identity.is_webapp_site


def _strict_three_site_enabled() -> bool:
    return bool(
        getattr(settings, "three_site_dr_enabled", False)
        and getattr(settings, "dr_event_protocol_strict", False)
    )


def _changed_fields(session: Session, obj: object, *, is_new: bool) -> frozenset[str]:
    state = sa_inspect(obj)
    fields: set[str] = set()
    for attribute in state.mapper.column_attrs:
        history = state.attrs[attribute.key].history
        if history.has_changes() or (is_new and getattr(obj, attribute.key, None) is not None):
            fields.add(attribute.columns[0].name)
    return frozenset(fields)


def _validate_projection_target(table_name: str, fields: frozenset[str] | None = None) -> None:
    context = current_projection_fence_context()
    if context is None:
        raise WriterFenceError("WebApp mutation lacks an explicit writer/projection capability")
    if table_name not in context.allowed_tables:
        raise WriterFenceError(f"projection capability forbids table {table_name}")
    if fields is not None:
        forbidden = set(fields) - set(context.allowed_fields.get(table_name, frozenset()))
        if forbidden:
            raise WriterFenceError(
                f"projection capability forbids fields on {table_name}: {','.join(sorted(forbidden))}"
            )


def _guard_before_flush(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    if not _strict_webapp_fencing_enabled():
        return
    candidates = [(obj, "delete", False) for obj in session.deleted]
    candidates += [(obj, "insert", True) for obj in session.new]
    candidates += [
        (obj, "update", False)
        for obj in session.dirty
        if session.is_modified(obj, include_collections=False)
    ]
    if not candidates:
        return
    writer = current_writer_fence_context()
    projection = current_projection_fence_context()
    projection_role_session = _is_projection_session(session)
    control = _is_control_session(session)
    if writer is None and projection is None and not projection_role_session and not control:
        raise WriterFenceError("WebApp ORM mutation lacks an explicit writer/projection capability")
    for obj, operation, is_new in candidates:
        table_name = sa_inspect(obj).mapper.local_table.name
        if control and table_name not in CONTROL_TABLES:
            raise WriterFenceError(f"control database role forbids table {table_name}")
        if projection is not None or projection_role_session:
            fields = frozenset() if operation == "delete" else _changed_fields(session, obj, is_new=is_new)
            if projection is not None:
                _validate_projection_target(table_name, fields)
            else:
                allowed_tables, allowed_fields = _projection_allowlist()
                if table_name not in allowed_tables or set(fields) - set(
                    allowed_fields.get(table_name, frozenset())
                ):
                    raise WriterFenceError(
                        f"projection database role forbids table/fields on {table_name}"
                    )
    if writer is not None:
        from core.dr_durability_gate import enforce_session_durability

        enforce_session_durability(
            session,
            {sa_inspect(obj).mapper.local_table.name for obj, _operation, _is_new in candidates},
        )
    session.info[_WRITE_SEEN_KEY] = True


def _guard_orm_execute(orm_execute_state) -> None:  # noqa: ANN001
    if not _strict_webapp_fencing_enabled():
        return
    statement = getattr(orm_execute_state, "statement", None)
    table_name = statement_write_target_table(statement)
    if table_name is None and isinstance(statement, TextClause):
        if raw_sql_is_provably_read_only(str(statement)):
            return
        raise WriterFenceError(
            "Unclassified raw SQL is forbidden by strict WebApp writer fencing"
        )
    if table_name is None:
        return
    writer = current_writer_fence_context()
    projection = current_projection_fence_context()
    projection_role_session = _is_projection_session(orm_execute_state.session)
    control = _is_control_session(orm_execute_state.session)
    if writer is None and projection is None and not projection_role_session and not control:
        raise WriterFenceError("WebApp bulk/raw mutation lacks an explicit writer/projection capability")
    if control and table_name not in CONTROL_TABLES:
        raise WriterFenceError(f"control database role forbids table {table_name}")
    if (projection is not None or projection_role_session) and table_name != "__sequence__":
        if isinstance(statement, TextClause):
            raise WriterFenceError("projection capability forbids raw SQL mutations")
        raw_values = getattr(statement, "_values", None)
        fields: frozenset[str] | None = None
        if raw_values is not None:
            normalized: set[str] = set()
            for key in raw_values:
                name = getattr(key, "name", None) or getattr(key, "key", None) or str(key)
                normalized.add(str(name))
            fields = frozenset(normalized)
        if projection is not None:
            _validate_projection_target(table_name, fields)
        else:
            allowed_tables, allowed_fields = _projection_allowlist()
            if table_name not in allowed_tables or (
                fields is not None
                and set(fields) - set(allowed_fields.get(table_name, frozenset()))
            ):
                raise WriterFenceError(
                    f"projection database role forbids table/fields on {table_name}"
                )
    orm_execute_state.session.info[_WRITE_SEEN_KEY] = True


def _set_database_capability_after_begin(session: Session, transaction, connection) -> None:  # noqa: ANN001
    if not _strict_three_site_enabled():
        return
    from core.runtime_identity import resolve_runtime_identity

    identity = resolve_runtime_identity(settings)
    writer = current_writer_fence_context()
    projection = current_projection_fence_context()
    if writer is not None:
        settings_map = {
            "trading_bot.mutation_capability": "writer",
            "trading_bot.physical_site": writer.physical_site,
            "trading_bot.writer_epoch": str(writer.writer_epoch),
            "trading_bot.transition_id": writer.transition_id,
            "trading_bot.witness_lease_id": writer.witness_lease_id or "",
        }
    elif projection is not None or _is_projection_session(session):
        from core.dr_database_roles import projection_scope_for_service

        settings_map = {
            "trading_bot.mutation_capability": "projection",
            "trading_bot.physical_site": str(getattr(settings, "physical_site", "") or ""),
            "trading_bot.projection_scope": projection_scope_for_service(
                getattr(settings, "trading_bot_service", None)
            ),
        }
    elif _is_control_session(session):
        settings_map = {
            "trading_bot.mutation_capability": "control",
            "trading_bot.physical_site": str(getattr(settings, "physical_site", "") or ""),
        }
    elif identity.is_bot_site:
        settings_map = {
            "trading_bot.mutation_capability": "foreign_writer",
            "trading_bot.physical_site": identity.physical_site,
            "trading_bot.dr_producer_epoch": str(
                int(getattr(settings, "dr_producer_epoch", 0))
            ),
        }
    else:
        return
    for key, value in settings_map.items():
        connection.execute(
            text("SELECT set_config(:key, :value, true)"),
            {"key": key, "value": value},
        )


def _enforce_writer_fence_before_commit(session: Session) -> None:
    context = current_writer_fence_context()
    projection = current_projection_fence_context()
    write_seen = bool(session.info.get(_WRITE_SEEN_KEY)) if hasattr(session, "info") else False
    pending = bool(
        getattr(session, "new", ()) or getattr(session, "dirty", ()) or getattr(session, "deleted", ())
    )
    if _strict_webapp_fencing_enabled() and (write_seen or pending):
        if (
            context is None
            and projection is None
            and not _is_projection_session(session)
            and not _is_control_session(session)
        ):
            raise WriterFenceError("WebApp commit lacks an explicit writer/projection capability")
    if context is None:
        return
    # This application-side check is an early stale-context guard only.  The
    # database triggers remain the authoritative write barrier and take the
    # SECURITY DEFINER row lock on webapp_writer_state for every protected DML
    # statement.  Taking FOR SHARE here would require granting the runtime app
    # role UPDATE-class privileges on the control table in PostgreSQL, which
    # weakens the closed role boundary without adding protection beyond the
    # trigger-held transaction lock.
    row = session.connection().execute(
        text(
            """
            SELECT active_site, writer_epoch, control_state, transition_id,
                   witness_lease_id, witness_lease_expires_at,
                   witness_local_boot_id, witness_local_boottime_deadline
            FROM webapp_writer_state
            WHERE authority = 'webapp'
            """
        )
    ).mappings().one_or_none()
    if row is None:
        raise WriterFenceError("writer state is missing at commit boundary")
    if row["control_state"] != "active":
        raise WriterFenceError("writer is fenced at commit boundary")
    if row["active_site"] != context.physical_site:
        raise WriterFenceError("active physical site changed before commit")
    if int(row["writer_epoch"]) != context.writer_epoch:
        raise WriterFenceError("writer epoch changed before commit")
    if row["transition_id"] != context.transition_id:
        raise WriterFenceError("writer transition changed before commit")
    if context.require_witness_lease:
        if not context.witness_lease_id or row["witness_lease_id"] != context.witness_lease_id:
            raise WriterFenceError("writer witness lease changed before commit")
        if row["witness_lease_expires_at"] is None:
            raise WriterFenceError("writer witness lease expiry is missing at commit boundary")
        timing_reasons = lease_clock_reasons(
            stored_boot_id=row["witness_local_boot_id"],
            stored_boottime_deadline=row["witness_local_boottime_deadline"],
            boot_id_file=settings.writer_witness_boot_id_file,
        )
        if timing_reasons:
            raise WriterFenceError(
                "writer witness monotonic lease rejected before commit: " + ",".join(timing_reasons)
            )


def register_writer_fence_listener() -> None:
    global _listener_registered
    if _listener_registered:
        return
    event.listen(Session, "before_flush", _guard_before_flush)
    event.listen(Session, "do_orm_execute", _guard_orm_execute)
    event.listen(Session, "after_begin", _set_database_capability_after_begin)
    event.listen(Session, "before_commit", _enforce_writer_fence_before_commit)
    _listener_registered = True
