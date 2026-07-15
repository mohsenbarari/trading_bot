import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.services import market_transition_service


def _market_stage6_database_url() -> str:
    raw = str(os.getenv("MARKET_STAGE6_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE6_TEST_DATABASE_NAME", "")).strip()
    if not raw and not database_name:
        return ""
    url = make_url(raw or str(os.getenv("DATABASE_URL", "")))
    if database_name:
        url = url.set(database=database_name)
    database_name = str(url.database or "")
    if not database_name.startswith("market_stage6_") or not database_name.endswith("_test"):
        raise RuntimeError(
            "MARKET_STAGE6_TEST_DATABASE_URL must target market_stage6_*_test"
        )
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


MARKET_STAGE6_DATABASE_URL = _market_stage6_database_url()


@unittest.skipUnless(
    MARKET_STAGE6_DATABASE_URL,
    "set MARKET_STAGE6_TEST_DATABASE_URL for the real PostgreSQL admission-fence tests",
)
class MarketOfferAdmissionPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(MARKET_STAGE6_DATABASE_URL, pool_pre_ping=True)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as session:
            await session.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS market_stage6_offer_lock_probe "
                    "(id INTEGER PRIMARY KEY)"
                )
            )
            await session.execute(text("TRUNCATE market_stage6_offer_lock_probe"))
            await session.execute(
                text("INSERT INTO market_stage6_offer_lock_probe (id) VALUES (1)")
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    async def _lock_republish_source(session) -> None:
        await session.execute(
            text(
                "SELECT id FROM market_stage6_offer_lock_probe "
                "WHERE id = 1 FOR UPDATE"
            )
        )

    async def test_close_lock_winner_blocks_then_rejects_final_offer_admission(self):
        close_session = self.Session()
        await close_session.begin()
        await market_transition_service._acquire_market_runtime_lock(close_session)

        attempt_started = asyncio.Event()

        async def attempt_offer_admission():
            async with self.Session() as create_session:
                attempt_started.set()
                with patch.object(
                    market_transition_service,
                    "evaluate_current_market_schedule",
                    new=AsyncMock(return_value=SimpleNamespace(is_open=False)),
                ):
                    with self.assertRaises(
                        market_transition_service.MarketOfferAdmissionClosedError
                    ):
                        await market_transition_service.acquire_market_offer_admission_fence(
                            create_session
                        )

        create_task = asyncio.create_task(attempt_offer_admission())
        await asyncio.wait_for(attempt_started.wait(), timeout=1)
        await asyncio.sleep(0.1)
        self.assertFalse(create_task.done())

        await close_session.commit()
        await asyncio.wait_for(create_task, timeout=2)
        await close_session.close()

    async def test_offer_lock_winner_commits_before_close_can_continue(self):
        create_session = self.Session()
        await create_session.begin()
        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ):
            await market_transition_service.acquire_market_offer_admission_fence(
                create_session
            )

        close_started = asyncio.Event()
        close_acquired = asyncio.Event()

        async def attempt_close_lock():
            async with self.Session() as close_session:
                close_started.set()
                await market_transition_service._acquire_market_runtime_lock(close_session)
                close_acquired.set()
                await close_session.rollback()

        close_task = asyncio.create_task(attempt_close_lock())
        await asyncio.wait_for(close_started.wait(), timeout=1)
        await asyncio.sleep(0.1)
        self.assertFalse(close_acquired.is_set())

        await create_session.commit()
        await asyncio.wait_for(close_task, timeout=2)
        self.assertTrue(close_acquired.is_set())
        await create_session.close()

    async def test_close_first_keeps_republish_source_unlocked_until_fence_releases(self):
        close_session = self.Session()
        await close_session.begin()
        await market_transition_service._acquire_market_runtime_lock(close_session)

        republish_started = asyncio.Event()
        republish_source_locked = asyncio.Event()

        async def attempt_republish_lock_order():
            async with self.Session() as republish_session:
                republish_started.set()
                with patch.object(
                    market_transition_service,
                    "evaluate_current_market_schedule",
                    new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
                ):
                    await market_transition_service.acquire_market_offer_admission_fence(
                        republish_session
                    )
                await self._lock_republish_source(republish_session)
                republish_source_locked.set()
                await republish_session.rollback()

        republish_task = asyncio.create_task(attempt_republish_lock_order())
        await asyncio.wait_for(republish_started.wait(), timeout=1)
        await asyncio.sleep(0.1)
        self.assertFalse(republish_source_locked.is_set())

        # A close/fence holder can still acquire the source row because the
        # republish path has not inverted source-row -> market-fence ordering.
        await self._lock_republish_source(close_session)
        await close_session.rollback()
        await close_session.close()

        await asyncio.wait_for(republish_task, timeout=2)
        self.assertTrue(republish_source_locked.is_set())

    async def test_republish_first_holds_fence_before_source_and_close_waits(self):
        republish_session = self.Session()
        await republish_session.begin()
        with patch.object(
            market_transition_service,
            "evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ):
            await market_transition_service.acquire_market_offer_admission_fence(
                republish_session
            )
        await self._lock_republish_source(republish_session)

        close_started = asyncio.Event()
        close_acquired = asyncio.Event()

        async def attempt_close():
            async with self.Session() as close_session:
                close_started.set()
                await market_transition_service._acquire_market_runtime_lock(close_session)
                close_acquired.set()
                await close_session.rollback()

        close_task = asyncio.create_task(attempt_close())
        await asyncio.wait_for(close_started.wait(), timeout=1)
        await asyncio.sleep(0.1)
        self.assertFalse(close_acquired.is_set())

        await republish_session.rollback()
        await republish_session.close()
        await asyncio.wait_for(close_task, timeout=2)
        self.assertTrue(close_acquired.is_set())


if __name__ == "__main__":
    unittest.main()
