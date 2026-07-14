import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    async def asyncTearDown(self):
        await self.engine.dispose()

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


if __name__ == "__main__":
    unittest.main()
