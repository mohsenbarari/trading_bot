import os
import re
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routers.offers import (
    _active_offer_filter_signature,
    _build_active_offer_page_query,
    _decode_active_offer_cursor,
    _encode_active_offer_cursor,
)
from core.enums import SettlementType
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage12_[a-z0-9_]+_test$")


def _stage12_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE12_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE12_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None
    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with MARKET_STAGE12_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 12 PostgreSQL tests require a market_stage12_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage12_database_urls()


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


class ActiveOfferPaginationDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE12_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE12_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage12_\\*_test"):
                _stage12_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE12_TEST_DATABASE_NAME or MARKET_STAGE12_TEST_DATABASE_URL",
)
class ActiveOfferPaginationPostgresTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_offers(self, count: int = 137):
        async with self.Session() as session:
            owner = User(
                account_name="stage12_owner",
                mobile_number="09000000112",
                telegram_id=3_120_000_012,
                full_name="Stage 12 owner",
                address="Stage 12 scratch database",
                role=UserRole.STANDARD,
                home_server="foreign",
                must_change_password=False,
            )
            other = User(
                account_name="stage12_other",
                mobile_number="09000000113",
                telegram_id=3_120_000_013,
                full_name="Stage 12 other",
                address="Stage 12 scratch database",
                role=UserRole.STANDARD,
                home_server="foreign",
                must_change_password=False,
            )
            coin = Commodity(name="stage12_coin")
            gold = Commodity(name="stage12_gold")
            session.add_all([owner, other, coin, gold])
            await session.flush()

            base_time = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            offers = []
            for index in range(count):
                offers.append(
                    Offer(
                        offer_public_id=f"ofr_stage12_{index:020d}",
                        user_id=owner.id if index % 3 else other.id,
                        home_server="foreign",
                        offer_type=OfferType.BUY if index % 2 == 0 else OfferType.SELL,
                        settlement_type=(
                            SettlementType.CASH if index % 4 < 2 else SettlementType.TOMORROW
                        ),
                        commodity_id=coin.id if index % 5 else gold.id,
                        quantity=10,
                        remaining_quantity=10,
                        price=100_000 + index,
                        status=OfferStatus.ACTIVE,
                        is_wholesale=True,
                        archived=False,
                        # Repeated timestamps force the id tie-breaker to participate.
                        created_at=base_time - timedelta(minutes=index // 5),
                    )
                )
            session.add_all(offers)
            await session.commit()
            expected = sorted(
                offers,
                key=lambda offer: (offer.created_at, offer.id),
                reverse=True,
            )
            return owner.id, other.id, coin.id, gold.id, expected

    async def _read_page(
        self,
        *,
        owner_user_id: int,
        limit: int,
        cursor: str | None = None,
        offer_type: str | None = None,
        settlement_type: str | None = None,
        commodity_id: int | None = None,
        own_only: bool = False,
    ) -> tuple[list[Offer], str | None, bool]:
        signature = _active_offer_filter_signature(
            offer_type=offer_type,
            settlement_type=settlement_type,
            commodity_id=commodity_id,
            own_only=own_only,
        )
        position = (
            _decode_active_offer_cursor(cursor, expected_filter_signature=signature)
            if cursor
            else None
        )
        statement = _build_active_offer_page_query(
            owner_user_id=owner_user_id,
            offer_type=offer_type,
            settlement_type=settlement_type,
            commodity_id=commodity_id,
            own_only=own_only,
            cursor_position=position,
            limit=limit,
        )
        async with self.Session() as session:
            rows = list((await session.execute(statement)).scalars().all())
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_active_offer_cursor(page[-1], filter_signature=signature)
            if has_more and page
            else None
        )
        return page, next_cursor, has_more

    async def _read_all(self, **kwargs) -> list[Offer]:
        cursor = None
        rows = []
        while True:
            page, cursor, has_more = await self._read_page(cursor=cursor, **kwargs)
            rows.extend(page)
            if not has_more:
                return rows

    async def test_51_100_and_137_rows_are_accessible_once_in_stable_order(self):
        owner_id, _, _, _, expected = await self._seed_offers()

        first_51, cursor_51, has_more_51 = await self._read_page(
            owner_user_id=owner_id,
            limit=51,
        )
        self.assertEqual([offer.id for offer in first_51], [offer.id for offer in expected[:51]])
        self.assertTrue(has_more_51)
        self.assertIsNotNone(cursor_51)

        first_100, cursor_100, has_more_100 = await self._read_page(
            owner_user_id=owner_id,
            limit=100,
        )
        self.assertEqual(len(first_100), 100)
        self.assertTrue(has_more_100)
        second_100, final_cursor, final_has_more = await self._read_page(
            owner_user_id=owner_id,
            limit=100,
            cursor=cursor_100,
        )
        self.assertEqual(len(second_100), 37)
        self.assertFalse(final_has_more)
        self.assertIsNone(final_cursor)

        all_rows = first_100 + second_100
        self.assertEqual([offer.id for offer in all_rows], [offer.id for offer in expected])
        self.assertEqual(len({offer.id for offer in all_rows}), 137)

    async def test_server_filters_return_exact_matching_rows(self):
        owner_id, _, coin_id, _, expected = await self._seed_offers()
        filtered = await self._read_all(
            owner_user_id=owner_id,
            limit=17,
            offer_type="buy",
            settlement_type="cash",
            commodity_id=coin_id,
            own_only=True,
        )
        expected_filtered = [
            offer for offer in expected
            if offer.offer_type == OfferType.BUY
            and offer.settlement_type == SettlementType.CASH
            and offer.commodity_id == coin_id
            and offer.user_id == owner_id
        ]
        self.assertEqual([offer.id for offer in filtered], [offer.id for offer in expected_filtered])

    async def test_insert_and_expire_between_pages_do_not_duplicate_or_resurface_rows(self):
        owner_id, _, coin_id, _, expected = await self._seed_offers()
        first_page, cursor, has_more = await self._read_page(
            owner_user_id=owner_id,
            limit=50,
        )
        self.assertTrue(has_more)
        first_ids = {offer.id for offer in first_page}
        expiring = expected[80]

        async with self.Session() as session:
            expiring_row = await session.get(Offer, expiring.id)
            expiring_row.status = OfferStatus.EXPIRED
            newest = Offer(
                offer_public_id="ofr_stage12_new_between_pages",
                user_id=owner_id,
                home_server="foreign",
                offer_type=OfferType.BUY,
                settlement_type=SettlementType.CASH,
                commodity_id=coin_id,
                quantity=10,
                remaining_quantity=10,
                price=200_000,
                status=OfferStatus.ACTIVE,
                is_wholesale=True,
                archived=False,
                created_at=expected[0].created_at + timedelta(minutes=1),
            )
            session.add(newest)
            await session.commit()
            newest_id = newest.id

        continued = []
        while has_more:
            page, cursor, has_more = await self._read_page(
                owner_user_id=owner_id,
                limit=50,
                cursor=cursor,
            )
            continued.extend(page)

        continued_ids = [offer.id for offer in continued]
        self.assertNotIn(newest_id, continued_ids)
        self.assertNotIn(expiring.id, continued_ids)
        self.assertTrue(first_ids.isdisjoint(continued_ids))
        self.assertEqual(len(continued_ids), 86)
        self.assertEqual(len(set(continued_ids)), len(continued_ids))

        fresh_page, _, _ = await self._read_page(owner_user_id=owner_id, limit=50)
        self.assertEqual(fresh_page[0].id, newest_id)
        self.assertNotIn(expiring.id, [offer.id for offer in fresh_page])


if __name__ == "__main__":
    unittest.main()
