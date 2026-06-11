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
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from typing import AsyncGenerator
from .config import settings

__all__ = ["engine", "AsyncSessionLocal", "get_db", "init_db"]


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
    # Import Base and models locally to avoid circular imports
    from models.database import Base
    import models  # Register all models with Base

    async with engine.begin() as conn:
        # await conn.run_sync(Base.metadata.drop_all) # Uncomment to reset DB
        await conn.run_sync(Base.metadata.create_all)
