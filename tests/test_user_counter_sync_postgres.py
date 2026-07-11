import json
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routers.trades import _apply_trade_counter_increment
from core import events
from core.sync_outbox_guard import SyncOutboxError, register_sync_outbox_guards
from core.user_counter_sync import increment_user_counters, reset_user_counters_in_memory
from models.change_log import ChangeLog
from models.commodity import Commodity
from models.trade import Trade, TradeStatus, TradeType
from models.user import User, UserRole
from models.user_counter_event_receipt import UserCounterEventReceipt


COUNTER_DATABASE_NAME_PATTERN = re.compile(r"^stage1_counter_[a-z0-9_]+$")


def _require_counter_scratch_database(url):
    database_name = str(url.database or "").strip().lower()
    if not COUNTER_DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise RuntimeError(
            "User counter PostgreSQL tests require a stage1_counter_* scratch database"
        )
    return url


def _counter_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("STAGE1_COUNTER_TEST_DATABASE_URL", "")).strip()
    if explicit:
        target = _require_counter_scratch_database(make_url(explicit))
        return (
            target.set(drivername="postgresql+psycopg2").render_as_string(
                hide_password=False
            ),
            target.set(drivername="postgresql+asyncpg").render_as_string(
                hide_password=False
            ),
        )
    database_name = str(os.getenv("STAGE1_COUNTER_TEST_DATABASE_NAME", "")).strip()
    if not database_name:
        return None
    runtime_database_url = str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_database_url:
        raise RuntimeError(
            "SYNC_DATABASE_URL or DATABASE_URL is required with "
            "STAGE1_COUNTER_TEST_DATABASE_NAME"
        )
    target = _require_counter_scratch_database(
        make_url(runtime_database_url).set(
            drivername="postgresql+asyncpg",
            database=database_name,
        )
    )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(
            hide_password=False
        ),
        target.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        ),
    )


COUNTER_DATABASE_URLS = _counter_database_urls()


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


def _capture_listeners(registry):
    def listens_for(model, event_name):
        def decorator(func):
            registry.append((model, event_name, func))
            return func

        return decorator

    return listens_for


class _WakeupRedis:
    def __init__(self):
        self.payloads = []

    def rpush(self, queue_name, *payloads):
        self.payloads.extend((queue_name, payload) for payload in payloads)


class UserCounterDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with self.assertRaisesRegex(RuntimeError, "stage1_counter_\\*"):
            _require_counter_scratch_database(
                make_url("postgresql+asyncpg:///trading_bot")
            )

    def test_accepts_counter_scratch_database_name(self):
        target = _require_counter_scratch_database(
            make_url("postgresql+asyncpg:///stage1_counter_guard_test")
        )
        self.assertEqual(target.database, "stage1_counter_guard_test")


