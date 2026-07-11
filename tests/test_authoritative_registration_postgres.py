import asyncio
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.registration_contracts import TelegramRegistrationOutcome
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services import authoritative_registration_service as registration
from core.services.invitation_identity_reservation_service import (
    normalize_invitation_identity,
    reserve_invitation_identity,
)
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.invitation_identity_reservation import InvitationIdentityReservation
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.telegram_registration_command_receipt import TelegramRegistrationCommandReceipt
from models.user_counter_event_receipt import UserCounterEventReceipt
from models.user import User, UserRole
from api.routers.sync import _apply_user_counter_event

from tests.test_authoritative_registration_service import _telegram_command


STAGE2_DATABASE_NAME_PATTERN = re.compile(r"^stage2_registration_[a-z0-9_]+$")


def _require_stage2_scratch_database(url):
    database_name = str(url.database or "").strip().lower()
    if not STAGE2_DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise RuntimeError(
            "Stage 2 PostgreSQL tests require a stage2_registration_* scratch database"
        )
    return url


def _stage2_database_url() -> str | None:
    explicit = str(os.getenv("STAGE2_TEST_DATABASE_URL", "")).strip()
    if explicit:
        target = _require_stage2_scratch_database(make_url(explicit))
        return target.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        )
    database_name = str(os.getenv("STAGE2_TEST_DATABASE_NAME", "")).strip()
    if not database_name:
        return None
    runtime_database_url = str(
        os.getenv("SYNC_DATABASE_URL", "") or os.getenv("DATABASE_URL", "")
    ).strip()
    if not runtime_database_url:
        raise RuntimeError("SYNC_DATABASE_URL or DATABASE_URL is required with STAGE2_TEST_DATABASE_NAME")
    target = _require_stage2_scratch_database(
        make_url(runtime_database_url).set(
            drivername="postgresql+asyncpg",
            database=database_name,
        )
    )
    return target.render_as_string(hide_password=False)


STAGE2_DATABASE_URL = _stage2_database_url()


class Stage2DatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with self.assertRaisesRegex(RuntimeError, "stage2_registration_\\*"):
            _require_stage2_scratch_database(
                make_url("postgresql+asyncpg:///trading_bot")
            )

    def test_accepts_stage2_scratch_database_name(self):
        target = _require_stage2_scratch_database(
            make_url(
                "postgresql+asyncpg:///stage2_registration_guard_test"
            )
        )
        self.assertEqual(target.database, "stage2_registration_guard_test")


