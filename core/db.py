# core/db.py
"""
تنظیمات اتصال به دیتابیس با SQLAlchemy Async

Connection Pool:
- DB_POOL_SIZE: تعداد اتصالات دائمی در pool برای هر process
- DB_MAX_OVERFLOW: تعداد اتصالات اضافی در ترافیک بالا برای هر process
- pool_pre_ping: بررسی سلامت اتصال قبل از استفاده (برای جلوگیری از خطاهای stale connection)
- DB_POOL_RECYCLE_SECONDS: بازسازی اتصالات هر N ثانیه

در محیط production با چند worker، کل اتصالات = pool_size × workers
مثال: 10 pool × 4 workers = 40 اتصال همزمان به Postgres
"""
import re
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Delete, Insert, Update

from .application_writer_term import (
    ApplicationWriterTermPolicy,
    ValidatedWriterTerm,
    policy_from_settings,
    require_active_writer_term,
)
from .application_writer_transaction_envelope_guard import (
    ApplicationWriterTransactionEnvelopeGuardPolicy,
    policy_from_settings as application_writer_transaction_envelope_guard_policy_from_settings,
    require_application_writer_transaction_envelope_connection,
    require_application_writer_transaction_envelope_session,
)
from .external_effect_execution_gate import (
    ExternalEffectExecutionAuthorization,
    ExternalEffectExecutionGateError,
    ExternalEffectExecutionGatePolicy,
    policy_from_settings as external_effect_execution_gate_policy_from_settings,
    require_external_effect_execution_authorization as require_term_bound_external_effect_authorization,
    same_validated_writer_term,
)
from .application_snapshot_write_barrier import (
    ApplicationSnapshotWriteBarrierPolicy,
    acquire_shared_snapshot_write_barrier,
    policy_from_settings as snapshot_write_barrier_policy_from_settings,
)
from .config import settings
from .object_delta_receiver_apply_scope import (
    execution_is_authorized_for_object_delta_receiver_apply,
    session_is_authorized_for_object_delta_receiver_apply,
)

__all__ = [
    "engine",
    "AsyncSessionLocal",
    "get_db",
    "init_db",
    "application_writer_term_policy",
    "require_application_writer_term",
    "application_writer_transaction_envelope_guard_policy",
    "external_effect_execution_gate_policy",
    "require_external_effect_execution_authorization",
    "register_application_writer_term_guards",
    "register_application_writer_term_engine_guard",
    "application_snapshot_write_barrier_policy",
    "register_application_snapshot_write_barrier_guard",
]


def application_writer_term_policy() -> ApplicationWriterTermPolicy:
    """Build the current default-off term policy from application settings."""

    return policy_from_settings(settings)


def require_application_writer_term() -> ValidatedWriterTerm | None:
    """Fail closed when an enabled local Writer Witness term is not active."""

    return require_active_writer_term(application_writer_term_policy())


def application_writer_transaction_envelope_guard_policy(
) -> ApplicationWriterTransactionEnvelopeGuardPolicy:
    """Project the canonical, default-off DML fence from Settings.

    This factory is the only policy path used by the registered canonical
    ``core.db`` session and engine hooks.  A future approved writer runtime
    therefore activates the exact same policy at all ORM/Core/direct-SQL
    boundaries without monkeypatching an in-process callback.  Alembic and
    explicitly named manual maintenance engines are intentionally outside
    this application-engine registration and remain separate control planes.
    """

    return application_writer_transaction_envelope_guard_policy_from_settings(settings)


def _require_application_writer_transaction_envelope_for_session(session: object) -> None:
    """Reject raw application DML when the future envelope gate is enabled."""

    require_application_writer_transaction_envelope_session(
        application_writer_transaction_envelope_guard_policy(),
        session,
    )


def _require_application_writer_transaction_envelope_for_connection(
    connection: object,
) -> None:
    """Reject direct SQL unless it uses the exact registered envelope bind."""

    require_application_writer_transaction_envelope_connection(
        application_writer_transaction_envelope_guard_policy(),
        connection,
    )


def external_effect_execution_gate_policy() -> ExternalEffectExecutionGatePolicy:
    """Build the default-dormant external-effect policy from current settings."""

    return external_effect_execution_gate_policy_from_settings(settings)


