"""Async SQLAlchemy setup and the application-side Writer Witness fence.

The local Writer Witness lease is intentionally checked at every application
write boundary while enforcement is enabled.  This is not an election system:
the separate root-owned lease agent remains the only component allowed to
obtain or renew a Witness term.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Delete, Insert, Update

from .application_writer_term import (
    ApplicationWriterTermPolicy,
    ApplicationWriterTermError,
    ValidatedWriterTerm,
    policy_from_settings,
    require_active_writer_term,
    validate_application_writer_term_runtime,
)
from .config import settings


__all__ = [
    "engine",
    "AsyncSessionLocal",
    "get_db",
    "init_db",
    "application_writer_term_policy",
    "require_application_writer_term",
    "validate_application_writer_term_runtime_settings",
    "register_application_writer_term_guards",
    "register_application_writer_term_engine_guard",
]


def application_writer_term_policy() -> ApplicationWriterTermPolicy:
    """Return the current default-off Writer Witness policy."""

    return policy_from_settings(settings)


def validate_application_writer_term_runtime_settings(
    *, expected_service: str | None = None
) -> ApplicationWriterTermPolicy:
    """Validate static fenced-runtime configuration without opening the lease."""

    return validate_application_writer_term_runtime(
        settings,
        expected_service=expected_service,
    )


def require_application_writer_term() -> ValidatedWriterTerm | None:
    """Fail closed unless the enabled local Writer Witness term is active."""

    return require_active_writer_term(application_writer_term_policy())


def _session_has_pending_orm_writes(session: Session) -> bool:
    if session.new or session.deleted:
        return True
    for instance in session.dirty:
        try:
            if session.is_modified(instance, include_collections=False):
                return True
        except Exception:
            # If SQLAlchemy cannot classify a change, treating it as a write
            # prevents an uncertain path from bypassing the term fence.
            return True
    return False


def _enforce_application_writer_term_before_flush(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    """Fence ORM inserts/updates/deletes before SQLAlchemy emits DML."""

    if _session_has_pending_orm_writes(session):
        require_application_writer_term()


def _enforce_application_writer_term_before_commit(session: Session) -> None:
    """Revalidate immediately before durability, including a prior flush."""

    # A read-only commit is harmless, but checking it while enforcement is on
    # makes a term loss between flush and commit fail closed.  The disabled
    # policy is a pure no-op and does not open the lease file.
    require_application_writer_term()


def _enforce_application_writer_term_for_core_dml(orm_execute_state: object) -> None:
    """Fence ORM/Core bulk DML which bypasses an ORM flush."""

    statement = getattr(orm_execute_state, "statement", None)
    if isinstance(statement, (Insert, Update, Delete)):
        require_application_writer_term()


def _term_enforcement_requested() -> bool:
    """Treat malformed activation values as enabled so they fail closed."""

    return getattr(settings, "application_writer_term_enforced", False) is not False


def _enforce_application_writer_term_before_cursor_execute(
    _connection: object,
    _cursor: object,
    _statement: object,
    _parameters: object,
    _context: object,
    _executemany: object,
) -> None:
    """Fence raw SQL through this application's engine when active.

    Raw SQL cannot safely be classified as read-only (for example, a SELECT
    can invoke a mutating database function).  The term-enforced runtime is a
    sole writer, so a conservative check for every raw statement is preferable
    to a textual write-detection bypass.  Explicit migration or maintenance
    engines are not globally subscribed to this listener.
    """

    if _term_enforcement_requested():
        require_application_writer_term()


_application_writer_term_guards_registered = False


def register_application_writer_term_guards() -> None:
    """Install the Session-level guards exactly once for sync/async sessions."""

    global _application_writer_term_guards_registered
    if _application_writer_term_guards_registered:
        return
    event.listen(Session, "before_flush", _enforce_application_writer_term_before_flush)
    event.listen(Session, "before_commit", _enforce_application_writer_term_before_commit)
    event.listen(Session, "do_orm_execute", _enforce_application_writer_term_for_core_dml)
    _application_writer_term_guards_registered = True


def register_application_writer_term_engine_guard(target_engine: Engine) -> None:
    """Attach a raw-SQL fence to one named application engine only."""

    if event.contains(
        target_engine,
        "before_cursor_execute",
        _enforce_application_writer_term_before_cursor_execute,
    ):
        return
    event.listen(
        target_engine,
        "before_cursor_execute",
        _enforce_application_writer_term_before_cursor_execute,
    )


# ===== Engine with connection pooling =====
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_recycle=settings.db_pool_recycle_seconds,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize the legacy schema only when bootstrap is explicitly allowed.

    A Writer Witness runtime first validates all static invariants and then the
    live local term.  Its compose profile sets schema bootstrap false, so this
    function returns before importing metadata or issuing any DDL.
    """

    validate_application_writer_term_runtime_settings()
    require_application_writer_term()
    if settings.database_schema_bootstrap_enabled is False:
        return

    from models.database import Base
    import models  # noqa: F401  # Register all models with Base.

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


register_application_writer_term_guards()
register_application_writer_term_engine_guard(engine.sync_engine)
