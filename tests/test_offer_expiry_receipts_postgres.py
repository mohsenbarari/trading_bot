import asyncio
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routers.offers import InternalOfferExpireRequest, _expire_offer_internal_with_receipt
from core import offer_publication_worker as publication_worker
from core.events import setup_event_listeners
from core.offer_expiry_contracts import build_offer_expiry_command_identity
from core.services.telegram_offer_channel_service import OfferChannelStateApplyResult
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.offer_expiry_command_receipt import OfferExpiryCommandReceipt
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)
from models.user import User, UserRole


DATABASE_NAME_PATTERN = re.compile(r"^market_stage11_[a-z0-9_]+_test$")


def _stage11_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("MARKET_STAGE11_TEST_DATABASE_URL", "")).strip()
    database_name = str(os.getenv("MARKET_STAGE11_TEST_DATABASE_NAME", "")).strip()
    if not explicit and not database_name:
        return None
    runtime_url = explicit or str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_url:
        raise RuntimeError(
            "DATABASE_URL or SYNC_DATABASE_URL is required with MARKET_STAGE11_TEST_DATABASE_NAME"
        )
    target = make_url(runtime_url)
    if database_name:
        target = target.set(database=database_name)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Stage 11 PostgreSQL tests require a market_stage11_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _stage11_database_urls()


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


def _payload(
    *,
    offer_id: int,
    offer_public_id: str,
    owner_user_id: int,
    expire_reason: str = "cancel_all",
) -> InternalOfferExpireRequest:
    values = {
        "offer_public_id": offer_public_id,
        "owner_user_id": owner_user_id,
        "actor_user_id": owner_user_id,
        "source_surface": "webapp",
        "source_server": "foreign",
        "expire_reason": expire_reason,
    }
    identity = build_offer_expiry_command_identity(**values)
    return InternalOfferExpireRequest(
        offer_id=offer_id,
        command_id=identity.command_id,
        idempotency_key=identity.idempotency_key,
        **values,
    )


class OfferExpiryReceiptDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {
                "MARKET_STAGE11_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot",
                "MARKET_STAGE11_TEST_DATABASE_NAME": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "market_stage11_\\*_test"):
                _stage11_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set MARKET_STAGE11_TEST_DATABASE_NAME or MARKET_STAGE11_TEST_DATABASE_URL",
)
class OfferExpiryReceiptsPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    async def asyncSetUp(self):
        _, async_url = DATABASE_URLS
        self.async_url = async_url
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
                    "TRUNCATE TABLE change_log, offer_expiry_command_receipts, offers, commodities, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_offer(self) -> tuple[int, int, str]:
        suffix = uuid4().hex[:10]
        public_id = f"ofr_stage11_{suffix}_123456"
        async with self.Session() as session:
            user = User(
                account_name=f"stage11_{suffix}",
                mobile_number=f"09{int(suffix, 16) % 1_000_000_000:09d}",
                telegram_id=3_000_000_000 + int(suffix, 16) % 1_000_000_000,
                full_name="Stage 11 owner",
                address="Stage 11 isolated scratch database",
                role=UserRole.STANDARD,
                home_server="iran",
                must_change_password=False,
            )
            commodity = Commodity(name=f"stage11_{suffix}")
            session.add_all([user, commodity])
            await session.flush()
            offer = Offer(
                offer_public_id=public_id,
                user_id=user.id,
                home_server="iran",
                offer_type=OfferType.BUY,
                commodity_id=commodity.id,
                quantity=10,
                remaining_quantity=10,
                price=100_000,
                status=OfferStatus.ACTIVE,
                is_wholesale=True,
                archived=False,
            )
            session.add(offer)
            await session.commit()
            return user.id, offer.id, public_id

    async def _execute(self, session, payload, side_effects):
        with patch("api.routers.offers.current_server", return_value="iran"), patch(
            "core.services.offer_expiry_service.current_server",
            return_value="iran",
        ), patch(
            "api.routers.offers._expire_offer_side_effects",
            new=side_effects,
        ):
            return await _expire_offer_internal_with_receipt(
                payload,
                target_server="iran",
                db=session,
            )

    async def test_two_concurrent_replays_mutate_and_dispatch_side_effect_once(self):
        user_id, _home_offer_id, public_id = await self._seed_offer()
        setup_event_listeners()
        payload = _payload(
            offer_id=987654,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        side_effects = AsyncMock()

        async with self.Session() as first_session, self.Session() as second_session:
            first, second = await asyncio.gather(
                self._execute(first_session, payload, side_effects),
                self._execute(second_session, payload, side_effects),
            )

        self.assertEqual({first["replayed"], second["replayed"]}, {False, True})
        self.assertEqual(side_effects.await_count, 1)
        async with self.Session() as session:
            offer = (
                await session.execute(select(Offer).where(Offer.offer_public_id == public_id))
            ).scalar_one()
            receipt_count = await session.scalar(select(func.count(OfferExpiryCommandReceipt.id)))
            offer_update_count = await session.scalar(
                text(
                    "SELECT count(*) FROM change_log "
                    "WHERE table_name = 'offers' AND operation = 'UPDATE'"
                )
            )
            receipt_outbox_count = await session.scalar(
                text(
                    "SELECT count(*) FROM change_log "
                    "WHERE table_name = 'offer_expiry_command_receipts'"
                )
            )
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(receipt_count, 1)
        self.assertEqual(offer_update_count, 1)
        self.assertEqual(receipt_outbox_count, 0)

    async def test_replay_survives_engine_restart_and_ignores_numeric_id_drift(self):
        user_id, _home_offer_id, public_id = await self._seed_offer()
        first_payload = _payload(
            offer_id=111,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        side_effects = AsyncMock()
        async with self.Session() as session:
            first = await self._execute(session, first_payload, side_effects)
        self.assertFalse(first["replayed"])

        await self.engine.dispose()
        restarted_engine = create_async_engine(self.async_url, pool_pre_ping=True)
        RestartedSession = async_sessionmaker(
            restarted_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        replay_payload = _payload(
            offer_id=999999,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        try:
            async with RestartedSession() as session:
                replay = await self._execute(session, replay_payload, side_effects)
        finally:
            await restarted_engine.dispose()

        self.assertTrue(replay["replayed"])
        self.assertEqual(first["command_id"], replay["command_id"])
        self.assertEqual(side_effects.await_count, 1)

    async def test_commit_failure_rolls_back_offer_and_receipt_together(self):
        user_id, _home_offer_id, public_id = await self._seed_offer()
        payload = _payload(
            offer_id=222,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        side_effects = AsyncMock()
        async with self.Session() as session:
            with patch.object(
                session,
                "commit",
                new=AsyncMock(side_effect=RuntimeError("forced commit failure")),
            ):
                with self.assertRaisesRegex(RuntimeError, "forced commit failure"):
                    await self._execute(session, payload, side_effects)

        async with self.Session() as session:
            offer = (
                await session.execute(select(Offer).where(Offer.offer_public_id == public_id))
            ).scalar_one()
            receipt_count = await session.scalar(select(func.count(OfferExpiryCommandReceipt.id)))
        self.assertEqual(offer.status, OfferStatus.ACTIVE)
        self.assertEqual(receipt_count, 0)
        side_effects.assert_not_awaited()

    async def test_post_commit_side_effect_failure_is_replay_safe(self):
        user_id, _home_offer_id, public_id = await self._seed_offer()
        payload = _payload(
            offer_id=333,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        failing_side_effect = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        async with self.Session() as session:
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                await self._execute(session, payload, failing_side_effect)

        async with self.Session() as session:
            offer = (
                await session.execute(select(Offer).where(Offer.offer_public_id == public_id))
            ).scalar_one()
            receipt = (
                await session.execute(
                    select(OfferExpiryCommandReceipt).where(
                        OfferExpiryCommandReceipt.offer_public_id == public_id
                    )
                )
            ).scalar_one()
        self.assertEqual(offer.status, OfferStatus.EXPIRED)
        self.assertEqual(receipt.outcome_code, "expired")
        self.assertIsNotNone(receipt.completed_at)

        replay_side_effect = AsyncMock()
        async with self.Session() as session:
            replay = await self._execute(session, payload, replay_side_effect)
        self.assertTrue(replay["replayed"])
        replay_side_effect.assert_not_awaited()

    async def test_failed_channel_side_effect_is_repaired_by_existing_reconciliation_worker(self):
        user_id, _home_offer_id, public_id = await self._seed_offer()
        async with self.Session() as session:
            offer = (
                await session.execute(select(Offer).where(Offer.offer_public_id == public_id))
            ).scalar_one()
            offer.channel_message_id = 880011
            await session.flush()
            session.add(
                OfferPublicationState(
                    offer_id=offer.id,
                    offer_public_id=public_id,
                    offer_home_server="iran",
                    surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
                    publication_owner_server="foreign",
                    status=OfferPublicationStatus.SENT,
                    dedupe_key=f"offer-publication:telegram_channel:{public_id}",
                    telegram_message_id=offer.channel_message_id,
                    offer_version_id=offer.version_id,
                    last_known_offer_status=OfferStatus.ACTIVE.value,
                    archived=False,
                )
            )
            await session.commit()

        payload = _payload(
            offer_id=666,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        async with self.Session() as session:
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                await self._execute(
                    session,
                    payload,
                    AsyncMock(side_effect=RuntimeError("provider unavailable")),
                )

        provider_edit = AsyncMock(
            return_value=OfferChannelStateApplyResult(
                ok=True,
                response_class="2xx",
                reason="ok",
            )
        )
        with patch(
            "core.offer_publication_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.offer_publication_worker.assert_background_job_authority",
        ), patch(
            "core.offer_publication_worker.apply_offer_channel_state_with_result",
            new=provider_edit,
        ), patch(
            "core.offer_publication_worker._channel_edit_spacing_seconds",
            return_value=0,
        ):
            report = await publication_worker.run_offer_channel_state_cycle(limit=1)

        self.assertEqual(report.applied, 1)
        provider_edit.assert_awaited_once()
        async with self.Session() as session:
            offer = (
                await session.execute(select(Offer).where(Offer.offer_public_id == public_id))
            ).scalar_one()
            state = (
                await session.execute(
                    select(OfferPublicationState).where(
                        OfferPublicationState.offer_public_id == public_id
                    )
                )
            ).scalar_one()
        self.assertEqual(state.offer_version_id, offer.version_id)
        self.assertEqual(state.last_known_offer_status, OfferStatus.EXPIRED.value)

    async def test_independent_command_on_inactive_offer_is_rejected_without_new_receipt(self):
        user_id, _home_offer_id, public_id = await self._seed_offer()
        original = _payload(
            offer_id=444,
            offer_public_id=public_id,
            owner_user_id=user_id,
        )
        async with self.Session() as session:
            await self._execute(session, original, AsyncMock())

        independent = _payload(
            offer_id=555,
            offer_public_id=public_id,
            owner_user_id=user_id,
            expire_reason="manual",
        )
        async with self.Session() as session:
            with self.assertRaises(HTTPException) as exc_info:
                await self._execute(session, independent, AsyncMock())
        self.assertEqual(exc_info.exception.status_code, 400)

        async with self.Session() as session:
            receipt_count = await session.scalar(select(func.count(OfferExpiryCommandReceipt.id)))
        self.assertEqual(receipt_count, 1)


if __name__ == "__main__":
    unittest.main()
