"""Transactional guard for mandatory sync outbox coverage."""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
import logging
import re
import time
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

from core.sync_registry import SyncPolicy, sync_registry_entries

logger = logging.getLogger(__name__)

SYNC_OUTBOX_PENDING_KEY = "_sync_outbox_pending"
SYNC_OUTBOX_TOKEN_KEY = "_sync_outbox_flush_token"
SYNC_OUTBOX_CURRENT_TOKEN_KEY = "_sync_outbox_current_token"
SYNC_OUTBOX_RECORDED_KEY = "_sync_outbox_recorded"
SYNC_OUTBOX_WAKEUP_NEEDED_KEY = "_sync_outbox_wakeup_needed"

_REGISTERED = False
_TOKEN_COUNTER = itertools.count(1)
_SYNC_WAKEUP_REDIS = None
_RAW_WRITE_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*"
    r"(?:insert\s+into|update|delete\s+from)\s+"
    r"((?:(?:\"[^\"]+\"|[A-Za-z_][\w$]*)\.)?"
    r"(?:\"[^\"]+\"|[A-Za-z_][\w$]*))",
    re.IGNORECASE | re.DOTALL,
)


class SyncOutboxError(RuntimeError):
    """Raised when a synced authoritative write lacks durable outbox coverage."""


class SyncOutboxBypassError(SyncOutboxError):
    """Raised when a bulk/raw write would bypass sync outbox recording."""


@dataclass(frozen=True)
class PendingSyncWrite:
    table_name: str
    operation: str
    obj: Any


def active_sync_tables() -> set[str]:
    return {
        table_name
        for table_name, entry in sync_registry_entries(include_planned=False).items()
        if entry.policy == SyncPolicy.SYNC
    }


def sync_table_requires_outbox(table_name: str | None) -> bool:
    normalized = _normalize_identifier(table_name)
    return bool(normalized and normalized in active_sync_tables())


def mark_sync_outbox_recorded(
    connection: Any,
    table_name: str,
    operation: str,
    record_id: Any,
    data: dict[str, Any] | None = None,
) -> None:
    """Mark that log_change inserted the durable row for the current flush."""
    info = getattr(connection, "info", None)
    if not isinstance(info, dict):
        return
    token = info.get(SYNC_OUTBOX_CURRENT_TOKEN_KEY)
    recorded = info.setdefault(SYNC_OUTBOX_RECORDED_KEY, set())
    recorded.add(
        _record_key(
            token,
            table_name,
            operation,
            _outbox_identity(table_name, record_id, data=data),
        )
    )


def collect_pending_sync_writes(session: Session, flush_context: Any, instances: Any) -> None:
    if _session_is_sync(session):
        _clear_pending(session)
        return

    pending: list[PendingSyncWrite] = []
    seen: set[int] = set()

    for obj in list(session.deleted):
        _append_pending(pending, seen, obj, "DELETE")

    for obj in list(session.new):
        _append_pending(pending, seen, obj, "INSERT")

    for obj in list(session.dirty):
        if id(obj) in seen:
            continue
        try:
            if not session.is_modified(obj, include_collections=False):
                continue
        except Exception:
            pass
        _append_pending(pending, seen, obj, "UPDATE")

    if not pending:
        _clear_pending(session)
        return

    token = f"{id(session)}:{id(flush_context)}:{next(_TOKEN_COUNTER)}"
    session.info[SYNC_OUTBOX_PENDING_KEY] = pending
    session.info[SYNC_OUTBOX_TOKEN_KEY] = token

    connection = _safe_session_connection(session)
    if connection is not None:
        info = getattr(connection, "info", None)
        if isinstance(info, dict):
            info[SYNC_OUTBOX_CURRENT_TOKEN_KEY] = token


def verify_pending_sync_outbox(session: Session, flush_context: Any) -> None:
    pending = list(session.info.pop(SYNC_OUTBOX_PENDING_KEY, []) or [])
    token = session.info.pop(SYNC_OUTBOX_TOKEN_KEY, None)
    if not pending:
        return

    connection = _safe_session_connection(session)
    connection_info = getattr(connection, "info", None) if connection is not None else None
    recorded = (
        connection_info.get(SYNC_OUTBOX_RECORDED_KEY, set())
        if isinstance(connection_info, dict)
        else set()
    )

    missing: list[str] = []
    for item in pending:
        identity = _expected_identity(item.obj, item.table_name)
        if identity is None:
            missing.append(f"{item.operation} {item.table_name}:missing-record-id")
            continue
        key = _record_key(token, item.table_name, item.operation, identity)
        if key not in recorded:
            missing.append(f"{item.operation} {item.table_name}:{identity}")

    if isinstance(connection_info, dict):
        if connection_info.get(SYNC_OUTBOX_CURRENT_TOKEN_KEY) == token:
            connection_info.pop(SYNC_OUTBOX_CURRENT_TOKEN_KEY, None)
        recorded_set = connection_info.get(SYNC_OUTBOX_RECORDED_KEY)
        if isinstance(recorded_set, set):
            recorded_set.difference_update(
                [key for key in recorded_set if key and key[0] == token]
            )

    if missing:
        raise SyncOutboxError(
            "Synced authoritative write missing mandatory change_log outbox row: "
            + ", ".join(missing)
        )

    session.info[SYNC_OUTBOX_WAKEUP_NEEDED_KEY] = True


