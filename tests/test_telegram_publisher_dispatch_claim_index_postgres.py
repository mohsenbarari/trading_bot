"""Scratch PostgreSQL round-trip for claim-index migration ff6c7d8e9f01."""
from __future__ import annotations

import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


REVISION = "ff6c7d8e9f01"
DOWN_REVISION = "ff5a6b7c8d9e"
INDEX = "ix_telegram_publisher_dispatch_commands_claim"


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramPublisherDispatchClaimIndexPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DATABASE_URLS.runtime_async, pool_pre_ping=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _indexdef(self) -> str:
        async with self.engine.connect() as connection:
            return (
                await connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                    {"name": INDEX},
                )
            ).scalar_one()

    async def test_upgrade_downgrade_round_trip_keeps_claim_predicate(self):
        _run_alembic(DATABASE_URLS.owner_sync, "upgrade", "head")
        upgraded = await self._indexdef()
        self.assertIn("pending", upgraded)
        self.assertIn("retry_due", upgraded)
        self.assertIn("sent", upgraded)
        self.assertIn("(id)", upgraded)

        _run_alembic(DATABASE_URLS.owner_sync, "downgrade", DOWN_REVISION)
        downgraded = await self._indexdef()
        self.assertIn("pending", downgraded)
        self.assertIn("retry_due", downgraded)
        self.assertNotIn("sent", downgraded)
        self.assertIn("state", downgraded)
        self.assertIn("next_retry_at", downgraded)

        _run_alembic(DATABASE_URLS.owner_sync, "upgrade", REVISION)
        restored = await self._indexdef()
        self.assertEqual(restored, upgraded)
        self.assertIn("sent", restored)
        self.assertIn("(id)", restored)
