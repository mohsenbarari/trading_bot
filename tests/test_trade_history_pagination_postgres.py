import os
import re
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routers.trades import (
    _build_my_trades_query,
    _build_trade_history_page_query,
    _build_trades_with_user_query,
    _decode_trade_history_cursor,
    _encode_trade_history_cursor,
    _trade_history_filter_signature,
)
from core.enums import SettlementType
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.trade import Trade, TradeStatus, TradeType
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage13_[a-z0-9_]+_test$")


def _stage13_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE13_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE13_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None
    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with MARKET_STAGE13_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 13 PostgreSQL tests require a market_stage13_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage13_database_urls()


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


class TradeHistoryPaginationDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE13_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE13_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage13_\\*_test"):
                _stage13_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE13_TEST_DATABASE_NAME or MARKET_STAGE13_TEST_DATABASE_URL",
)
class TradeHistoryPaginationPostgresTests(unittest.IsolatedAsyncioTestCase):
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
                    "TRUNCATE TABLE change_log, trades, customer_relations, offers, "
                    "commodities, users RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _context(user: User):
        return type(
            "Context",
            (),
            {
                "owner_user": user,
                "actor_user": user,
                "relation": None,
                "is_accountant_context": False,
            },
        )()

    async def _seed_history(self):
        async with self.Session() as session:
            users = [
                User(
                    account_name=name,
                    mobile_number=f"0900000010{index}",
                    telegram_id=3_130_000_010 + index,
                    full_name=name,
                    address="Stage 13 scratch database",
                    role=UserRole.STANDARD,
                    home_server="iran",
                    must_change_password=False,
                )
                for index, name in enumerate(
                    ["stage13_owner", "stage13_customer", "stage13_outsider", "stage13_third"],
                    start=1,
                )
            ]
            owner, customer, outsider, third = users
            coin = Commodity(name="stage13_coin")
            gold = Commodity(name="stage13_gold")
            session.add_all([*users, coin, gold])
            await session.flush()

            base_time = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
            relation = CustomerRelation(
                owner_user_id=owner.id,
                customer_user_id=customer.id,
                created_by_user_id=owner.id,
                invitation_token="stage13-customer-relation",
                management_name="stage13 customer",
                customer_tier=CustomerTier.TIER_1,
                status=CustomerRelationStatus.ACTIVE,
                activated_at=base_time - timedelta(days=30),
                created_at=base_time - timedelta(days=30),
            )
            session.add(relation)

            trades = []
            trade_number = 30_000
            for index in range(73):
                mode = index % 4
                if mode == 0:
                    offer_user, responder = customer, outsider
                elif mode == 1:
                    offer_user, responder = owner, customer
                elif mode == 2:
                    offer_user, responder = customer, third
                else:
                    offer_user, responder = third, customer
                trades.append(
                    Trade(
                        trade_number=trade_number,
                        offer_user_id=offer_user.id,
                        responder_user_id=responder.id,
                        actor_user_id=responder.id,
                        commodity_id=coin.id if index % 5 else gold.id,
                        trade_type=TradeType.BUY if index % 2 == 0 else TradeType.SELL,
                        settlement_type=(
                            SettlementType.CASH if index % 3 else SettlementType.TOMORROW
                        ),
                        quantity=10,
                        price=100_000 + index,
                        status=TradeStatus.COMPLETED,
                        created_at=base_time - timedelta(minutes=index // 4),
                        archived=False,
                    )
                )
                trade_number += 1

            for index in range(55):
                trades.append(
                    Trade(
                        trade_number=trade_number,
                        offer_user_id=owner.id if index % 2 else third.id,
                        responder_user_id=third.id if index % 2 else owner.id,
                        actor_user_id=third.id if index % 2 else owner.id,
                        commodity_id=coin.id,
                        trade_type=TradeType.BUY if index % 2 == 0 else TradeType.SELL,
                        settlement_type=SettlementType.CASH,
                        quantity=5,
                        price=200_000 + index,
                        status=TradeStatus.COMPLETED,
                        created_at=base_time - timedelta(hours=2, minutes=index // 3),
                        archived=False,
                    )
                )
                trade_number += 1

            session.add_all(trades)
            await session.commit()
            return owner, customer, outsider, third, coin, gold, trades

    async def _read_pages(
        self,
        *,
        context,
        scope: str,
        target_user_id: int | None,
        limit: int,
        settlement_type: str | None = None,
        perspective_trade_type: str | None = None,
        commodity_id: int | None = None,
    ) -> list[Trade]:
        signature = _trade_history_filter_signature(
            scope=scope,
            viewer_owner_user_id=context.owner_user.id,
            target_user_id=target_user_id,
            from_date=None,
            to_date=None,
            commodity_id=commodity_id,
            commodity_query=None,
            settlement_type=settlement_type,
            perspective_trade_type=perspective_trade_type,
        )
        cursor = None
        output = []
        while True:
            if scope == "my":
                query = _build_my_trades_query(
                    context.owner_user.id,
                    from_date=None,
                    to_date=None,
                    commodity_id=commodity_id,
                    commodity_query=None,
                    settlement_type=settlement_type,
                    perspective_trade_type=perspective_trade_type,
                )
            else:
                async with self.Session() as relation_session:
                    query, _ = await _build_trades_with_user_query(
                        relation_session,
                        other_user_id=target_user_id,
                        context=context,
                        from_date=None,
                        to_date=None,
                        commodity_id=commodity_id,
                        commodity_query=None,
                        settlement_type=settlement_type,
                        perspective_trade_type=perspective_trade_type,
                    )
            position = (
                _decode_trade_history_cursor(cursor, expected_filter_signature=signature)
                if cursor
                else None
            )
            query = _build_trade_history_page_query(
                query,
                cursor_position=position,
                limit=limit,
            )
            async with self.Session() as session:
                rows = list((await session.execute(query)).scalars().all())
            has_more = len(rows) > limit
            page = rows[:limit]
            output.extend(page)
            cursor = (
                _encode_trade_history_cursor(page[-1], filter_signature=signature)
                if has_more and page
                else None
            )
            if not has_more:
                return output

    async def test_personal_history_over_50_is_accessible_once_in_stable_order(self):
        owner, _, _, _, _, _, trades = await self._seed_history()
        rows = await self._read_pages(
            context=self._context(owner),
            scope="my",
            target_user_id=None,
            limit=50,
        )
        expected = sorted(
            [
                trade for trade in trades
                if trade.offer_user_id == owner.id or trade.responder_user_id == owner.id
            ],
            key=lambda trade: (trade.created_at, trade.id),
            reverse=True,
        )
        self.assertGreater(len(rows), 50)
        self.assertEqual([trade.id for trade in rows], [trade.id for trade in expected])
        self.assertEqual(len({trade.id for trade in rows}), len(rows))

    async def test_owner_sees_full_customer_history_but_unrelated_user_sees_only_mutual_rows(self):
        owner, customer, outsider, _, _, _, trades = await self._seed_history()
        owner_rows = await self._read_pages(
            context=self._context(owner),
            scope="with",
            target_user_id=customer.id,
            limit=20,
        )
        outsider_rows = await self._read_pages(
            context=self._context(outsider),
            scope="with",
            target_user_id=customer.id,
            limit=7,
        )
        customer_trade_ids = {
            trade.id for trade in trades
            if trade.offer_user_id == customer.id or trade.responder_user_id == customer.id
        }
        mutual_ids = {
            trade.id for trade in trades
            if {trade.offer_user_id, trade.responder_user_id} == {customer.id, outsider.id}
        }
        self.assertEqual({trade.id for trade in owner_rows}, customer_trade_ids)
        self.assertEqual({trade.id for trade in outsider_rows}, mutual_ids)
        self.assertGreater(len(owner_rows), 20)
        self.assertTrue(mutual_ids)
        self.assertTrue(mutual_ids < customer_trade_ids)

    async def test_settlement_commodity_and_relative_trade_type_filters_match_every_row(self):
        owner, customer, _, _, coin, _, _ = await self._seed_history()
        rows = await self._read_pages(
            context=self._context(owner),
            scope="with",
            target_user_id=customer.id,
            limit=9,
            settlement_type="cash",
            perspective_trade_type="sell",
            commodity_id=coin.id,
        )
        self.assertTrue(rows)
        for trade in rows:
            self.assertEqual(trade.settlement_type, SettlementType.CASH)
            self.assertEqual(trade.commodity_id, coin.id)
            if trade.responder_user_id == customer.id:
                self.assertEqual(trade.trade_type, TradeType.SELL)
            else:
                self.assertEqual(trade.offer_user_id, customer.id)
                self.assertEqual(trade.trade_type, TradeType.BUY)

    async def test_new_trade_between_pages_appears_only_in_a_fresh_traversal(self):
        owner, _, _, third, coin, _, trades = await self._seed_history()
        context = self._context(owner)
        signature = _trade_history_filter_signature(
            scope="my",
            viewer_owner_user_id=owner.id,
            target_user_id=None,
            from_date=None,
            to_date=None,
            commodity_id=None,
            commodity_query=None,
            settlement_type=None,
            perspective_trade_type=None,
        )
        query = _build_trade_history_page_query(
            _build_my_trades_query(
                owner.id,
                from_date=None,
                to_date=None,
                commodity_id=None,
                commodity_query=None,
            ),
            cursor_position=None,
            limit=20,
        )
        async with self.Session() as session:
            rows = list((await session.execute(query)).scalars().all())
        first_page = rows[:20]
        cursor = _encode_trade_history_cursor(first_page[-1], filter_signature=signature)

        newest_created_at = max(trade.created_at for trade in trades) + timedelta(minutes=1)
        async with self.Session() as session:
            newest = Trade(
                trade_number=99_999,
                offer_user_id=owner.id,
                responder_user_id=third.id,
                actor_user_id=third.id,
                commodity_id=coin.id,
                trade_type=TradeType.BUY,
                settlement_type=SettlementType.CASH,
                quantity=1,
                price=999_999,
                status=TradeStatus.COMPLETED,
                created_at=newest_created_at,
                archived=False,
            )
            session.add(newest)
            await session.commit()
            newest_id = newest.id

        continued = []
        has_more = True
        while has_more:
            position = _decode_trade_history_cursor(
                cursor,
                expected_filter_signature=signature,
            )
            next_query = _build_trade_history_page_query(
                _build_my_trades_query(
                    owner.id,
                    from_date=None,
                    to_date=None,
                    commodity_id=None,
                    commodity_query=None,
                ),
                cursor_position=position,
                limit=20,
            )
            async with self.Session() as session:
                next_rows = list((await session.execute(next_query)).scalars().all())
            has_more = len(next_rows) > 20
            page = next_rows[:20]
            continued.extend(page)
            cursor = (
                _encode_trade_history_cursor(page[-1], filter_signature=signature)
                if has_more and page
                else None
            )

        traversed_ids = [trade.id for trade in [*first_page, *continued]]
        expected_original_ids = {
            trade.id for trade in trades
            if trade.offer_user_id == owner.id or trade.responder_user_id == owner.id
        }
        self.assertEqual(set(traversed_ids), expected_original_ids)
        self.assertEqual(len(traversed_ids), len(set(traversed_ids)))
        self.assertNotIn(newest_id, traversed_ids)

        fresh_rows = await self._read_pages(
            context=context,
            scope="my",
            target_user_id=None,
            limit=20,
        )
        self.assertEqual(fresh_rows[0].id, newest_id)


if __name__ == "__main__":
    unittest.main()