def require_external_effect_execution_authorization(
    scope: str,
) -> ExternalEffectExecutionAuthorization | None:
    """Require a fresh local no-resend decision for one effectful scope.

    A disabled external-effect gate never opens its local authorization file.
    It still revalidates an *enabled* Writer Witness term: a process that has
    lost its term must not retain a previously scheduled provider effect just
    because the optional no-resend reconciliation gate is dormant.  With both
    policies disabled this remains a no-I/O compatibility path.

    When the external-effect gate is enabled, validate the active term before
    and after securely reading the authorization.  An atomic replacement of
    the lease between those checks is rejected rather than allowing the old
    authorization to leak into the new term's worker cycle.
    """

    policy = external_effect_execution_gate_policy()
    if policy.enabled is False:
        # Keep the legacy default entirely dormant, but do not let an active
        # single-writer runtime execute a delayed provider call with a stale
        # request/update admission.  ``is not False`` deliberately treats a
        # malformed value as enforced so it fails closed in the term policy.
        if getattr(settings, "application_writer_term_enforced", False) is not False:
            require_application_writer_term()
        return None
    active_term_before = require_application_writer_term()
    authorization = require_term_bound_external_effect_authorization(
        policy,
        active_writer_term=active_term_before,
        scope=scope,
    )
    active_term_after = require_application_writer_term()
    if not same_validated_writer_term(active_term_before, active_term_after):
        raise ExternalEffectExecutionGateError(
            "active Writer Witness term changed while external-effect authorization was checked"
        )
    return authorization


def application_snapshot_write_barrier_policy() -> ApplicationSnapshotWriteBarrierPolicy:
    """Build the independent, default-off source cutover barrier policy."""

    return snapshot_write_barrier_policy_from_settings(settings)


def _acquire_application_snapshot_write_barrier_after_begin(
    session: Session,
    _transaction: object,
    connection: object,
) -> None:
    """Make participating Session transactions wait during a source cutover.

    This hook intentionally covers only Session transactions. Direct engine
    work, migrations, DBAPI clients, and external clients need separate
    operational controls before a production cutover can rely on the barrier.
    """

    # The outer transaction already holds the shared lock. Reacquiring it for
    # a SAVEPOINT adds no protection and makes the hook's scope ambiguous.
    if getattr(_transaction, "nested", False):
        return
    acquire_shared_snapshot_write_barrier(
        session,
        connection,
        policy=application_snapshot_write_barrier_policy(),
    )


def _session_has_pending_orm_writes(session: Session) -> bool:
    if session.new or session.deleted:
        return True
    for instance in session.dirty:
        try:
            if session.is_modified(instance, include_collections=False):
                return True
        except Exception:
            # A failed change inspection must never create a write bypass.
            return True
    return False