@unittest.skipUnless(
    COUNTER_DATABASE_URLS,
    "set STAGE1_COUNTER_TEST_DATABASE_NAME or STAGE1_COUNTER_TEST_DATABASE_URL",
)
class UserCounterPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = COUNTER_DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    @classmethod
    def tearDownClass(cls):
        sync_url, _ = COUNTER_DATABASE_URLS
        _run_alembic(sync_url, "downgrade", "f7c8d9e0a1b2")
        super().tearDownClass()

    async def asyncSetUp(self):
        _, async_url = COUNTER_DATABASE_URLS
        self.engine = create_async_engine(async_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self.listeners = []
        suffix = uuid4().hex
        async with self.session_factory() as session:
            self.counter_user = User(
                account_name=f"counter_only_{suffix[:12]}",
                mobile_number=f"091{int(suffix[:8], 16) % 100000000:08d}",
                telegram_id=1_000_000_000 + int(suffix[:8], 16) % 900_000_000,
                full_name="Counter Only User",
                address="Tehran counter-only address",
                role=UserRole.STANDARD,
                home_server="iran",
                must_change_password=False,
            )
            self.trade_user = User(
                account_name=f"counter_trade_{suffix[12:24]}",
                mobile_number=f"092{int(suffix[8:16], 16) % 100000000:08d}",
                telegram_id=1_900_000_001 + int(suffix[8:16], 16) % 900_000_000,
                full_name="Counter Trade User",
                address="Tehran counter-trade address",
                role=UserRole.STANDARD,
                home_server="iran",
                must_change_password=False,
            )
            self.commodity = Commodity(name=f"counter_commodity_{suffix}")
            session.add_all([self.counter_user, self.trade_user, self.commodity])
            await session.commit()

        captured = []
        with patch("core.events.event.listens_for", side_effect=_capture_listeners(captured)):
            events.setup_user_events()
            events.setup_trade_events()
        for model, event_name, listener in captured:
            event.listen(model, event_name, listener)
            self.listeners.append((model, event_name, listener))
        register_sync_outbox_guards()
        self.wakeup_redis = _WakeupRedis()

    async def asyncTearDown(self):
        for model, event_name, listener in reversed(self.listeners):
            event.remove(model, event_name, listener)
        await self.engine.dispose()

    async def _counter_events_for_user(self, user_id: int) -> list[dict]:
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(ChangeLog)
                        .where(
                            ChangeLog.table_name == "users",
                            ChangeLog.record_id == user_id,
                        )
                        .order_by(ChangeLog.id)
                    )
                )
                .scalars()
                .all()
            )
        events = []
        for row in rows:
            payload = json.loads(row.data) if isinstance(row.data, str) else dict(row.data)
            if payload.get("_sync_contract") == "user_counter_event_v2":
                events.append(payload)
        return events

    async def test_counter_only_and_trade_transactions_have_durable_outbox(self):
        with patch("core.events.settings.registration_sync_v2_enabled", True), patch(
            "core.events.settings.server_mode",
            "iran",
        ), patch(
            "core.sync_outbox_guard._get_sync_wakeup_redis",
            return_value=self.wakeup_redis,
        ):
            async with self.session_factory() as session:
                user = await session.get(User, self.counter_user.id)
                increment_user_counters(user, channel_messages=1)
                await session.commit()

            counter_events = await self._counter_events_for_user(self.counter_user.id)
            self.assertEqual(len(counter_events), 1)
            self.assertEqual(
                counter_events[0]["_counter_deltas"],
                {"channel_messages_count": 1},
            )

            async with self.session_factory() as session:
                user = await session.get(User, self.counter_user.id)
                reset_user_counters_in_memory(user)
                await session.commit()

            counter_events = await self._counter_events_for_user(self.counter_user.id)
            self.assertEqual(len(counter_events), 2)
            self.assertEqual(counter_events[-1]["_counter_event_kind"], "reset")
            self.assertEqual(counter_events[-1]["_counter_deltas"], {})

            async with self.session_factory() as session:
                user = await session.get(User, self.trade_user.id)
                trade = Trade(
                    trade_number=10_000_000 + int(uuid4().hex[:6], 16),
                    offer_user_id=user.id,
                    offer_user_mobile=user.mobile_number,
                    responder_user_id=user.id,
                    responder_user_mobile=user.mobile_number,
                    actor_user_id=user.id,
                    commodity_id=self.commodity.id,
                    trade_type=TradeType.BUY,
                    quantity=3,
                    price=1_000_000,
                    status=TradeStatus.COMPLETED,
                    idempotency_key=uuid4().hex,
                    archived=False,
                )
                session.add(trade)
                _apply_trade_counter_increment(user, quantity=3)
                await session.commit()
                trade_id = trade.id

            trade_counter_events = await self._counter_events_for_user(self.trade_user.id)
            self.assertEqual(len(trade_counter_events), 1)
            self.assertEqual(
                trade_counter_events[0]["_counter_deltas"],
                {"commodities_traded_count": 3, "trades_count": 1},
            )
            async with self.session_factory() as session:
                local_receipts = list(
                    (
                        await session.execute(
                            select(UserCounterEventReceipt).where(
                                UserCounterEventReceipt.user_id.in_(
                                    [self.counter_user.id, self.trade_user.id]
                                )
                            )
                        )
                    ).scalars().all()
                )
                trade_outbox_count = len(
                    list(
                        (
                            await session.execute(
                                select(ChangeLog).where(
                                    ChangeLog.table_name == "trades",
                                    ChangeLog.record_id == trade_id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                )
            self.assertEqual(trade_outbox_count, 1)
            self.assertEqual(len(local_receipts), 3)
            self.assertEqual(
                sorted(row.event_kind for row in local_receipts),
                ["increment", "increment", "reset"],
            )
            self.assertTrue(all(row.occurred_at is not None for row in local_receipts))
            self.assertGreaterEqual(len(self.wakeup_redis.payloads), 4)

            suffix = uuid4().hex
            account_name = f"foreign_forbidden_{suffix[:12]}"
            with patch("core.events.settings.server_mode", "foreign"):
                async with self.session_factory() as session:
                    session.add(
                        User(
                            account_name=account_name,
                            mobile_number=f"090{int(suffix[:8], 16) % 100000000:08d}",
                            full_name="Forbidden foreign User",
                            address="Forbidden foreign User address",
                            role=UserRole.STANDARD,
                            home_server="iran",
                            must_change_password=False,
                        )
                    )
                    with self.assertRaises(SyncOutboxError):
                        await session.commit()
                    await session.rollback()

            async with self.session_factory() as session:
                stored = (
                    await session.execute(
                        select(User).where(User.normalized_account_name == account_name)
                    )
                ).scalar_one_or_none()
                self.assertIsNone(stored)


if __name__ == "__main__":
    unittest.main()