@unittest.skipUnless(
    STAGE2_DATABASE_URL,
    "set STAGE2_TEST_DATABASE_NAME or STAGE2_TEST_DATABASE_URL for PostgreSQL race tests",
)
class AuthoritativeRegistrationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._server_override = override_current_server(SERVER_IRAN)
        self._server_override.__enter__()
        self.engine = create_async_engine(STAGE2_DATABASE_URL, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._server_override.__exit__(None, None, None)

    async def _seed_invitation(self, label: str) -> Invitation:
        suffix = uuid4().hex
        mobile = f"091{int(suffix[:8], 16) % 100000000:08d}"
        invitation = Invitation(
            account_name=f"stage2_{label}_{suffix[:12]}",
            mobile_number=mobile,
            token=f"INV-stage2-{label}-{suffix}",
            short_code=suffix[:8],
            role=UserRole.STANDARD,
            kind=InvitationKind.STANDARD,
            is_used=False,
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        )
        async with self.session_factory() as session:
            session.add(invitation)
            await session.flush()
            await reserve_invitation_identity(
                session,
                invitation=invitation,
                identity=normalize_invitation_identity(
                    mobile_number=invitation.mobile_number,
                    account_name=invitation.account_name,
                ),
            )
            await session.commit()
        return invitation

    async def _seed_telegram_recipient(self, label: str) -> User:
        suffix = uuid4().hex
        user = User(
            account_name=f"stage2_recipient_{label}_{suffix[:10]}",
            mobile_number=f"099{int(suffix[:8], 16) % 100000000:08d}",
            telegram_id=1_000_000_000 + (int(suffix[:8], 16) % 900_000_000),
            username=f"recipient_{suffix[:8]}",
            full_name="Stage 2 Recipient",
            address="Tehran, Stage Two recipient address",
            role=UserRole.STANDARD,
            home_server="iran",
            must_change_password=False,
        )
        async with self.session_factory() as session:
            session.add(user)
            await session.commit()
        return user

    async def _assert_single_completed_user(self, invitation: Invitation) -> User:
        async with self.session_factory() as session:
            users = list(
                (
                    await session.execute(
                        select(User).where(
                            User.mobile_number == invitation.mobile_number,
                            User.account_name == invitation.account_name,
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(users), 1)
            stored_invitation = (
                await session.execute(select(Invitation).where(Invitation.id == invitation.id))
            ).scalar_one()
            self.assertTrue(stored_invitation.is_used)
            self.assertEqual(stored_invitation.registered_user_id, users[0].id)
            reservation_count = int(
                (
                    await session.execute(
                        select(func.count(InvitationIdentityReservation.id)).where(
                            InvitationIdentityReservation.invitation_id == invitation.id
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(reservation_count, 0)
            return users[0]

    async def test_web_web_race_serializes_to_one_user_and_one_completion(self):
        invitation = await self._seed_invitation("web_web")
        original_loader = registration._load_invitation_for_update
        winner_locked = asyncio.Event()
        release_winner = asyncio.Event()
        loser_loader_started = asyncio.Event()

        async def loader(db, token):
            if asyncio.current_task().get_name() == "stage2-web-loser":
                loser_loader_started.set()
            return await original_loader(db, token)

        async def winner_checkpoint(name):
            if name == "after_invitation_lock":
                winner_locked.set()
                await release_winner.wait()

        async def run_web(task_name, checkpoint=None):
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address="Tehran, Stage Two race address",
                    ),
                    checkpoint=checkpoint,
                )

        with patch.object(registration, "_load_invitation_for_update", new=loader):
            winner = asyncio.create_task(
                run_web("stage2-web-winner", winner_checkpoint),
                name="stage2-web-winner",
            )
            await winner_locked.wait()
            loser = asyncio.create_task(run_web("stage2-web-loser"), name="stage2-web-loser")
            await loser_loader_started.wait()
            self.assertFalse(loser.done())
            release_winner.set()
            outcomes = await asyncio.gather(winner, loser, return_exceptions=True)

        self.assertEqual(outcomes[0].outcome, TelegramRegistrationOutcome.CREATED)
        self.assertIsInstance(outcomes[1], registration.AuthoritativeRegistrationError)
        self.assertEqual(
            outcomes[1].outcome,
            TelegramRegistrationOutcome.INVITATION_ALREADY_USED,
        )
        await self._assert_single_completed_user(invitation)

    async def test_web_winner_then_telegram_links_same_user_and_replay_is_stable(self):
        invitation = await self._seed_invitation("web_telegram")
        command = _telegram_command(invitation, telegram_id=2_000_000_000 + invitation.id)
        original_loader = registration._load_invitation_for_update
        web_locked = asyncio.Event()
        release_web = asyncio.Event()
        telegram_loader_started = asyncio.Event()

        async def loader(db, token):
            if asyncio.current_task().get_name() == "stage2-telegram-loser":
                telegram_loader_started.set()
            return await original_loader(db, token)

        async def web_checkpoint(name):
            if name == "after_invitation_lock":
                web_locked.set()
                await release_web.wait()

        async def run_web():
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address="Authoritative Web Race Address",
                    ),
                    checkpoint=web_checkpoint,
                )

        async def run_telegram():
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_telegram(
                        command=command,
                        source_server=SERVER_FOREIGN,
                        received_at=command.local_completed_at,
                    ),
                )

        with patch.object(registration, "_load_invitation_for_update", new=loader):
            web_task = asyncio.create_task(run_web(), name="stage2-web-winner")
            await web_locked.wait()
            telegram_task = asyncio.create_task(run_telegram(), name="stage2-telegram-loser")
            await telegram_loader_started.wait()
            self.assertFalse(telegram_task.done())
            release_web.set()
            web_result, telegram_result = await asyncio.gather(web_task, telegram_task)

        self.assertEqual(web_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(telegram_result.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        user = await self._assert_single_completed_user(invitation)
        self.assertEqual(user.telegram_id, command.telegram_id)
        self.assertEqual(user.address, "Authoritative Web Race Address")
        async with self.session_factory() as session:
            stored_invitation = (
                await session.execute(select(Invitation).where(Invitation.id == invitation.id))
            ).scalar_one()
            self.assertEqual(stored_invitation.completed_via, InvitationCompletionSurface.WEB)
            receipt_count = int(
                (
                    await session.execute(
                        select(func.count(TelegramRegistrationCommandReceipt.id)).where(
                            TelegramRegistrationCommandReceipt.command_id == command.command_id
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(receipt_count, 1)

        async with self.session_factory() as session:
            replay = await registration.complete_invitation_registration(
                session,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )
        self.assertTrue(replay.replayed)
        self.assertFalse(replay.first_terminal_transition)
        self.assertEqual(replay.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)

    async def test_telegram_winner_then_web_rejects_without_second_user(self):
        invitation = await self._seed_invitation("telegram_web")
        command = _telegram_command(invitation, telegram_id=2_000_000_000 + invitation.id)
        original_loader = registration._load_invitation_for_update
        telegram_locked = asyncio.Event()
        release_telegram = asyncio.Event()
        web_loader_started = asyncio.Event()

        async def loader(db, token):
            if asyncio.current_task().get_name() == "stage2-web-loser":
                web_loader_started.set()
            return await original_loader(db, token)

        async def telegram_checkpoint(name):
            if name == "after_invitation_lock":
                telegram_locked.set()
                await release_telegram.wait()

        async def run_telegram():
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_telegram(
                        command=command,
                        source_server=SERVER_FOREIGN,
                        received_at=command.local_completed_at,
                    ),
                    checkpoint=telegram_checkpoint,
                )

        async def run_web():
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address="Tehran, Web loser address",
                    ),
                )

        with patch.object(registration, "_load_invitation_for_update", new=loader):
            telegram_task = asyncio.create_task(run_telegram(), name="stage2-telegram-winner")
            await telegram_locked.wait()
            web_task = asyncio.create_task(run_web(), name="stage2-web-loser")
            await web_loader_started.wait()
            self.assertFalse(web_task.done())
            release_telegram.set()
            outcomes = await asyncio.gather(telegram_task, web_task, return_exceptions=True)

        self.assertEqual(outcomes[0].outcome, TelegramRegistrationOutcome.CREATED)
        self.assertIsInstance(outcomes[1], registration.AuthoritativeRegistrationError)
        self.assertEqual(outcomes[1].outcome, TelegramRegistrationOutcome.INVITATION_ALREADY_USED)
        user = await self._assert_single_completed_user(invitation)
        self.assertEqual(user.telegram_id, command.telegram_id)
        async with self.session_factory() as session:
            stored_invitation = (
                await session.execute(select(Invitation).where(Invitation.id == invitation.id))
            ).scalar_one()
            self.assertEqual(stored_invitation.completed_via, InvitationCompletionSurface.TELEGRAM)

    async def test_failure_after_receipt_and_outbox_flush_rolls_back_every_registration_row(self):
        invitation = await self._seed_invitation("rollback")
        await self._seed_telegram_recipient("rollback")
        command = _telegram_command(invitation, telegram_id=2_000_000_000 + invitation.id)
        async with self.session_factory() as session:
            outbox_before = int(
                (await session.execute(select(func.count(TelegramNotificationOutbox.id)))).scalar_one()
            )

        async def fail_after_durable_rows(name):
            if name == "after_receipt_outbox_insert":
                raise RuntimeError("stage2_injected_rollback")

        async with self.session_factory() as session:
            with self.assertRaisesRegex(RuntimeError, "stage2_injected_rollback"):
                await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_telegram(
                        command=command,
                        source_server=SERVER_FOREIGN,
                        received_at=command.local_completed_at,
                    ),
                    checkpoint=fail_after_durable_rows,
                )

        async with self.session_factory() as session:
            user_count = int(
                (
                    await session.execute(
                        select(func.count(User.id)).where(
                            User.mobile_number == invitation.mobile_number,
                            User.account_name == invitation.account_name,
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(user_count, 0)
            stored_invitation = (
                await session.execute(select(Invitation).where(Invitation.id == invitation.id))
            ).scalar_one()
            self.assertFalse(stored_invitation.is_used)
            self.assertIsNone(stored_invitation.registered_user_id)
            self.assertIsNone(stored_invitation.completed_at)
            self.assertIsNone(stored_invitation.completed_via)
            reservation_count = int(
                (
                    await session.execute(
                        select(func.count(InvitationIdentityReservation.id)).where(
                            InvitationIdentityReservation.invitation_id == invitation.id
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(reservation_count, 1)
            receipt_count = int(
                (
                    await session.execute(
                        select(func.count(TelegramRegistrationCommandReceipt.id)).where(
                            TelegramRegistrationCommandReceipt.command_id == command.command_id
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(receipt_count, 0)
            outbox_after = int(
                (await session.execute(select(func.count(TelegramNotificationOutbox.id)))).scalar_one()
            )
            self.assertEqual(outbox_after, outbox_before)

    async def test_registration_receipt_terminal_constraints_reject_partial_states(self):
        invalid_rows = (
            {
                "outcome_code": None,
                "completed_at": None,
                "authoritative_user_id": 1,
            },
            {
                "outcome_code": TelegramRegistrationOutcome.CREATED.value,
                "completed_at": datetime.now(timezone.utc),
                "authoritative_user_id": None,
            },
            {
                "outcome_code": TelegramRegistrationOutcome.INVALID_COMMAND.value,
                "completed_at": datetime.now(timezone.utc),
                "authoritative_user_id": 1,
            },
            {
                "outcome_code": TelegramRegistrationOutcome.INVALID_COMMAND.value,
                "completed_at": None,
                "authoritative_user_id": None,
            },
        )
        async with self.session_factory() as session:
            for index, state in enumerate(invalid_rows):
                with self.subTest(state=state):
                    with self.assertRaises(IntegrityError):
                        async with session.begin_nested():
                            session.add(
                                TelegramRegistrationCommandReceipt(
                                    command_id=uuid4(),
                                    idempotency_key=f"stage2-receipt-invalid-{index}-{uuid4().hex}",
                                    request_hash="a" * 64,
                                    invitation_token_hash="b" * 64,
                                    source_server=SERVER_FOREIGN,
                                    **state,
                                )
                            )
                            await session.flush()

            valid_pending = TelegramRegistrationCommandReceipt(
                command_id=uuid4(),
                idempotency_key=f"stage2-receipt-pending-{uuid4().hex}",
                request_hash="c" * 64,
                invitation_token_hash="d" * 64,
                source_server=SERVER_FOREIGN,
            )
            valid_failure = TelegramRegistrationCommandReceipt(
                command_id=uuid4(),
                idempotency_key=f"stage2-receipt-failure-{uuid4().hex}",
                request_hash="e" * 64,
                invitation_token_hash="f" * 64,
                source_server=SERVER_FOREIGN,
                outcome_code=TelegramRegistrationOutcome.INVALID_COMMAND.value,
                completed_at=datetime.now(timezone.utc),
            )
            valid_success = TelegramRegistrationCommandReceipt(
                command_id=uuid4(),
                idempotency_key=f"stage2-receipt-success-{uuid4().hex}",
                request_hash="1" * 64,
                invitation_token_hash="2" * 64,
                source_server=SERVER_FOREIGN,
                outcome_code=TelegramRegistrationOutcome.CREATED.value,
                authoritative_user_id=1,
                completed_at=datetime.now(timezone.utc),
            )
            session.add_all([valid_pending, valid_failure, valid_success])
            await session.commit()

    async def test_counter_events_are_exactly_once_and_reset_ordered_by_epoch(self):
        user = await self._seed_telegram_recipient("counter_event")

        def payload(event_id, *, kind, epoch, deltas):
            return {
                "_counter_event_id": str(event_id),
                "_counter_event_kind": kind,
                "_counter_epoch": epoch,
                "_counter_deltas": deltas,
                "_sync_identity": {
                    "current": {
                        "account_name": user.account_name,
                        "mobile_number": user.mobile_number,
                        "telegram_id": user.telegram_id,
                    },
                    "previous": {},
                },
            }

        increment_id = uuid4()
        async with self.session_factory() as session:
            first = await _apply_user_counter_event(
                session,
                record_id=user.id + 10_000,
                data=payload(
                    increment_id,
                    kind="increment",
                    epoch=1,
                    deltas={"trades_count": 1, "commodities_traded_count": 3},
                ),
                source_server=SERVER_FOREIGN,
            )
            duplicate = await _apply_user_counter_event(
                session,
                record_id=user.id + 10_000,
                data=payload(
                    increment_id,
                    kind="increment",
                    epoch=1,
                    deltas={"trades_count": 1, "commodities_traded_count": 3},
                ),
                source_server=SERVER_FOREIGN,
            )
            self.assertEqual((first, duplicate), ("ok", "ignored"))
            conflicting_replay = await _apply_user_counter_event(
                session,
                record_id=user.id + 10_000,
                data=payload(
                    increment_id,
                    kind="increment",
                    epoch=1,
                    deltas={"trades_count": 2, "commodities_traded_count": 3},
                ),
                source_server=SERVER_FOREIGN,
            )
            self.assertEqual(conflicting_replay, "error")

            # A post-reset increment may arrive before the reset event. It
            # advances the epoch, clears prior counts, and remains after reset.
            post_reset_id = uuid4()
            reset_id = uuid4()
            old_epoch_id = uuid4()
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        post_reset_id,
                        kind="increment",
                        epoch=2,
                        deltas={"channel_messages_count": 2},
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(reset_id, kind="reset", epoch=2, deltas={}),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        old_epoch_id,
                        kind="increment",
                        epoch=1,
                        deltas={"trades_count": 8},
                    ),
                    source_server=SERVER_FOREIGN,
                ),
                "ignored",
            )
            await session.commit()

        async with self.session_factory() as session:
            stored = (
                await session.execute(select(User).where(User.id == user.id))
            ).scalar_one()
            self.assertEqual(stored.counter_epoch, 2)
            self.assertEqual(stored.trades_count, 0)
            self.assertEqual(stored.commodities_traded_count, 0)
            self.assertEqual(stored.channel_messages_count, 2)
            receipt_count = int(
                (
                    await session.execute(
                        select(func.count(UserCounterEventReceipt.event_id)).where(
                            UserCounterEventReceipt.event_id.in_(
                                [increment_id, post_reset_id, reset_id, old_epoch_id]
                            )
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(receipt_count, 4)


if __name__ == "__main__":
    unittest.main()
