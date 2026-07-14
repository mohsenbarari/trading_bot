import asyncio
from datetime import datetime, timedelta
import json
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.offer_source import OfferSourceSurface
from core import events
from core.services.offer_creation_service import (
    OfferCreationCommand,
    OfferCreationLimitExceededError,
    OfferCreationOutcome,
    OfferCreationQuotaPolicy,
    _lock_offer_owner,
    create_authoritative_offer_with_outcome,
)
from models.commodity import Commodity
from models.change_log import ChangeLog
from models.offer import Offer
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage7_[a-z0-9_]+_test$")


def _stage7_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE7_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE7_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None

    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with "
            "MARKET_STAGE7_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 7 PostgreSQL tests require a market_stage7_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage7_database_urls()


def _capture_listeners(registry):
    def listens_for(model, event_name):
        def decorator(func):
            registry.append((model, event_name, func))
            return func

        return decorator

    return listens_for


def _run_alembic(sync_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["SYNC_DATABASE_URL"] = sync_url
    env["DATABASE_URL"] = sync_url
    env["TRADING_BOT_MIGRATION_MODE"] = "scratch"
    env["TRADING_BOT_EXPECTED_CHECKOUT"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "scripts/run_guarded_scratch_alembic.py", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


class OfferQuotaDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE7_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE7_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage7_\\*_test"):
                _stage7_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE7_TEST_DATABASE_NAME or MARKET_STAGE7_TEST_DATABASE_URL",
)
class OfferQuotaPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    async def asyncSetUp(self):
        _, async_url = DATABASE_URLS
        self.engine = create_async_engine(async_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self.listeners = []
        captured = []
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(captured)):
            events.setup_user_events()
            events.setup_offer_events()
        for model, event_name, listener in captured:
            event.listen(model, event_name, listener)
            self.listeners.append((model, event_name, listener))
        self.events_settings = patch.multiple(
            "core.events.settings",
            registration_sync_v2_enabled=True,
            server_mode="iran",
        )
        self.events_settings.start()

    async def asyncTearDown(self):
        self.events_settings.stop()
        for model, event_name, listener in reversed(self.listeners):
            event.remove(model, event_name, listener)
        await self.engine.dispose()

    async def _seed_owner(
        self,
        *,
        max_daily_requests: int | None = None,
        channel_messages_count: int = 0,
        limitations_expire_at: datetime | None = None,
    ) -> tuple[int, int]:
        suffix = uuid4().hex
        async with self.Session() as session:
            owner = User(
                account_name=f"stage7_{suffix[:16]}",
                mobile_number=f"091{int(suffix[:8], 16) % 100000000:08d}",
                telegram_id=1_000_000_000 + int(suffix[:8], 16) % 900_000_000,
                full_name="Stage 7 quota owner",
                address="Stage 7 test address",
                role=UserRole.STANDARD,
                home_server="iran",
                must_change_password=False,
                max_daily_requests=max_daily_requests,
                channel_messages_count=channel_messages_count,
                limitations_expire_at=limitations_expire_at,
            )
            commodity = Commodity(name=f"stage7_commodity_{suffix}")
            session.add_all([owner, commodity])
            await session.commit()
            return owner.id, commodity.id

    @staticmethod
    def _command(
        owner_id: int,
        commodity_id: int,
        *,
        source_surface: OfferSourceSurface,
        idempotency_key: str | None = None,
    ) -> OfferCreationCommand:
        return OfferCreationCommand(
            source_surface=source_surface,
            owner_user_id=owner_id,
            actor_user_id=owner_id,
            offer_type="buy",
            commodity_id=commodity_id,
            quantity=1,
            price=100_000,
            idempotency_key=idempotency_key,
        )

    async def _create(
        self,
        command: OfferCreationCommand,
        *,
        max_active_offers: int,
        commit: bool = True,
    ) -> OfferCreationOutcome:
        async with self.Session() as session:
            try:
                return await create_authoritative_offer_with_outcome(
                    session,
                    command,
                    commit=commit,
                    refresh=commit,
                    validate_market=False,
                    enforce_market_admission=False,
                    quota_policy=OfferCreationQuotaPolicy(
                        max_active_offers=max_active_offers,
                    ),
                )
            except Exception:
                await session.rollback()
                raise

    async def _stored_counts(self, owner_id: int) -> tuple[int, int]:
        async with self.Session() as session:
            offers = int(
                await session.scalar(
                    select(func.count(Offer.id)).where(Offer.user_id == owner_id)
                )
                or 0
            )
            counter = int(
                await session.scalar(
                    select(User.channel_messages_count).where(User.id == owner_id)
                )
                or 0
            )
            return offers, counter

    async def _counter_event_count(self, owner_id: int) -> int:
        async with self.Session() as session:
            rows = list(
                (
                    await session.execute(
                        select(ChangeLog).where(
                            ChangeLog.table_name == "users",
                            ChangeLog.record_id == owner_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        count = 0
        for row in rows:
            payload = json.loads(row.data) if isinstance(row.data, str) else dict(row.data)
            if payload.get("_sync_contract") == "user_counter_event_v2":
                count += 1
        return count

    async def test_webapp_and_bot_compete_for_one_local_active_slot(self):
        owner_id, commodity_id = await self._seed_owner()
        start = asyncio.Event()

        async def compete(surface: OfferSourceSurface):
            await start.wait()
            try:
                return await self._create(
                    self._command(owner_id, commodity_id, source_surface=surface),
                    max_active_offers=1,
                )
            except Exception as exc:
                return exc

        tasks = [
            asyncio.create_task(compete(OfferSourceSurface.WEBAPP)),
            asyncio.create_task(compete(OfferSourceSurface.TELEGRAM_BOT)),
        ]
        start.set()
        results = await asyncio.gather(*tasks)

        self.assertEqual(sum(isinstance(item, OfferCreationOutcome) for item in results), 1)
        self.assertEqual(
            sum(isinstance(item, OfferCreationLimitExceededError) for item in results),
            1,
        )
        self.assertEqual(await self._stored_counts(owner_id), (1, 1))
        self.assertEqual(await self._counter_event_count(owner_id), 1)

    async def test_concurrent_requests_share_limitation_period_counter(self):
        owner_id, commodity_id = await self._seed_owner(
            max_daily_requests=1,
            limitations_expire_at=datetime.utcnow() + timedelta(days=2),
        )

        async def create(surface: OfferSourceSurface):
            try:
                return await self._create(
                    self._command(owner_id, commodity_id, source_surface=surface),
                    max_active_offers=5,
                )
            except Exception as exc:
                return exc

        results = await asyncio.gather(
            create(OfferSourceSurface.WEBAPP),
            create(OfferSourceSurface.TELEGRAM_BOT),
        )

        self.assertEqual(sum(isinstance(item, OfferCreationOutcome) for item in results), 1)
        limit_errors = [item for item in results if isinstance(item, OfferCreationLimitExceededError)]
        self.assertEqual(len(limit_errors), 1)
        self.assertIn("channel_message", limit_errors[0].reason)
        self.assertEqual(await self._stored_counts(owner_id), (1, 1))
        self.assertEqual(await self._counter_event_count(owner_id), 1)

    async def test_rollback_releases_offer_and_counter_together(self):
        owner_id, commodity_id = await self._seed_owner()
        command = self._command(
            owner_id,
            commodity_id,
            source_surface=OfferSourceSurface.WEBAPP,
        )
        async with self.Session() as session:
            outcome = await create_authoritative_offer_with_outcome(
                session,
                command,
                commit=False,
                refresh=False,
                validate_market=False,
                quota_policy=OfferCreationQuotaPolicy(max_active_offers=1),
            )
            self.assertTrue(outcome.created)
            await session.rollback()

        self.assertEqual(await self._stored_counts(owner_id), (0, 0))
        self.assertEqual(await self._counter_event_count(owner_id), 0)
        retry = await self._create(command, max_active_offers=1)
        self.assertTrue(retry.created)
        self.assertEqual(await self._stored_counts(owner_id), (1, 1))
        self.assertEqual(await self._counter_event_count(owner_id), 1)

    async def test_concurrent_idempotent_retry_consumes_quota_once(self):
        owner_id, commodity_id = await self._seed_owner()
        command = self._command(
            owner_id,
            commodity_id,
            source_surface=OfferSourceSurface.WEBAPP,
            idempotency_key=f"stage7-{uuid4().hex}",
        )
        results = await asyncio.gather(
            self._create(command, max_active_offers=1),
            self._create(command, max_active_offers=1),
        )

        self.assertEqual(sorted(item.created for item in results), [False, True])
        self.assertEqual(results[0].offer.id, results[1].offer.id)
        self.assertEqual(await self._stored_counts(owner_id), (1, 1))
        self.assertEqual(await self._counter_event_count(owner_id), 1)

    async def test_different_user_row_does_not_wait_for_unrelated_quota_lock(self):
        first_owner_id, _ = await self._seed_owner()
        second_owner_id, commodity_id = await self._seed_owner()

        blocker = self.Session()
        await blocker.begin()
        await _lock_offer_owner(blocker, first_owner_id)
        try:
            outcome = await asyncio.wait_for(
                self._create(
                    self._command(
                        second_owner_id,
                        commodity_id,
                        source_surface=OfferSourceSurface.TELEGRAM_BOT,
                    ),
                    max_active_offers=1,
                ),
                timeout=1.5,
            )
        finally:
            await blocker.rollback()
            await blocker.close()

        self.assertTrue(outcome.created)
        self.assertEqual(await self._stored_counts(second_owner_id), (1, 1))

    async def test_calendar_day_change_does_not_reset_current_limitation_period(self):
        owner_id, commodity_id = await self._seed_owner(
            max_daily_requests=1,
            channel_messages_count=1,
            limitations_expire_at=datetime(2026, 7, 20, 12, 0, 0),
        )
        command = self._command(
            owner_id,
            commodity_id,
            source_surface=OfferSourceSurface.WEBAPP,
        )

        with patch(
            "core.utils.utc_now_naive",
            return_value=datetime(2026, 7, 15, 0, 1, 0),
        ):
            with self.assertRaises(OfferCreationLimitExceededError):
                await self._create(command, max_active_offers=5)

        self.assertEqual(await self._stored_counts(owner_id), (0, 1))


if __name__ == "__main__":
    unittest.main()
