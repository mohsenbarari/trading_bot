import os
import re
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.events import setup_event_listeners
from core.services.offer_cancel_all_service import (
    OfferCancelAllItemStatus,
    _load_active_candidates,
    cancel_all_active_offers_authoritatively,
)
from core.services.offer_expiry_service import OfferExpiryReason, OfferExpirySourceSurface
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage10_[a-z0-9_]+_test$")


def _stage10_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE10_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE10_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None
    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with MARKET_STAGE10_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 10 PostgreSQL tests require a market_stage10_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage10_database_urls()


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


class FakeLease:
    acquired = True

    async def release(self):
        return None


class OfferCancelAllDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE10_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE10_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage10_\\*_test"):
                _stage10_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE10_TEST_DATABASE_NAME or MARKET_STAGE10_TEST_DATABASE_URL",
)
class OfferCancelAllPostgresTests(unittest.IsolatedAsyncioTestCase):
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
        async with self.Session() as session:
            await session.execute(
                text(
                    "TRUNCATE TABLE change_log, offers, commodities, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()
        setup_event_listeners()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_offers(self, count: int = 2) -> tuple[int, list[int]]:
        async with self.Session() as session:
            user = User(
                account_name="stage10_owner",
                mobile_number="09000000110",
                telegram_id=3_100_000_010,
                full_name="Stage 10 owner",
                address="Stage 10 scratch database",
                role=UserRole.STANDARD,
                home_server="foreign",
                must_change_password=False,
            )
            commodity = Commodity(name="stage10_commodity")
            session.add_all([user, commodity])
            await session.flush()
            offers = []
            for index in range(1, count + 1):
                offers.append(
                    Offer(
                        offer_public_id=f"ofr_stage10_{index:020d}",
                        user_id=user.id,
                        home_server="foreign",
                        offer_type=OfferType.BUY,
                        commodity_id=commodity.id,
                        quantity=10,
                        remaining_quantity=10,
                        price=100_000 + index,
                        status=OfferStatus.ACTIVE,
                        is_wholesale=True,
                        archived=False,
                    )
                )
            session.add_all(offers)
            await session.commit()
            return user.id, [int(offer.id) for offer in offers]

    async def _cancel_all(self, owner_user_id: int):
        with patch(
            "core.services.offer_cancel_all_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_cancel_all_service.try_acquire_offer_expiry_gate",
            new=AsyncMock(side_effect=lambda **_kwargs: FakeLease()),
        ):
            return await cancel_all_active_offers_authoritatively(
                owner_user_id=owner_user_id,
                actor_user_id=owner_user_id,
                source_surface=OfferExpirySourceSurface.WEBAPP,
                expire_reason=OfferExpiryReason.CANCEL_ALL,
                session_factory=self.Session,
                include_command_identity=True,
            )

    async def test_locked_offer_failure_does_not_rollback_independent_offer(self):
        owner_id, offer_ids = await self._seed_offers(2)
        holder = self.Session()
        try:
            await holder.execute(
                select(Offer).where(Offer.id == offer_ids[0]).with_for_update()
            )
            result = await self._cancel_all(owner_id)
        finally:
            await holder.rollback()
            await holder.close()

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.cancelled_count, 1)
        self.assertEqual(result.remaining_active_count, 1)
        self.assertEqual(result.items[0].error_code, "lock_busy")
        async with self.Session() as session:
            offers = (
                await session.execute(select(Offer).order_by(Offer.offer_public_id.asc()))
            ).scalars().all()
        self.assertEqual(
            [offer.status for offer in offers],
            [OfferStatus.ACTIVE, OfferStatus.EXPIRED],
        )

    async def test_trade_wins_row_lock_without_impossible_expiry_state(self):
        owner_id, offer_ids = await self._seed_offers(1)
        holder = self.Session()
        try:
            locked = (
                await holder.execute(
                    select(Offer).where(Offer.id == offer_ids[0]).with_for_update()
                )
            ).scalar_one()
            locked.status = OfferStatus.COMPLETED
            locked.remaining_quantity = 0
            await holder.flush()
            result = await self._cancel_all(owner_id)
            await holder.commit()
        finally:
            await holder.close()

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.cancelled_count, 0)
        async with self.Session() as session:
            offer = await session.get(Offer, offer_ids[0])
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        self.assertEqual(offer.remaining_quantity, 0)
        self.assertIsNone(offer.expire_reason)

    async def test_stale_snapshot_of_completed_offer_is_reported_already_inactive(self):
        owner_id, offer_ids = await self._seed_offers(1)
        with patch(
            "core.services.offer_cancel_all_service.current_server",
            return_value="foreign",
        ):
            candidates = await _load_active_candidates(
                self.Session,
                owner_user_id=owner_id,
            )
        async with self.Session() as session:
            offer = await session.get(Offer, offer_ids[0])
            offer.status = OfferStatus.COMPLETED
            offer.remaining_quantity = 0
            await session.commit()

        with patch(
            "core.services.offer_cancel_all_service._load_active_candidates",
            new=AsyncMock(return_value=candidates),
        ), patch(
            "core.services.offer_cancel_all_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="foreign",
        ), patch(
            "core.services.offer_cancel_all_service.try_acquire_offer_expiry_gate",
            new=AsyncMock(side_effect=lambda **_kwargs: FakeLease()),
        ):
            result = await cancel_all_active_offers_authoritatively(
                owner_user_id=owner_id,
                actor_user_id=owner_id,
                source_surface=OfferExpirySourceSurface.WEBAPP,
                expire_reason=OfferExpiryReason.CANCEL_ALL,
                session_factory=self.Session,
                include_command_identity=True,
            )

        self.assertEqual(result.already_inactive_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.remaining_active_count, 0)


if __name__ == "__main__":
    unittest.main()
