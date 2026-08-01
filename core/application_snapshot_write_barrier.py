"""Default-off PostgreSQL write barrier for an exported-snapshot cutover.

This primitive coordinates only participating SQLAlchemy ``Session``
transactions in a single PostgreSQL database.  When enabled on WebApp-FI,
ordinary root transactions acquire a transaction-scoped shared advisory lock.
A dedicated coordinator can hold the matching session-scoped exclusive lock
while it exports a snapshot and prepares the next source stream generation.

It intentionally has no route, migration, worker, Object Storage, or snapshot
implementation.  Raw engines, Alembic, direct DBAPI clients, and external
clients do not participate in the Session hook and remain explicit operational
bypasses.  Do not claim a complete write fence until those surfaces are either
disabled or separately guarded.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import AsyncIterator, Callable

from sqlalchemy import text


# A fixed two-int namespace avoids collisions with the existing single-key
# trade and Object-delta stream advisory locks.  These values are a protocol
# constant, not configuration, so all participating WebApp-FI processes use
# the same database-global barrier.
SNAPSHOT_WRITE_BARRIER_LOCK_NAMESPACE = 0x47544231
SNAPSHOT_WRITE_BARRIER_LOCK_KEY = 0x534E4150
SNAPSHOT_WRITE_BARRIER_SITE = "webapp_fi"

_SHARED_LOCK_STATEMENT = text(
    "SELECT pg_advisory_xact_lock_shared(:namespace, :lock_key)"
)
_EXCLUSIVE_LOCK_STATEMENT = text(
    "SELECT pg_advisory_lock(:namespace, :lock_key)"
)
_EXCLUSIVE_UNLOCK_STATEMENT = text(
    "SELECT pg_advisory_unlock(:namespace, :lock_key)"
)

_COORDINATOR_SESSION_INFO_KEY = "_snapshot_write_barrier_coordinator_marker"
_ACTIVE_COORDINATOR_MARKER: ContextVar[object | None] = ContextVar(
    "snapshot_write_barrier_coordinator_marker",
    default=None,
)


class ApplicationSnapshotWriteBarrierError(RuntimeError):
    """The configured cutover write barrier cannot safely be entered."""


@dataclass(frozen=True)
class ApplicationSnapshotWriteBarrierPolicy:
    """Explicit, WebApp-FI-only policy for the Session transaction hook."""

    enabled: bool = False
    local_site: str | None = None


def policy_from_settings(settings: object) -> ApplicationSnapshotWriteBarrierPolicy:
    """Project only the dedicated default-off settings into a barrier policy."""

    enabled = getattr(settings, "application_snapshot_write_barrier_enabled", False)
    if enabled is False:
        return ApplicationSnapshotWriteBarrierPolicy()
    return ApplicationSnapshotWriteBarrierPolicy(
        enabled=enabled,
        local_site=getattr(settings, "application_snapshot_write_barrier_local_site", None),
    )


def _require_enabled_policy(
    policy: ApplicationSnapshotWriteBarrierPolicy,
) -> ApplicationSnapshotWriteBarrierPolicy:
    if not isinstance(policy, ApplicationSnapshotWriteBarrierPolicy):
        raise ApplicationSnapshotWriteBarrierError("snapshot write barrier policy is invalid")
    if policy.enabled is False:
        return policy
    if policy.enabled is not True:
        raise ApplicationSnapshotWriteBarrierError("snapshot write barrier enabled flag is invalid")
    if policy.local_site != SNAPSHOT_WRITE_BARRIER_SITE:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier may be enabled only on WebApp-FI"
        )
    return policy


def _is_postgresql_connection(connection: object) -> bool:
    dialect = getattr(connection, "dialect", None)
    return getattr(dialect, "name", None) == "postgresql"


def _lock_parameters() -> dict[str, int]:
    return {
        "namespace": SNAPSHOT_WRITE_BARRIER_LOCK_NAMESPACE,
        "lock_key": SNAPSHOT_WRITE_BARRIER_LOCK_KEY,
    }


def _session_info(session: object) -> dict[object, object]:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier session has no mutable info"
        )
    return info


def _require_fresh_session(session: object) -> None:
    in_transaction = getattr(session, "in_transaction", None)
    if not callable(in_transaction):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator session transaction state is unavailable"
        )
    try:
        if in_transaction():
            raise ApplicationSnapshotWriteBarrierError(
                "snapshot write barrier coordinator requires a fresh session"
            )
    except ApplicationSnapshotWriteBarrierError:
        raise
    except Exception as exc:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator session transaction state is unavailable"
        ) from exc


def session_is_authorized_for_snapshot_write_barrier_coordinator(session: object) -> bool:
    """Return true only for the fresh session paired to the active coordinator."""

    marker = _ACTIVE_COORDINATOR_MARKER.get()
    if marker is None:
        return False
    info = getattr(session, "info", None)
    return isinstance(info, dict) and info.get(_COORDINATOR_SESSION_INFO_KEY) is marker


def acquire_shared_snapshot_write_barrier(
    session: object,
    connection: object,
    *,
    policy: ApplicationSnapshotWriteBarrierPolicy,
) -> None:
    """Participate in the root transaction barrier through a sync event hook.

    SQLAlchemy invokes ``Session.after_begin`` with its synchronous connection,
    including for ``AsyncSession``.  This function intentionally executes only
    through that supplied connection; it must not call ``Session.execute`` or
    await from the synchronous event handler.
    """

    active_policy = _require_enabled_policy(policy)
    if active_policy.enabled is False:
        return
    if not _is_postgresql_connection(connection):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier requires a PostgreSQL Session connection"
        )
    if session_is_authorized_for_snapshot_write_barrier_coordinator(session):
        return
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier Session connection cannot execute SQL"
        )
    try:
        execute(_SHARED_LOCK_STATEMENT, _lock_parameters())
    except Exception as exc:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier shared lock acquisition failed"
        ) from exc


async def _await_execute(connection: object, statement: object) -> object:
    execute = getattr(connection, "execute", None)
    if not callable(execute):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator connection cannot execute SQL"
        )
    try:
        result = execute(statement, _lock_parameters())
        if not hasattr(result, "__await__"):
            raise ApplicationSnapshotWriteBarrierError(
                "snapshot write barrier coordinator connection is not asynchronous"
            )
        return await result
    except ApplicationSnapshotWriteBarrierError:
        raise
    except Exception as exc:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator SQL failed"
        ) from exc


async def _commit_connection(connection: object) -> None:
    commit = getattr(connection, "commit", None)
    if not callable(commit):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator connection cannot commit"
        )
    try:
        result = commit()
        if not hasattr(result, "__await__"):
            raise ApplicationSnapshotWriteBarrierError(
                "snapshot write barrier coordinator connection is not asynchronous"
            )
        await result
    except ApplicationSnapshotWriteBarrierError:
        raise
    except Exception as exc:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator commit failed"
        ) from exc


async def _invalidate_connection(connection: object) -> None:
    """Discard a pooled connection whenever its session lock is uncertain."""

    invalidate = getattr(connection, "invalidate", None)
    if not callable(invalidate):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator connection cannot be invalidated"
        )
    try:
        result = invalidate()
        if not hasattr(result, "__await__"):
            raise ApplicationSnapshotWriteBarrierError(
                "snapshot write barrier coordinator connection is not asynchronous"
            )
        await result
    except ApplicationSnapshotWriteBarrierError:
        raise
    except Exception as exc:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator connection invalidation failed"
        ) from exc


def _require_unlock_result(result: object) -> None:
    scalar_one = getattr(result, "scalar_one", None)
    if not callable(scalar_one):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator unlock result is invalid"
        )
    try:
        unlocked = scalar_one()
    except Exception as exc:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator unlock result is invalid"
        ) from exc
    if unlocked is not True:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator lock was not held"
        )


@asynccontextmanager
async def coordinator_snapshot_write_barrier_scope(
    *,
    async_engine: object,
    session_factory: Callable[..., object],
    policy: ApplicationSnapshotWriteBarrierPolicy,
) -> AsyncIterator[object]:
    """Hold the exclusive PostgreSQL lock and yield one fresh marked session.

    ``session_factory`` must create a new ``AsyncSession`` when passed
    ``bind=connection``.  The coordinator owns that session's transaction
    boundaries, but the scope owns the session-level advisory lock and always
    attempts its explicit release on the same dedicated connection. The
    connection is invalidated if explicit unlock is uncertain, so a pool cannot
    hand a session-level advisory lock to an unrelated caller.

    This does not guard raw engines, migrations, direct DBAPI clients, or other
    uninstrumented processes.  It is deliberately a narrow coordinator tool,
    not a generic write authorization bypass.
    """

    active_policy = _require_enabled_policy(policy)
    if active_policy.enabled is False:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator requires an enabled policy"
        )
    if _ACTIVE_COORDINATOR_MARKER.get() is not None:
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator scopes cannot nest"
        )
    connect = getattr(async_engine, "connect", None)
    if not callable(connect):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator engine is invalid"
        )

    context = connect()
    if not hasattr(context, "__aenter__") or not hasattr(context, "__aexit__"):
        raise ApplicationSnapshotWriteBarrierError(
            "snapshot write barrier coordinator engine is not asynchronous"
        )

    async with context as connection:
        if not _is_postgresql_connection(connection):
            raise ApplicationSnapshotWriteBarrierError(
                "snapshot write barrier coordinator requires PostgreSQL"
            )

        lock_may_be_held = False
        exclusive_lock_confirmed = False
        reset_token: Token[object | None] | None = None
        try:
            # Set this before awaiting: cancellation or a driver failure can
            # arrive after PostgreSQL accepted the session-level lock but
            # before the caller regains control. In that case cleanup must
            # treat the physical connection as unsafe.
            lock_may_be_held = True
            await _await_execute(connection, _EXCLUSIVE_LOCK_STATEMENT)
            exclusive_lock_confirmed = True
            # ``pg_advisory_lock`` is session-scoped. Commit the implicit
            # transaction opened by the lock query before handing the dedicated
            # connection to the fresh coordinator session; the lock remains
            # held on that physical connection.
            await _commit_connection(connection)
            marker = object()
            reset_token = _ACTIVE_COORDINATOR_MARKER.set(marker)
            try:
                session_context = session_factory(bind=connection)
            except Exception as exc:
                raise ApplicationSnapshotWriteBarrierError(
                    "snapshot write barrier coordinator session factory failed"
                ) from exc
            if not (
                hasattr(session_context, "__aenter__")
                and hasattr(session_context, "__aexit__")
            ):
                raise ApplicationSnapshotWriteBarrierError(
                    "snapshot write barrier coordinator session is not asynchronous"
                )
            async with session_context as coordinator_session:
                info = _session_info(coordinator_session)
                _require_fresh_session(coordinator_session)
                if _COORDINATOR_SESSION_INFO_KEY in info:
                    raise ApplicationSnapshotWriteBarrierError(
                        "snapshot write barrier coordinator session is already marked"
                    )
                info[_COORDINATOR_SESSION_INFO_KEY] = marker
                try:
                    yield coordinator_session
                finally:
                    if info.get(_COORDINATOR_SESSION_INFO_KEY) is marker:
                        info.pop(_COORDINATOR_SESSION_INFO_KEY, None)
        finally:
            try:
                if lock_may_be_held:
                    if not exclusive_lock_confirmed:
                        # The driver may have been interrupted after the
                        # server accepted ``pg_advisory_lock`` but before it
                        # returned success. Never reuse that backend.
                        await _invalidate_connection(connection)
                    else:
                        try:
                            unlock_result = await _await_execute(
                                connection,
                                _EXCLUSIVE_UNLOCK_STATEMENT,
                            )
                            _require_unlock_result(unlock_result)
                            lock_may_be_held = False
                        except BaseException:
                            # ``async with engine.connect()`` normally returns
                            # a connection to its pool. A failed session-level
                            # unlock must instead discard that physical
                            # connection because the advisory lock could still
                            # be held.
                            await _invalidate_connection(connection)
                            raise
            finally:
                if reset_token is not None:
                    _ACTIVE_COORDINATOR_MARKER.reset(reset_token)