def publish_sync_outbox_wakeup_after_commit(session: Session) -> None:
    """Wake the sync worker after durable change_log rows are committed.

    The Redis payload is intentionally only a signal. sync_worker ignores the
    queued payload for fresh outbound work and reads the committed change_log
    row from the database, so no peer-deliverable data is published pre-commit.
    """
    if not session.info.pop(SYNC_OUTBOX_WAKEUP_NEEDED_KEY, False):
        return

    try:
        client = _get_sync_wakeup_redis()
        client.rpush(
            "sync:outbound",
            json.dumps(
                {
                    "type": "sync_outbox_wakeup",
                    "timestamp": time.time(),
                },
                sort_keys=True,
            ),
        )
    except Exception as exc:
        logger.warning("Could not wake sync worker after commit: %s", exc)


def clear_sync_outbox_wakeup_after_rollback(session: Session) -> None:
    session.info.pop(SYNC_OUTBOX_WAKEUP_NEEDED_KEY, None)


def guard_sync_bulk_or_raw_execute(orm_execute_state: Any) -> None:
    execution_options = getattr(orm_execute_state, "execution_options", {}) or {}
    if execution_options.get("is_sync"):
        return

    table_name = statement_write_target_table(getattr(orm_execute_state, "statement", None))
    if not sync_table_requires_outbox(table_name):
        return

    raise SyncOutboxBypassError(
        "Bulk/raw write to synced table would bypass mandatory change_log outbox: "
        f"{table_name}"
    )


def statement_write_target_table(statement: Any) -> str | None:
    if isinstance(statement, (Insert, Update, Delete)):
        return _table_name(getattr(statement, "table", None))
    if isinstance(statement, TextClause):
        return _raw_sql_write_target(str(statement))
    return None


def register_sync_outbox_guards() -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    event.listen(Session, "before_flush", collect_pending_sync_writes)
    event.listen(Session, "after_flush", verify_pending_sync_outbox)
    event.listen(Session, "after_commit", publish_sync_outbox_wakeup_after_commit)
    event.listen(Session, "after_rollback", clear_sync_outbox_wakeup_after_rollback)
    event.listen(Session, "do_orm_execute", guard_sync_bulk_or_raw_execute)
    _REGISTERED = True
    logger.info("✅ Sync outbox guard registered")


def _append_pending(
    pending: list[PendingSyncWrite],
    seen: set[int],
    obj: Any,
    operation: str,
) -> None:
    table_name = object_table_name(obj)
    if not sync_table_requires_outbox(table_name):
        return
    pending.append(PendingSyncWrite(str(table_name), operation, obj))
    seen.add(id(obj))


def _get_sync_wakeup_redis():
    global _SYNC_WAKEUP_REDIS
    if _SYNC_WAKEUP_REDIS is not None:
        return _SYNC_WAKEUP_REDIS

    import redis
    from core.config import settings

    try:
        socket_timeout = max(0.05, float(getattr(settings, "sync_signal_redis_timeout_seconds", 0.25)))
    except (TypeError, ValueError):
        socket_timeout = 0.25

    _SYNC_WAKEUP_REDIS = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=0,
        decode_responses=True,
        socket_connect_timeout=socket_timeout,
        socket_timeout=socket_timeout,
        socket_keepalive=True,
        retry_on_timeout=True,
    )
    return _SYNC_WAKEUP_REDIS


def object_table_name(obj: Any) -> str | None:
    try:
        return inspect(obj).mapper.local_table.name
    except Exception:
        return getattr(obj, "__tablename__", None)


def _expected_identity(obj: Any, table_name: str) -> Any:
    normalized = _normalize_identifier(table_name)
    if normalized == "trading_settings":
        key = getattr(obj, "key", None)
        return f"key:{key}" if key else None
    record_id = getattr(obj, "id", None)
    if record_id is None:
        return None
    return _normalize_record_id(record_id)


def _outbox_identity(
    table_name: str,
    record_id: Any,
    *,
    data: dict[str, Any] | None = None,
) -> Any:
    normalized = _normalize_identifier(table_name)
    if normalized == "trading_settings" and data:
        key = data.get("key")
        if key:
            return f"key:{key}"
    return _normalize_record_id(record_id)


def _record_key(token: str | None, table_name: str, operation: str, identity: Any) -> tuple[Any, ...]:
    return (
        token,
        _normalize_identifier(table_name),
        str(operation).upper(),
        identity,
    )


def _normalize_record_id(record_id: Any) -> Any:
    try:
        return int(record_id)
    except (TypeError, ValueError):
        return str(record_id)


def _session_is_sync(session: Any) -> bool:
    info = getattr(session, "info", {}) or {}
    if info.get("is_sync"):
        return True

    connection = _safe_session_connection(session)
    if connection is None:
        return False
    get_options = getattr(connection, "get_execution_options", None)
    if get_options is None:
        return False
    try:
        return bool((get_options() or {}).get("is_sync"))
    except Exception:
        return False


def _safe_session_connection(session: Any) -> Any | None:
    connection_factory = getattr(session, "connection", None)
    if connection_factory is None:
        return None
    try:
        return connection_factory()
    except Exception:
        return None


def _clear_pending(session: Any) -> None:
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info.pop(SYNC_OUTBOX_PENDING_KEY, None)
        info.pop(SYNC_OUTBOX_TOKEN_KEY, None)


def _table_name(table: Any) -> str | None:
    return _normalize_identifier(getattr(table, "name", None))


def _raw_sql_write_target(sql: str) -> str | None:
    match = _RAW_WRITE_RE.match(sql)
    if not match:
        return None
    return _normalize_identifier(match.group(1))


def _normalize_identifier(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]
    if normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    return normalized.lower()
