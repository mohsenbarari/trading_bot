import asyncio
from datetime import timedelta
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import offer_publication_worker as worker
from core.services import offer_publication_reconciliation_service as reconciliation
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from core.utils import utc_now
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage9_[a-z0-9_]+_test$")


def _stage9_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE9_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE9_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None
    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with MARKET_STAGE9_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 9 PostgreSQL tests require a market_stage9_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage9_database_urls()


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


class OfferPublicationRepairDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE9_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE9_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage9_\\*_test"):
                _stage9_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE9_TEST_DATABASE_NAME or MARKET_STAGE9_TEST_DATABASE_URL",
)
class OfferPublicationRepairPostgresTests(unittest.IsolatedAsyncioTestCase):
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
                    "TRUNCATE TABLE offer_publication_states, offers, commodities, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_channel_candidates(self, count: int) -> list[str]:
        suffix = uuid4().hex[:10]
        public_ids: list[str] = []
        async with self.Session() as session:
            commodity = Commodity(name=f"stage9_channel_{suffix}")
            session.add(commodity)
            await session.flush()
            for index in range(count):
                public_id = f"ofr_stage9_channel_{suffix}_{index}"
                public_ids.append(public_id)
                offer = Offer(
                    offer_public_id=public_id,
                    home_server="foreign",
                    offer_type=OfferType.BUY,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=0,
                    price=100_000,
                    status=OfferStatus.EXPIRED,
                    channel_message_id=900_000 + index,
                    is_wholesale=True,
                    archived=False,
                )
                session.add(offer)
                await session.flush()
                session.add(
                    OfferPublicationState(
                        offer_id=offer.id,
                        offer_public_id=public_id,
                        offer_home_server="foreign",
                        surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
                        publication_owner_server="foreign",
                        status=OfferPublicationStatus.SENT,
                        dedupe_key=f"offer-publication:telegram_channel:{public_id}",
                        telegram_message_id=offer.channel_message_id,
                        offer_version_id=0,
                        last_known_offer_status=OfferStatus.ACTIVE.value,
                    )
                )
            await session.commit()
        return public_ids

    async def _seed_publication_candidates(
        self,
        count: int,
        *,
        created_offset: timedelta,
        prefix: str,
    ) -> list[str]:
        suffix = uuid4().hex[:10]
        public_ids: list[str] = []
        created_at = utc_now() + created_offset
        async with self.Session() as session:
            user = User(
                account_name=f"stage9_{prefix}_{suffix}",
                mobile_number=f"09{int(suffix, 16) % 1_000_000_000:09d}",
                telegram_id=2_000_000_000 + int(suffix, 16) % 1_000_000_000,
                full_name="Stage 9 worker owner",
                address="Stage 9 scratch database",
                role=UserRole.STANDARD,
                home_server="foreign",
                must_change_password=False,
            )
            commodity = Commodity(name=f"stage9_{prefix}_{suffix}")
            session.add_all([user, commodity])
            await session.flush()
            for index in range(count):
                public_id = f"ofr_stage9_{prefix}_{suffix}_{index}"
                public_ids.append(public_id)
                offer = Offer(
                    offer_public_id=public_id,
                    user_id=user.id,
                    home_server="foreign",
                    offer_type=OfferType.BUY,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=10,
                    price=100_000,
                    status=OfferStatus.ACTIVE,
                    is_wholesale=True,
                    archived=False,
                    created_at=created_at,
                )
                session.add(offer)
                await session.flush()
                session.add(
                    OfferPublicationState(
                        offer_id=offer.id,
                        offer_public_id=public_id,
                        offer_home_server="foreign",
                        surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
                        publication_owner_server="foreign",
                        status=OfferPublicationStatus.FAILED,
                        dedupe_key=f"offer-publication:telegram_channel:{public_id}",
                        offer_version_id=offer.version_id,
                        last_known_offer_status=OfferStatus.ACTIVE.value,
                        last_attempt_at=created_at,
                        error_code="stage9_seed_failure",
                    )
                )
            await session.commit()
        return public_ids

    async def test_channel_backlog_of_101_drains_across_bounded_cycles(self):
        await self._seed_channel_candidates(101)
        apply_state = AsyncMock(
            return_value=OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")
        )
        with patch("core.offer_publication_worker.AsyncSessionLocal", self.Session), patch(
            "core.offer_publication_worker.assert_background_job_authority"
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result", new=apply_state
        ), patch("core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0):
            reports = [await worker.run_offer_channel_state_cycle(limit=25) for _ in range(5)]

        self.assertEqual(apply_state.await_count, 101)
        self.assertEqual(sum(report.applied for report in reports), 101)
        self.assertEqual(reports[-1].backlog_total, 0)

    async def test_continuous_new_publications_do_not_starve_old_candidates(self):
        old_ids = await self._seed_publication_candidates(
            50,
            created_offset=timedelta(hours=-1),
            prefix="old",
        )

        async def send(offer, _user):
            return 1_000_000 + int(offer.id)

        with patch(
            "core.services.telegram_offer_publication_service.current_server", return_value="foreign"
        ):
            async with self.Session() as session:
                first = await reconciliation.reconcile_offer_publications(
                    session,
                    server_mode="foreign",
                    dry_run=False,
                    limit=25,
                    send_offer_to_channel=send,
                )
            await self._seed_publication_candidates(
                100,
                created_offset=timedelta(0),
                prefix="new",
            )
            async with self.Session() as session:
                second = await reconciliation.reconcile_offer_publications(
                    session,
                    server_mode="foreign",
                    dry_run=False,
                    limit=25,
                    send_offer_to_channel=send,
                )

        async with self.Session() as session:
            remaining_old = list(
                (
                    await session.execute(
                        select(Offer.offer_public_id).where(
                            Offer.offer_public_id.in_(old_ids),
                            Offer.channel_message_id.is_(None),
                        )
                    )
                ).scalars()
            )
        self.assertEqual(first["repaired"], 25)
        self.assertEqual(second["repaired"], 25)
        self.assertEqual(remaining_old, [])

    async def test_two_channel_workers_do_not_duplicate_provider_edit(self):
        await self._seed_channel_candidates(1)
        entered = asyncio.Event()
        release = asyncio.Event()
        second_lock_checked = asyncio.Event()
        original_lock = worker._try_acquire_channel_state_lock
        lock_call_count = 0

        async def observed_lock(db, offer):
            nonlocal lock_call_count
            acquired = await original_lock(db, offer)
            lock_call_count += 1
            if lock_call_count >= 2:
                second_lock_checked.set()
            return acquired

        async def blocking_apply(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return OfferChannelStateApplyResult(ok=True, response_class="2xx", reason="ok")

        apply_state = AsyncMock(side_effect=blocking_apply)
        with patch("core.offer_publication_worker.AsyncSessionLocal", self.Session), patch(
            "core.offer_publication_worker.assert_background_job_authority"
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result", new=apply_state
        ), patch(
            "core.offer_publication_worker._try_acquire_channel_state_lock", side_effect=observed_lock
        ), patch("core.offer_publication_worker._channel_edit_spacing_seconds", return_value=0):
            first_task = asyncio.create_task(worker.run_offer_channel_state_cycle(limit=1))
            await asyncio.wait_for(entered.wait(), timeout=2)
            second_task = asyncio.create_task(worker.run_offer_channel_state_cycle(limit=1))
            await asyncio.wait_for(second_lock_checked.wait(), timeout=2)
            release.set()
            first, second = await asyncio.gather(first_task, second_task)

        self.assertEqual(apply_state.await_count, 1)
        self.assertEqual(first.applied + second.applied, 1)
        self.assertEqual(first.skipped_locked + second.skipped_locked, 1)


if __name__ == "__main__":
    unittest.main()
