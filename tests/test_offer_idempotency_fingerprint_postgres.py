import asyncio
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import SettlementType
from core.offer_source import OfferSourceSurface
from core.services.offer_creation_service import (
    OFFER_CREATION_FINGERPRINT_VERSION,
    OfferCreationCommand,
    OfferCreationIdempotencyConflictError,
    OfferCreationOutcome,
    OfferCreationQuotaPolicy,
    create_authoritative_offer_with_outcome,
)
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage15_[a-z0-9_]+_test$")


def _stage15_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE15_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE15_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None
    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with "
            "MARKET_STAGE15_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 15 PostgreSQL tests require a market_stage15_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage15_database_urls()


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


class OfferIdempotencyDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE15_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE15_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage15_\\*_test"):
                _stage15_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE15_TEST_DATABASE_NAME or MARKET_STAGE15_TEST_DATABASE_URL",
)
class OfferIdempotencyFingerprintPostgresTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, owner_count: int = 1) -> tuple[list[int], int]:
        suffix = uuid4().hex
        async with self.Session() as session:
            owners = [
                User(
                    account_name=f"stage15_{index}_{suffix[:12]}",
                    mobile_number=f"092{index}{int(suffix[:7], 16) % 10_000_000:07d}",
                    telegram_id=1_500_000_000 + index * 10_000_000 + int(suffix[:7], 16) % 9_000_000,
                    full_name=f"Stage 15 owner {index}",
                    address="Stage 15 scratch database",
                    role=UserRole.STANDARD,
                    home_server="iran",
                    must_change_password=False,
                    channel_messages_count=0,
                )
                for index in range(1, owner_count + 1)
            ]
            commodity = Commodity(name=f"stage15_commodity_{suffix}")
            session.add_all([*owners, commodity])
            await session.commit()
            return [owner.id for owner in owners], commodity.id

    @staticmethod
    def _command(
        owner_id: int,
        commodity_id: int,
        *,
        idempotency_key: str,
        price: int = 100_000,
        actor_user_id: int | None = None,
    ) -> OfferCreationCommand:
        return OfferCreationCommand(
            source_surface=OfferSourceSurface.WEBAPP,
            owner_user_id=owner_id,
            actor_user_id=actor_user_id or owner_id,
            offer_type=OfferType.BUY,
            settlement_type=SettlementType.CASH,
            commodity_id=commodity_id,
            quantity=20,
            price=price,
            is_wholesale=False,
            lot_sizes=[5, 15],
            original_lot_sizes=[5, 15],
            notes="stage15",
            idempotency_key=idempotency_key,
        )

    async def _create(self, command: OfferCreationCommand) -> OfferCreationOutcome:
        async with self.Session() as session:
            try:
                return await create_authoritative_offer_with_outcome(
                    session,
                    command,
                    validate_market=False,
                    quota_policy=OfferCreationQuotaPolicy(max_active_offers=10),
                )
            except Exception:
                await session.rollback()
                raise

    async def _counts(self, key: str) -> tuple[int, int]:
        async with self.Session() as session:
            offer_count = int(
                await session.scalar(
                    select(func.count(Offer.id)).where(Offer.idempotency_key == key)
                )
                or 0
            )
            counter_total = int(await session.scalar(select(func.sum(User.channel_messages_count))) or 0)
            return offer_count, counter_total

    async def test_concurrent_exact_replay_creates_one_offer_and_consumes_quota_once(self):
        owner_ids, commodity_id = await self._seed()
        key = f"stage15-exact-{uuid4().hex}"
        command = self._command(owner_ids[0], commodity_id, idempotency_key=key)

        results = await asyncio.gather(self._create(command), self._create(command))

        self.assertEqual(sorted(result.created for result in results), [False, True])
        self.assertEqual(results[0].offer.id, results[1].offer.id)
        self.assertEqual(await self._counts(key), (1, 1))
        self.assertEqual(
            results[0].offer.idempotency_fingerprint_version,
            OFFER_CREATION_FINGERPRINT_VERSION,
        )

    async def test_concurrent_same_key_with_different_payload_returns_one_conflict(self):
        owner_ids, commodity_id = await self._seed()
        key = f"stage15-mismatch-{uuid4().hex}"

        async def attempt(price: int):
            try:
                return await self._create(
                    self._command(
                        owner_ids[0],
                        commodity_id,
                        idempotency_key=key,
                        price=price,
                    )
                )
            except Exception as exc:
                return exc

        results = await asyncio.gather(attempt(100_000), attempt(100_001))

        self.assertEqual(sum(isinstance(item, OfferCreationOutcome) for item in results), 1)
        self.assertEqual(
            sum(isinstance(item, OfferCreationIdempotencyConflictError) for item in results),
            1,
        )
        self.assertEqual(await self._counts(key), (1, 1))

    async def test_global_unique_key_collision_between_owners_is_a_conflict_not_500(self):
        owner_ids, commodity_id = await self._seed(owner_count=2)
        key = f"stage15-global-{uuid4().hex}"

        async def attempt(owner_id: int):
            try:
                return await self._create(
                    self._command(owner_id, commodity_id, idempotency_key=key)
                )
            except Exception as exc:
                return exc

        results = await asyncio.gather(*(attempt(owner_id) for owner_id in owner_ids))

        self.assertEqual(sum(isinstance(item, OfferCreationOutcome) for item in results), 1)
        self.assertEqual(
            sum(isinstance(item, OfferCreationIdempotencyConflictError) for item in results),
            1,
        )
        self.assertEqual(await self._counts(key), (1, 1))

    async def test_stored_fingerprint_survives_mutable_offer_state_changes(self):
        owner_ids, commodity_id = await self._seed()
        key = f"stage15-state-{uuid4().hex}"
        command = self._command(owner_ids[0], commodity_id, idempotency_key=key)
        created = await self._create(command)
        async with self.Session() as session:
            offer = await session.get(Offer, created.offer.id)
            offer.remaining_quantity = 0
            offer.lot_sizes = []
            offer.status = OfferStatus.COMPLETED
            await session.commit()

        replay = await self._create(command)

        self.assertFalse(replay.created)
        self.assertEqual(replay.offer.id, created.offer.id)
        self.assertEqual(await self._counts(key), (1, 1))

    async def test_legacy_row_without_fingerprint_is_reconstructed_conservatively(self):
        owner_ids, commodity_id = await self._seed()
        key = f"stage15-legacy-{uuid4().hex}"
        command = self._command(owner_ids[0], commodity_id, idempotency_key=key)
        async with self.Session() as session:
            legacy = Offer(
                user_id=owner_ids[0],
                actor_user_id=owner_ids[0],
                home_server="iran",
                offer_type=OfferType.BUY,
                settlement_type=SettlementType.CASH,
                commodity_id=commodity_id,
                quantity=20,
                remaining_quantity=5,
                price=100_000,
                is_wholesale=False,
                lot_sizes=[5],
                original_lot_sizes=[15, 5],
                notes="stage15",
                status=OfferStatus.ACTIVE,
                idempotency_key=key,
                idempotency_fingerprint_version=None,
                idempotency_fingerprint=None,
            )
            session.add(legacy)
            await session.commit()

        replay = await self._create(command)
        self.assertFalse(replay.created)
        with self.assertRaises(OfferCreationIdempotencyConflictError):
            await self._create(
                self._command(
                    owner_ids[0],
                    commodity_id,
                    idempotency_key=key,
                    price=100_001,
                )
            )
        self.assertEqual(await self._counts(key), (1, 0))


if __name__ == "__main__":
    unittest.main()
