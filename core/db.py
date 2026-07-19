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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import AsyncGenerator
from .config import settings

__all__ = [
    "engine",
    "AsyncSessionLocal",
    "DrProjectionSessionLocal",
    "DrControlSessionLocal",
    "get_db",
    "get_dr_projection_db",
    "verify_three_site_database_role_bindings",
    "init_db",
]


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


def _auxiliary_session_factory(url: str | None, *, role: str):
    normalized = str(url or "").strip()
    if not normalized or normalized == settings.database_url:
        return AsyncSessionLocal
    auxiliary_engine = create_async_engine(
        normalized,
        pool_size=max(1, int(settings.dr_auxiliary_db_pool_size)),
        max_overflow=max(0, int(settings.dr_auxiliary_db_max_overflow)),
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_recycle=settings.db_pool_recycle_seconds,
        echo=False,
    )
    return async_sessionmaker(
        auxiliary_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        info={"three_site_db_role": role},
    )


DrProjectionSessionLocal = _auxiliary_session_factory(
    settings.dr_projection_database_url, role="projection"
)
DrControlSessionLocal = _auxiliary_session_factory(
    settings.dr_control_database_url, role="control"
)

# Register the fail-closed transaction guard at the shared session factory
# boundary so API, workers, CLI tools, and tests cannot accidentally depend on
# importing main.py for enforcement.
from core.writer_fencing import register_writer_fence_listener  # noqa: E402
from core.dr_event_outbox import register_dr_event_outbox_listener  # noqa: E402

register_writer_fence_listener()
register_dr_event_outbox_listener()


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


async def get_dr_projection_db() -> AsyncGenerator[AsyncSession, None]:
    async with DrProjectionSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """
    ایجاد جداول دیتابیس (اگر وجود نداشته باشند)
    """
    from core.dark_standby import assert_not_dark_standby

    assert_not_dark_standby("schema_init")
    # Import Base and models locally to avoid circular imports
    from models.database import Base
    import models  # Register all models with Base

    if settings.three_site_dr_enabled and settings.dr_event_protocol_strict:
        # Authoritative schemas are created only by the dedicated migration
        # role/service; an application process must never acquire DDL authority.
        return
    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all) # Uncomment to reset DB
        await conn.run_sync(Base.metadata.create_all)


async def verify_three_site_database_role_bindings() -> None:
    """Bind application/projection/control URLs to database-enforced roles."""

    if not (settings.three_site_dr_enabled and settings.dr_event_protocol_strict):
        return
    from core.runtime_identity import resolve_runtime_identity

    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_site:
        return
    urls = {
        settings.database_url,
        str(settings.dr_projection_database_url or ""),
        str(settings.dr_control_database_url or ""),
    }
    if "" in urls or len(urls) != 3:
        raise RuntimeError("strict three-site WebApp requires three distinct database role URLs")

    async def session_user(factory) -> str:  # noqa: ANN001
        async with factory() as session:
            return str(await session.scalar(text("SELECT session_user")))

    application_role = await session_user(AsyncSessionLocal)
    projection_role = await session_user(DrProjectionSessionLocal)
    control_role = await session_user(DrControlSessionLocal)
    if len({application_role, projection_role, control_role}) != 3:
        raise RuntimeError("strict three-site database sessions do not use three distinct roles")
    async with DrControlSessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT enforcement_enabled, physical_site, application_role, "
                    "projection_role, control_role FROM dr_database_runtime WHERE singleton_id = 1"
                )
            )
        ).mappings().one_or_none()
    if row is None or not row["enforcement_enabled"]:
        raise RuntimeError("three-site database enforcement is not enabled")
    expected = {
        "physical_site": identity.physical_site,
        "application_role": application_role,
        "projection_role": projection_role,
        "control_role": control_role,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise RuntimeError("three-site database role binding does not match runtime identity")
