import os
import re
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import SettlementType
from core.services.trade_service import _get_comparable_active_prices, detect_offer_price_warning
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage8_[a-z0-9_]+_test$")


def _stage8_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE8_TEST_DATABASE_URL", "")).strip()
    if not explicit:
        return None
    target = make_url(explicit)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 8 PostgreSQL tests require a market_stage8_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage8_database_urls()


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


class TradePriceSettlementDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {"MARKET_STAGE8_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage8_\\*_test"):
                _stage8_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE8_TEST_DATABASE_URL",
)
class TradePriceSettlementPostgresTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_price_comparison_and_warning_are_isolated_by_settlement_type(self):
        suffix = uuid4().hex[:10]
        async with self.Session() as session:
            owner = User(
                account_name=f"stage8_owner_{suffix}",
                mobile_number=f"091{int(suffix, 16) % 100_000_000:08d}",
                telegram_id=3_080_000_000 + int(suffix, 16) % 100_000_000,
                full_name="Stage 8 owner",
                address="Stage 8 scratch database",
                role=UserRole.STANDARD,
                home_server="foreign",
                must_change_password=False,
            )
            commodity = Commodity(name=f"stage8_coin_{suffix}")
            other_commodity = Commodity(name=f"stage8_other_{suffix}")
            session.add_all([owner, commodity, other_commodity])
            await session.flush()
            session.add_all([
                Offer(
                    offer_public_id=f"ofr_stage8_cash_{suffix}",
                    user_id=owner.id,
                    home_server="foreign",
                    offer_type=OfferType.SELL,
                    settlement_type=SettlementType.CASH,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=10,
                    price=100_000,
                    status=OfferStatus.ACTIVE,
                    is_wholesale=True,
                    archived=False,
                ),
                Offer(
                    offer_public_id=f"ofr_stage8_tomorrow_{suffix}",
                    user_id=owner.id,
                    home_server="foreign",
                    offer_type=OfferType.SELL,
                    settlement_type=SettlementType.TOMORROW,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=10,
                    price=900_000,
                    status=OfferStatus.ACTIVE,
                    is_wholesale=True,
                    archived=False,
                ),
                Offer(
                    offer_public_id=f"ofr_stage8_other_commodity_{suffix}",
                    user_id=owner.id,
                    home_server="foreign",
                    offer_type=OfferType.SELL,
                    settlement_type=SettlementType.CASH,
                    commodity_id=other_commodity.id,
                    quantity=10,
                    remaining_quantity=10,
                    price=10_000,
                    status=OfferStatus.ACTIVE,
                    is_wholesale=True,
                    archived=False,
                ),
                Offer(
                    offer_public_id=f"ofr_stage8_other_direction_{suffix}",
                    user_id=owner.id,
                    home_server="foreign",
                    offer_type=OfferType.BUY,
                    settlement_type=SettlementType.CASH,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=10,
                    price=20_000,
                    status=OfferStatus.ACTIVE,
                    is_wholesale=True,
                    archived=False,
                ),
                Offer(
                    offer_public_id=f"ofr_stage8_inactive_{suffix}",
                    user_id=owner.id,
                    home_server="foreign",
                    offer_type=OfferType.SELL,
                    settlement_type=SettlementType.CASH,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=0,
                    price=30_000,
                    status=OfferStatus.COMPLETED,
                    is_wholesale=True,
                    archived=False,
                ),
                Offer(
                    offer_public_id=f"ofr_stage8_ackwarn_{suffix}",
                    user_id=owner.id,
                    home_server="foreign",
                    offer_type=OfferType.SELL,
                    settlement_type=SettlementType.CASH,
                    commodity_id=commodity.id,
                    quantity=10,
                    remaining_quantity=10,
                    price=40_000,
                    status=OfferStatus.ACTIVE,
                    exclude_from_competitive_price=True,
                    price_warning_type="sell_below_market",
                    is_wholesale=True,
                    archived=False,
                ),
            ])
            await session.commit()

            cash_prices = await _get_comparable_active_prices(
                session,
                "sell",
                SettlementType.CASH,
                commodity.id,
                10,
                user_id=None,
            )
            tomorrow_prices = await _get_comparable_active_prices(
                session,
                "sell",
                SettlementType.TOMORROW,
                commodity.id,
                10,
                user_id=None,
            )
            with patch(
                "core.services.trade_service.get_trading_settings",
                return_value=type("Settings", (), {"offer_price_warning_enabled": True})(),
            ):
                warning = await detect_offer_price_warning(
                    session,
                    "sell",
                    SettlementType.CASH,
                    commodity.id,
                    10,
                    90_000,
                    user_id=None,
                )

        self.assertEqual(cash_prices, [100_000])
        self.assertEqual(tomorrow_prices, [900_000])
        self.assertEqual(warning["reference_price"], 100_000)
        self.assertEqual(warning["comparable_count"], 1)


if __name__ == "__main__":
    unittest.main()
