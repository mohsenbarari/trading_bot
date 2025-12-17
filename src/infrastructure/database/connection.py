# src/infrastructure/database/connection.py
"""اتصال به دیتابیس"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from typing import AsyncGenerator

# این متغیرها بعداً از core.config خوانده می‌شوند
DATABASE_URL = None
engine = None
AsyncSessionLocal = None
Base = declarative_base()


def init_database(database_url: str):
    """راه‌اندازی اولیه دیتابیس"""
    global DATABASE_URL, engine, AsyncSessionLocal
    
    DATABASE_URL = database_url
    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(
        engine, 
        class_=AsyncSession, 
        expire_on_commit=False
    )


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency برای FastAPI"""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_session() -> AsyncSession:
    """دریافت Session برای استفاده مستقیم (بات)"""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    
    return AsyncSessionLocal()
