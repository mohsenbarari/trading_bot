# src/infrastructure/database/connection.py
"""Retired legacy database factory.

This path predates the canonical ``core.db`` engine.  It used to construct a
second unguarded AsyncEngine and session factory, which would sit outside the
writer-term and transaction-envelope engine hooks.  There are no source
importers, so retaining a callable fallback would create an importlib/manual
bypass with no legitimate runtime consumer.

The module remains only to fail clearly for an old import path.  It never
imports SQLAlchemy and it can never create a database connection.  New code
must use the canonical ``core.db`` boundary instead.
"""

from __future__ import annotations

from typing import AsyncGenerator, NoReturn


RETIRED_LEGACY_DATABASE_CONNECTION_ERROR = (
    "src.infrastructure.database.connection is retired; use core.db"
)

# Kept as inert compatibility names only.  They must never be assigned an
# engine, sessionmaker, or declarative base from this module again.
DATABASE_URL = None
engine = None
AsyncSessionLocal = None
Base = None


class RetiredLegacyDatabaseConnectionError(RuntimeError):
    """Raised before the historical factory can construct any database object."""


def _retired() -> NoReturn:
    raise RetiredLegacyDatabaseConnectionError(RETIRED_LEGACY_DATABASE_CONNECTION_ERROR)


def init_database(_database_url: str) -> NoReturn:
    """Fail closed; this historical factory must not be revived implicitly."""

    _retired()


async def get_async_session() -> AsyncGenerator[object, None]:
    """Fail closed while preserving the old async-generator call shape."""

    _retired()
    if False:  # pragma: no cover - marks this as an async generator for callers.
        yield None


async def get_session() -> object:
    """Fail closed; callers must migrate to ``core.db.AsyncSessionLocal``."""

    _retired()