def _enforce_application_writer_term_before_flush(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    if _session_has_pending_orm_writes(session):
        if session_is_authorized_for_object_delta_receiver_apply(session):
            return
        require_application_writer_term()
        _require_application_writer_transaction_envelope_for_session(session)


def _enforce_application_writer_term_before_commit(session: Session) -> None:
    # Revalidate after a previous flush or direct DML and immediately before
    # the transaction becomes durable. There is deliberately no sync-source
    # exemption: an enabled term gate applies to /api/sync writes too. The
    # disabled policy does not open I/O.
    if not session_is_authorized_for_object_delta_receiver_apply(session):
        require_application_writer_term()
        # Core DML is checked at execution time and an automatic ORM flush is
        # checked by ``before_flush``.  Only pending ORM state needs a second
        # envelope check here; a read-only commit remains a read-only action.
        if _session_has_pending_orm_writes(session):
            _require_application_writer_transaction_envelope_for_session(session)


def _enforce_application_writer_term_for_core_dml(orm_execute_state: object) -> None:
    statement = getattr(orm_execute_state, "statement", None)
    if isinstance(statement, (Insert, Update, Delete)):
        if session_is_authorized_for_object_delta_receiver_apply(
            getattr(orm_execute_state, "session", None)
        ):
            return
        require_application_writer_term()
        _require_application_writer_transaction_envelope_for_session(
            getattr(orm_execute_state, "session", None)
        )


_KNOWN_READ_ONLY_SQL_PREFIXES = frozenset({"SELECT", "SHOW", "VALUES"})
_SQL_LEADING_KEYWORD_RE = re.compile(r"[A-Za-z]+")
_SQL_SELECT_UNSAFE_CLAUSE_RE = re.compile(
    r"\b(?:INTO|FOR\s+(?:NO\s+KEY\s+)?UPDATE|FOR\s+(?:KEY\s+)?SHARE)\b",
    re.IGNORECASE,
)


def _sql_leading_keyword(statement: object) -> str | None:
    """Return a leading SQL keyword after comments, or ``None`` if uncertain."""

    if not isinstance(statement, str):
        return None
    remaining = statement
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("--"):
            line_endings = [
                position
                for position in (remaining.find("\n"), remaining.find("\r"))
                if position >= 0
            ]
            if not line_endings:
                return None
            remaining = remaining[min(line_endings) + 1 :]
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/", 2)
            if comment_end < 0:
                return None
            remaining = remaining[comment_end + 2 :]
            continue
        break
    match = _SQL_LEADING_KEYWORD_RE.match(remaining)
    return match.group(0).upper() if match else None


def _sql_statement_requires_writer_term(statement: object) -> bool:
    """Treat all but a small, explicit read-only SQL surface as unsafe.

    DML, DDL, transaction control, CTEs, pragma-like statements, and unknown
    leading forms all require an enabled Writer Witness term.  This is
    deliberately conservative: parsing a statement that starts with ``WITH``
    or an unfamiliar keyword could otherwise miss a mutation.
    """

    leading_keyword = _sql_leading_keyword(statement)
    if leading_keyword not in _KNOWN_READ_ONLY_SQL_PREFIXES:
        return True
    if leading_keyword == "SELECT" and isinstance(statement, str):
        # ``SELECT INTO`` creates a relation in PostgreSQL, and locking SELECT
        # forms acquire writer-adjacent state. Prefer a false positive here.
        return _SQL_SELECT_UNSAFE_CLAUSE_RE.search(statement) is not None
    return False


def _enforce_application_writer_term_before_cursor_execute(
    _connection: object,
    _cursor: object,
    statement: object,
    _parameters: object,
    _context: object,
    _executemany: object,
) -> None:
    # Do not touch the lease path unless its existing term enforcement is on.
    # The envelope seam has an independent default-off policy so it can later
    # reject raw direct SQL even when a term has already been checked by a
    # higher application boundary.
    term_enforced = getattr(settings, "application_writer_term_enforced", False) is not False
    if not _sql_statement_requires_writer_term(statement):
        return
    if execution_is_authorized_for_object_delta_receiver_apply(_context):
        return
    if term_enforced:
        require_application_writer_term()
    require_application_writer_transaction_envelope_connection(
        application_writer_transaction_envelope_guard_policy(),
        _connection,
    )


_application_writer_term_guards_registered = False
_application_snapshot_write_barrier_guard_registered = False


def register_application_writer_term_guards() -> None:
    """Install default-off guards for synchronous and AsyncSession write paths."""

    global _application_writer_term_guards_registered
    if _application_writer_term_guards_registered:
        return
    event.listen(Session, "before_flush", _enforce_application_writer_term_before_flush)
    event.listen(Session, "before_commit", _enforce_application_writer_term_before_commit)
    event.listen(Session, "do_orm_execute", _enforce_application_writer_term_for_core_dml)
    _application_writer_term_guards_registered = True


def register_application_snapshot_write_barrier_guard() -> None:
    """Install the default-off PostgreSQL shared-lock Session hook once."""

    global _application_snapshot_write_barrier_guard_registered
    if _application_snapshot_write_barrier_guard_registered:
        return
    event.listen(
        Session,
        "after_begin",
        _acquire_application_snapshot_write_barrier_after_begin,
    )
    _application_snapshot_write_barrier_guard_registered = True


def register_application_writer_term_engine_guard(target_engine: Engine) -> None:
    """Guard direct SQL executed through one explicitly registered engine.

    The production call below registers only this module's application engine.
    It intentionally does not subscribe globally, so migration, management,
    Bot, and independently created driver engines remain outside this fence.
    """

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


# ===== Engine با Connection Pool =====
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_recycle=settings.db_pool_recycle_seconds,
    # لاگ کردن کوئری‌ها (فقط برای debug)
    echo=False,
)

# Session Factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # جلوگیری از lazy loading بعد از commit
    autoflush=False,  # کنترل دستی flush برای بهینه‌سازی
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency Injector برای FastAPI.
    
    استفاده:
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """
    ایجاد جداول دیتابیس (اگر وجود نداشته باشند)
    """
    # Validate a requested envelope-fence activation before opening the term
    # lease or application engine.  A disabled policy is a no-op; an enabled
    # policy refuses partial wiring and implicit schema bootstrap before any
    # application mutation can be attempted.
    application_writer_transaction_envelope_guard_policy()
    require_application_writer_term()
    if not settings.database_schema_bootstrap_enabled:
        # A lease-controlled runtime can serve only an already-verified
        # schema.  Keep the historical default enabled for legacy surfaces;
        # disabling this switch never bypasses the active Writer Witness term.
        return
    # Import Base and models locally to avoid circular imports
    from models.database import Base
    import models  # Register all models with Base

    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all) # Uncomment to reset DB
        await conn.run_sync(Base.metadata.create_all)


register_application_writer_term_guards()
register_application_snapshot_write_barrier_guard()
register_application_writer_term_engine_guard(engine.sync_engine)
