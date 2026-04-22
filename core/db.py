# core/db.py
"""
تنظیمات اتصال به دیتابیس با SQLAlchemy Async

Connection Pool:
- pool_size: تعداد اتصالات دائمی در pool (پیش‌فرض: 10)
- max_overflow: تعداد اتصالات اضافی در ترافیک بالا (پیش‌فرض: 20)
- pool_pre_ping: بررسی سلامت اتصال قبل از استفاده (برای جلوگیری از خطاهای stale connection)
- pool_recycle: بازسازی اتصالات هر N ثانیه (برای جلوگیری از timeout سمت DB)

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
    
    # تعداد اتصالات دائمی در pool (per uvicorn worker)
    # با 2 worker: 40 × 2 = 80 اتصال دائمی، + overflow 20×2 = 40 → جمع 120، زیر PG max_connections=500
    pool_size=40,
    
    # تعداد اتصالات اضافی در صورت نیاز (بالاتر از pool_size)
    max_overflow=20,
    
    # بررسی سلامت اتصال قبل از استفاده
    # اگر اتصال stale باشد، یک اتصال جدید می‌سازد
    pool_pre_ping=True,
    
    # بازسازی اتصالات هر 1 ساعت (PostgreSQL timeout معمولاً 8 ساعت است)
    pool_recycle=3600,
    
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