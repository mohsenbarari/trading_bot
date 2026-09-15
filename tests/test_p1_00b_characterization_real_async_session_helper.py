"""P1-00b characterization helper: reusable real AsyncSession binding for sync engines.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: origin_internal_webapp_tier2_real_async_session_helper_prerequisite

Explicit exclusions:
- Do not call create_trade or characterize any product handler.
- No TIER_2 success, origin-cell, blocker, seed, policy, notification, retry, reconciliation, or Stage closure claim.
- Test-only helper prerequisite for real AsyncSession persistence.

Hermetic and deterministic under APP_ENV_FILE=config/unit-test.env.example.
"""
from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
import unittest

from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, declarative_base
from sqlalchemy.pool import StaticPool

__all__ = [
    "real_async_session",
    "async_session_for_sync_engine",
    "create_real_async_session",
]

Base = declarative_base()


class _TestRecord(Base):
    """Minimal test-local table verifying cross-session SQLite persistence."""

    __tablename__ = "test_p1_00b_async_session_record"

    id = Column(Integer, primary_key=True)
    value = Column(String(64), nullable=False)


@asynccontextmanager
async def real_async_session(engine: Any) -> AsyncIterator[AsyncSession]:
    """Yield actual AsyncSession bound to the supplied sync engine without fake result machinery.

    The caller retains explicit ownership and disposal lifecycle of the supplied engine.
    Adapts an in-memory or sync SQLAlchemy engine through AsyncSession.sync_session_class
    without ordered response queues, statement dispatchers, or generic owner/None fallbacks.
    """
    class _BoundSyncSession(Session):
        def __init__(sub_self, *args: Any, **kwargs: Any) -> None:
            kwargs.pop("bind", None)
            kwargs.pop("binds", None)
            super().__init__(bind=engine, **kwargs)

    session = AsyncSession(sync_session_class=_BoundSyncSession)
    try:
        yield session
    finally:
        await session.close()


async_session_for_sync_engine = real_async_session
create_real_async_session = real_async_session


class RealAsyncSessionHelperCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize real AsyncSession helper durability across independent sessions."""

    def setUp(self) -> None:
        super().setUp()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.addCleanup(self._dispose_engine)

    def _dispose_engine(self) -> None:
        try:
            Base.metadata.drop_all(bind=self.engine, checkfirst=True)
        finally:
            self.engine.dispose()

    async def test_real_async_session_shares_durable_state_across_independent_sessions(self) -> None:
        """Prove create/commit in session 1, reload/update/commit in session 2, durable verify in session 3."""
        record_id = 101
        initial_val = "created_state"
        updated_val = "updated_state"

        # 1. Create and commit in one yielded AsyncSession, then close.
        async with real_async_session(self.engine) as session_create:
            record = _TestRecord(id=record_id, value=initial_val)
            session_create.add(record)
            await session_create.commit()

        # 2. Reload, update, and commit in a second independent AsyncSession, then close.
        async with real_async_session(self.engine) as session_update:
            reloaded = await session_update.get(_TestRecord, record_id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.value, initial_val)
            reloaded.value = updated_val
            await session_update.commit()

        # 3. Final durable verification in a third independent AsyncSession.
        async with real_async_session(self.engine) as session_verify:
            verified = await session_verify.get(_TestRecord, record_id)
            self.assertIsNotNone(verified)
            self.assertEqual(verified.value, updated_val)

            # Also confirm via execute(select(...)) that standard query execution functions properly.
            stmt = select(_TestRecord).where(_TestRecord.id == record_id)
            result = await session_verify.execute(stmt)
            scalar_record = result.scalar_one_or_none()
            self.assertIsNotNone(scalar_record)
            self.assertEqual(scalar_record.value, updated_val)


if __name__ == "__main__":
    unittest.main()
