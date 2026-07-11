import asyncio
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.registration_contracts import TelegramRegistrationOutcome
from core.enums import UserAccountStatus
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services import authoritative_registration_service as registration
from core.services.accountant_relation_service import cancel_pending_accountant_relation
from core.services.customer_relation_service import (
    cancel_pending_customer_relation,
    sweep_expired_pending_customer_relations,
)
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
from api.routers.invitations import delete_pending_invitation
from api.routers import auth as auth_router
from api.routers import invitations as invitation_router
from core.services.registration_notification_service import (
    publish_project_user_joined_web_notifications,
)
from core.utils import check_user_limits
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.session import UserSession

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


class _RegistrationProofRedis:
    def __init__(self, values: dict[str, str]):
        self.values = dict(values)
        self.deleted_keys: list[str] = []

    async def get(self, key: str):
        return self.values.get(key)

    async def delete(self, key: str):
        self.deleted_keys.append(key)
        self.values.pop(key, None)


def _registration_http_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/auth/register-complete",
            "raw_path": b"/api/auth/register-complete",
            "query_string": b"",
            "headers": [(b"user-agent", b"Stage2 PostgreSQL integration")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


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

    async def _seed_relation_invitation(
        self,
        label: str,
        *,
        kind: InvitationKind,
        expired: bool = False,
    ) -> tuple[Invitation, object, User]:
        suffix = uuid4().hex
        owner = User(
            account_name=f"stage2_owner_{label}_{suffix[:10]}",
            mobile_number=f"097{int(suffix[:8], 16) % 100000000:08d}",
            full_name="Stage 2 relation owner",
            address="Stage 2 relation owner address",
            role=UserRole.SUPER_ADMIN,
            home_server="iran",
            must_change_password=False,
        )
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + (
            timedelta(minutes=-1) if expired else timedelta(hours=1)
        )
        invitation = Invitation(
            account_name=f"stage2_{label}_{suffix[:12]}",
            mobile_number=f"096{int(suffix[8:16], 16) % 100000000:08d}",
            token=(
                f"{'ACCT' if kind == InvitationKind.ACCOUNTANT else 'CUST'}-"
                f"stage2-{label}-{suffix}"
            ),
            short_code=suffix[:8],
            role=(UserRole.WATCH if kind == InvitationKind.ACCOUNTANT else UserRole.STANDARD),
            kind=kind,
            is_used=False,
            expires_at=expires_at,
        )
        async with self.session_factory() as session:
            session.add(owner)
            await session.flush()
            invitation.created_by_id = owner.id
            session.add(invitation)
            await session.flush()
            if kind == InvitationKind.ACCOUNTANT:
                relation: object = AccountantRelation(
                    owner_user_id=owner.id,
                    created_by_user_id=owner.id,
                    invitation_token=invitation.token,
                    global_account_name=invitation.account_name,
                    relation_display_name=f"Accountant {label}",
                    mobile_number=invitation.mobile_number,
                    status=AccountantRelationStatus.PENDING,
                    expires_at=expires_at,
                )
            else:
                relation = CustomerRelation(
                    owner_user_id=owner.id,
                    created_by_user_id=owner.id,
                    invitation_token=invitation.token,
                    management_name=f"Customer {label}",
                    customer_tier=CustomerTier.TIER_1,
                    status=CustomerRelationStatus.PENDING,
                    expires_at=expires_at,
                )
            session.add(relation)
            await reserve_invitation_identity(
                session,
                invitation=invitation,
                identity=normalize_invitation_identity(
                    mobile_number=invitation.mobile_number,
                    account_name=invitation.account_name,
                ),
            )
            await session.commit()
        return invitation, relation, owner

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

    async def test_canonical_identity_lookup_and_unique_indexes_cover_legacy_variants(self):
        invitation = await self._seed_invitation("canonical_identity")
        persian_mobile = invitation.mobile_number.translate(
            str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        )
        legacy_user = User(
            account_name=f"  {invitation.account_name.upper()}  ",
            mobile_number=f" {persian_mobile} ",
            full_name="Canonical legacy user",
            address="Canonical legacy user address",
            role=UserRole.STANDARD,
            home_server="iran",
            must_change_password=False,
        )
        async with self.session_factory() as session:
            session.add(legacy_user)
            await session.commit()

        async with self.session_factory() as session:
            with self.assertRaises(registration.AuthoritativeRegistrationError) as exc_info:
                await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address="Canonical registration attempt address",
                    ),
                )
        self.assertEqual(
            exc_info.exception.outcome,
            TelegramRegistrationOutcome.MOBILE_CONFLICT,
        )

        async with self.session_factory() as session:
            canonical_count = int(
                (
                    await session.execute(
                        select(func.count(User.id)).where(
                            User.normalized_mobile_number == invitation.mobile_number,
                            User.normalized_account_name == invitation.account_name,
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(canonical_count, 1)
            with self.assertRaises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        User(
                            account_name=invitation.account_name,
                            mobile_number=f"098{invitation.id % 100000000:08d}",
                            full_name="Canonical duplicate",
                            address="Canonical duplicate address",
                            role=UserRole.STANDARD,
                            home_server="iran",
                            must_change_password=False,
                        )
                    )
                    await session.flush()

    async def test_canonical_identity_rejects_arabic_split_and_deleted_variants(self):
        arabic_digits = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

        whitespace_invitation = await self._seed_invitation("canonical_whitespace")
        whitespace_suffix = uuid4().hex
        async with self.session_factory() as session:
            session.add(
                User(
                    account_name=f"\t{whitespace_invitation.account_name.upper()}\u00a0",
                    mobile_number=f"095{int(whitespace_suffix[:8], 16) % 100000000:08d}",
                    full_name="Whitespace canonical user",
                    address="Whitespace canonical address",
                    role=UserRole.STANDARD,
                    home_server="iran",
                    must_change_password=False,
                )
            )
            await session.commit()
        async with self.session_factory() as session:
            with self.assertRaises(registration.AuthoritativeRegistrationError) as exc_info:
                await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=whitespace_invitation.token,
                        address="Whitespace collision attempt address",
                    ),
                )
        self.assertEqual(
            exc_info.exception.outcome,
            TelegramRegistrationOutcome.ACCOUNT_NAME_CONFLICT,
        )

        arabic_invitation = await self._seed_invitation("canonical_arabic")
        async with self.session_factory() as session:
            session.add(
                User(
                    account_name=f" {arabic_invitation.account_name.upper().translate(arabic_digits)} ",
                    mobile_number=f" {arabic_invitation.mobile_number.translate(arabic_digits)} ",
                    full_name="Arabic canonical user",
                    address="Arabic canonical address",
                    role=UserRole.STANDARD,
                    home_server="iran",
                    must_change_password=False,
                )
            )
            await session.commit()
        async with self.session_factory() as session:
            with self.assertRaises(registration.AuthoritativeRegistrationError) as exc_info:
                await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=arabic_invitation.token,
                        address="Arabic collision attempt address",
                    ),
                )
        self.assertEqual(exc_info.exception.outcome, TelegramRegistrationOutcome.MOBILE_CONFLICT)

        split_invitation = await self._seed_invitation("canonical_split")
        split_suffix = uuid4().hex
        async with self.session_factory() as session:
            session.add_all(
                [
                    User(
                        account_name=f"split_mobile_{split_suffix[:10]}",
                        mobile_number=split_invitation.mobile_number,
                        full_name="Split mobile user",
                        address="Split mobile address",
                        role=UserRole.STANDARD,
                        home_server="iran",
                        must_change_password=False,
                    ),
                    User(
                        account_name=f" {split_invitation.account_name.upper()} ",
                        mobile_number=f"093{int(split_suffix[:8], 16) % 100000000:08d}",
                        full_name="Split account user",
                        address="Split account address",
                        role=UserRole.STANDARD,
                        home_server="iran",
                        must_change_password=False,
                    ),
                ]
            )
            await session.commit()
        async with self.session_factory() as session:
            with self.assertRaises(registration.AuthoritativeRegistrationError) as exc_info:
                await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=split_invitation.token,
                        address="Split collision attempt address",
                    ),
                )
        self.assertEqual(exc_info.exception.outcome, TelegramRegistrationOutcome.MOBILE_CONFLICT)

        deleted_invitation = await self._seed_invitation("canonical_deleted")
        async with self.session_factory() as session:
            session.add(
                User(
                    account_name=deleted_invitation.account_name,
                    mobile_number=deleted_invitation.mobile_number,
                    full_name="Deleted canonical user",
                    address="Deleted canonical address",
                    role=UserRole.STANDARD,
                    home_server="iran",
                    must_change_password=False,
                    is_deleted=True,
                    deleted_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            await session.commit()
        async with self.session_factory() as session:
            with self.assertRaises(registration.AuthoritativeRegistrationError) as exc_info:
                await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=deleted_invitation.token,
                        address="Deleted collision attempt address",
                    ),
                )
        self.assertEqual(exc_info.exception.outcome, TelegramRegistrationOutcome.ACCOUNT_DELETED)

    async def test_unique_constraint_conflicts_persist_one_terminal_receipt_and_replay(self):
        cases = (
            ("mobile", TelegramRegistrationOutcome.MOBILE_CONFLICT),
            ("account", TelegramRegistrationOutcome.ACCOUNT_NAME_CONFLICT),
            ("telegram", TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED),
        )
        for case_name, expected_outcome in cases:
            with self.subTest(case=case_name):
                invitation = await self._seed_invitation(f"unique_{case_name}")
                command = _telegram_command(
                    invitation,
                    telegram_id=2_100_000_000 + invitation.id,
                )
                inserted = False

                async def insert_conflicting_user(name):
                    nonlocal inserted
                    if name != "after_natural_key_reads" or inserted:
                        return
                    inserted = True
                    suffix = uuid4().hex
                    account_name = f"external_{case_name}_{suffix[:10]}"
                    mobile_number = f"095{int(suffix[:8], 16) % 100000000:08d}"
                    telegram_id = None
                    if case_name == "mobile":
                        mobile_number = f" {invitation.mobile_number.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))} "
                    elif case_name == "account":
                        account_name = f" {invitation.account_name.upper()} "
                    else:
                        telegram_id = command.telegram_id
                    async with self.session_factory() as competing_session:
                        competing_session.add(
                            User(
                                account_name=account_name,
                                mobile_number=mobile_number,
                                telegram_id=telegram_id,
                                full_name=f"Unique conflict {case_name}",
                                address="Unique conflict external address",
                                role=UserRole.STANDARD,
                                home_server="iran",
                                must_change_password=False,
                            )
                        )
                        await competing_session.commit()

                async with self.session_factory() as session:
                    result = await registration.complete_invitation_registration(
                        session,
                        registration.AuthoritativeRegistrationRequest.for_telegram(
                            command=command,
                            source_server=SERVER_FOREIGN,
                            received_at=command.local_completed_at,
                        ),
                        checkpoint=insert_conflicting_user,
                    )
                self.assertEqual(result.outcome, expected_outcome)
                self.assertTrue(result.first_terminal_transition)
                self.assertIsNone(result.authoritative_user_id)

                async with self.session_factory() as session:
                    stored_invitation = (
                        await session.execute(
                            select(Invitation).where(Invitation.id == invitation.id)
                        )
                    ).scalar_one()
                    self.assertFalse(stored_invitation.is_used)
                    self.assertIsNone(stored_invitation.registered_user_id)
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
                    exact_target_count = int(
                        (
                            await session.execute(
                                select(func.count(User.id)).where(
                                    User.normalized_account_name == invitation.account_name,
                                    User.normalized_mobile_number == invitation.mobile_number,
                                )
                            )
                        ).scalar_one()
                    )
                    self.assertEqual(exact_target_count, 0)

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
                self.assertEqual(replay.outcome, expected_outcome)

                changed_command = command.model_copy(
                    update={"address": f"Changed replay address for {case_name}"}
                )
                async with self.session_factory() as session:
                    changed = await registration.complete_invitation_registration(
                        session,
                        registration.AuthoritativeRegistrationRequest.for_telegram(
                            command=changed_command,
                            source_server=SERVER_FOREIGN,
                            received_at=changed_command.local_completed_at,
                        ),
                    )
                self.assertEqual(
                    changed.outcome,
                    TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY,
                )

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
        self.assertEqual(outcomes[1].outcome, TelegramRegistrationOutcome.CREATED)
        self.assertTrue(outcomes[1].replayed)
        self.assertFalse(outcomes[1].first_terminal_transition)
        await self._assert_single_completed_user(invitation)

    async def test_direct_revoke_and_registration_have_deterministic_winners(self):
        admin = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)

        completion_wins = await self._seed_invitation("revoke_completion_wins")
        registration_locked = asyncio.Event()
        release_registration = asyncio.Event()
        revoke_started = asyncio.Event()
        original_route_lock = invitation_router.lock_invitation_for_transition

        async def completion_checkpoint(name):
            if name == "after_invitation_lock":
                registration_locked.set()
                await release_registration.wait()

        async def observed_route_lock(*args, **kwargs):
            revoke_started.set()
            return await original_route_lock(*args, **kwargs)

        async def run_registration(invitation, checkpoint=None, *, address):
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address=address,
                    ),
                    checkpoint=checkpoint,
                )

        async def run_revoke(invitation):
            async with self.session_factory() as session:
                return await delete_pending_invitation(
                    invitation.id,
                    db=session,
                    admin=admin,
                )

        with patch.object(
            invitation_router,
            "lock_invitation_for_transition",
            new=observed_route_lock,
        ):
            registration_task = asyncio.create_task(
                run_registration(
                    completion_wins,
                    completion_checkpoint,
                    address="Direct revoke completion winner address",
                )
            )
            await registration_locked.wait()
            revoke_task = asyncio.create_task(run_revoke(completion_wins))
            await revoke_started.wait()
            self.assertFalse(revoke_task.done())
            release_registration.set()
            registration_result, revoke_result = await asyncio.gather(
                registration_task,
                revoke_task,
                return_exceptions=True,
            )

        self.assertEqual(registration_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertIsInstance(revoke_result, HTTPException)
        self.assertEqual(revoke_result.status_code, 400)
        await self._assert_single_completed_user(completion_wins)

        revoke_wins = await self._seed_invitation("revoke_wins")
        revoke_locked = asyncio.Event()
        release_revoke = asyncio.Event()
        registration_started = asyncio.Event()

        async def held_route_lock(*args, **kwargs):
            invitation = await original_route_lock(*args, **kwargs)
            revoke_locked.set()
            await release_revoke.wait()
            return invitation

        async def registration_start_checkpoint(name):
            if name == "before_invitation_lock":
                registration_started.set()

        with patch.object(
            invitation_router,
            "lock_invitation_for_transition",
            new=held_route_lock,
        ):
            revoke_task = asyncio.create_task(run_revoke(revoke_wins))
            await revoke_locked.wait()
            registration_task = asyncio.create_task(
                run_registration(
                    revoke_wins,
                    registration_start_checkpoint,
                    address="Direct revoke winner address",
                )
            )
            await registration_started.wait()
            self.assertFalse(registration_task.done())
            release_revoke.set()
            revoke_result, registration_result = await asyncio.gather(
                revoke_task,
                registration_task,
                return_exceptions=True,
            )

        self.assertIsNone(revoke_result)
        self.assertIsInstance(
            registration_result,
            registration.AuthoritativeRegistrationError,
        )
        self.assertEqual(
            registration_result.outcome,
            TelegramRegistrationOutcome.INVITATION_REVOKED,
        )
        async with self.session_factory() as session:
            stored = (
                await session.execute(
                    select(Invitation).where(Invitation.id == revoke_wins.id)
                )
            ).scalar_one()
            self.assertFalse(stored.is_used)
            self.assertIsNotNone(stored.revoked_at)
            user_count = int(
                (
                    await session.execute(
                        select(func.count(User.id)).where(
                            User.normalized_mobile_number == revoke_wins.mobile_number
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(user_count, 0)

    async def test_relation_cancel_and_registration_have_deterministic_winners(self):
        customer_invitation, customer_relation, customer_owner = (
            await self._seed_relation_invitation(
                "customer_completion_wins",
                kind=InvitationKind.CUSTOMER,
            )
        )
        registration_locked = asyncio.Event()
        release_registration = asyncio.Event()
        cancel_started = asyncio.Event()
        from core.services import customer_relation_service as customer_service

        original_customer_lock = customer_service.lock_invitation_for_transition

        async def completion_checkpoint(name):
            if name == "after_invitation_lock":
                registration_locked.set()
                await release_registration.wait()

        async def observed_customer_lock(*args, **kwargs):
            cancel_started.set()
            return await original_customer_lock(*args, **kwargs)

        async def run_customer_registration():
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=customer_invitation.token,
                        address="Customer completion winner address",
                    ),
                    checkpoint=completion_checkpoint,
                )

        async def run_customer_cancel():
            async with self.session_factory() as session:
                return await cancel_pending_customer_relation(
                    session,
                    owner_user_id=customer_owner.id,
                    relation_id=customer_relation.id,
                )

        with patch.object(
            customer_service,
            "lock_invitation_for_transition",
            new=observed_customer_lock,
        ):
            registration_task = asyncio.create_task(run_customer_registration())
            await registration_locked.wait()
            cancel_task = asyncio.create_task(run_customer_cancel())
            await cancel_started.wait()
            self.assertFalse(cancel_task.done())
            release_registration.set()
            registration_result, cancel_result = await asyncio.gather(
                registration_task,
                cancel_task,
                return_exceptions=True,
            )

        self.assertEqual(registration_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertIsInstance(cancel_result, HTTPException)
        async with self.session_factory() as session:
            stored_relation = (
                await session.execute(
                    select(CustomerRelation).where(CustomerRelation.id == customer_relation.id)
                )
            ).scalar_one()
            self.assertEqual(stored_relation.status, CustomerRelationStatus.ACTIVE)
            self.assertIsNotNone(stored_relation.customer_user_id)

        accountant_invitation, accountant_relation, accountant_owner = (
            await self._seed_relation_invitation(
                "accountant_cancel_wins",
                kind=InvitationKind.ACCOUNTANT,
            )
        )
        from core.services import accountant_relation_service as accountant_service

        original_accountant_lock = accountant_service.lock_invitation_for_transition
        cancel_locked = asyncio.Event()
        release_cancel = asyncio.Event()
        registration_started = asyncio.Event()

        async def held_accountant_lock(*args, **kwargs):
            invitation = await original_accountant_lock(*args, **kwargs)
            cancel_locked.set()
            await release_cancel.wait()
            return invitation

        async def run_accountant_cancel():
            async with self.session_factory() as session:
                return await cancel_pending_accountant_relation(
                    session,
                    owner_user_id=accountant_owner.id,
                    relation_id=accountant_relation.id,
                )

        async def run_accountant_registration():
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=accountant_invitation.token,
                        address="Accountant cancel winner address",
                    ),
                    checkpoint=lambda name: self._set_event_on_checkpoint(
                        name,
                        "before_invitation_lock",
                        registration_started,
                    ),
                )

        with patch.object(
            accountant_service,
            "lock_invitation_for_transition",
            new=held_accountant_lock,
        ):
            cancel_task = asyncio.create_task(run_accountant_cancel())
            await cancel_locked.wait()
            registration_task = asyncio.create_task(run_accountant_registration())
            await registration_started.wait()
            self.assertFalse(registration_task.done())
            release_cancel.set()
            cancel_result, registration_result = await asyncio.gather(
                cancel_task,
                registration_task,
                return_exceptions=True,
            )

        self.assertEqual(cancel_result.status, AccountantRelationStatus.REVOKED)
        self.assertIsInstance(
            registration_result,
            registration.AuthoritativeRegistrationError,
        )
        self.assertEqual(
            registration_result.outcome,
            TelegramRegistrationOutcome.INVITATION_REVOKED,
        )

    async def test_expiry_sweep_and_registration_have_deterministic_winners(self):
        from core.services import customer_relation_service as customer_service

        original_customer_lock = customer_service.lock_invitation_for_transition

        async def run_registration(invitation, checkpoint=None, *, address):
            received_at = invitation.expires_at.replace(tzinfo=timezone.utc) - timedelta(seconds=1)
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address=address,
                        received_at=received_at,
                    ),
                    checkpoint=checkpoint,
                )

        async def run_sweep(invitation):
            async with self.session_factory() as session:
                rows = await sweep_expired_pending_customer_relations(
                    session,
                    invitation_token=invitation.token,
                )
                await session.commit()
                return rows

        completion_invitation, completion_relation, _ = await self._seed_relation_invitation(
            "expiry_completion_wins",
            kind=InvitationKind.CUSTOMER,
            expired=True,
        )
        registration_locked = asyncio.Event()
        release_registration = asyncio.Event()
        sweep_started = asyncio.Event()

        async def registration_checkpoint(name):
            if name == "after_invitation_lock":
                registration_locked.set()
                await release_registration.wait()

        async def observed_sweep_lock(*args, **kwargs):
            sweep_started.set()
            return await original_customer_lock(*args, **kwargs)

        with patch.object(
            customer_service,
            "lock_invitation_for_transition",
            new=observed_sweep_lock,
        ):
            registration_task = asyncio.create_task(
                run_registration(
                    completion_invitation,
                    registration_checkpoint,
                    address="Expiry completion winner address",
                )
            )
            await registration_locked.wait()
            sweep_task = asyncio.create_task(run_sweep(completion_invitation))
            await sweep_started.wait()
            self.assertFalse(sweep_task.done())
            release_registration.set()
            registration_result, sweep_result = await asyncio.gather(
                registration_task,
                sweep_task,
            )

        self.assertEqual(registration_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(sweep_result, [])
        async with self.session_factory() as session:
            stored_relation = (
                await session.execute(
                    select(CustomerRelation).where(CustomerRelation.id == completion_relation.id)
                )
            ).scalar_one()
            self.assertEqual(stored_relation.status, CustomerRelationStatus.ACTIVE)

        sweep_invitation, sweep_relation, _ = await self._seed_relation_invitation(
            "expiry_sweep_wins",
            kind=InvitationKind.CUSTOMER,
            expired=True,
        )
        sweep_locked = asyncio.Event()
        release_sweep = asyncio.Event()
        registration_started = asyncio.Event()

        async def held_sweep_lock(*args, **kwargs):
            invitation = await original_customer_lock(*args, **kwargs)
            sweep_locked.set()
            await release_sweep.wait()
            return invitation

        async def registration_start(name):
            if name == "before_invitation_lock":
                registration_started.set()

        with patch.object(
            customer_service,
            "lock_invitation_for_transition",
            new=held_sweep_lock,
        ):
            sweep_task = asyncio.create_task(run_sweep(sweep_invitation))
            await sweep_locked.wait()
            registration_task = asyncio.create_task(
                run_registration(
                    sweep_invitation,
                    registration_start,
                    address="Expiry sweep winner address",
                )
            )
            await registration_started.wait()
            self.assertFalse(registration_task.done())
            release_sweep.set()
            sweep_result, registration_result = await asyncio.gather(
                sweep_task,
                registration_task,
                return_exceptions=True,
            )

        self.assertEqual(len(sweep_result), 1)
        self.assertIsInstance(
            registration_result,
            registration.AuthoritativeRegistrationError,
        )
        self.assertEqual(
            registration_result.outcome,
            TelegramRegistrationOutcome.INVALID_RELATION,
        )
        async with self.session_factory() as session:
            stored_relation = (
                await session.execute(
                    select(CustomerRelation).where(CustomerRelation.id == sweep_relation.id)
                )
            ).scalar_one()
            self.assertEqual(stored_relation.status, CustomerRelationStatus.EXPIRED)
            user_count = int(
                (
                    await session.execute(
                        select(func.count(User.id)).where(
                            User.normalized_mobile_number == sweep_invitation.mobile_number
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(user_count, 0)

    async def _set_event_on_checkpoint(self, name, target, event):
        if name == target:
            event.set()

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

    async def test_delayed_telegram_link_rechecks_current_user_and_relation_truth(self):
        scenarios = (
            ("watch", TelegramRegistrationOutcome.INVALID_RELATION),
            ("accountant", TelegramRegistrationOutcome.INVALID_RELATION),
            ("tier2", TelegramRegistrationOutcome.INVALID_RELATION),
            ("inactive", TelegramRegistrationOutcome.ACCOUNT_INACTIVE),
            ("deleted", TelegramRegistrationOutcome.ACCOUNT_DELETED),
        )
        for scenario, expected_outcome in scenarios:
            with self.subTest(scenario=scenario):
                invitation = await self._seed_invitation(f"current_{scenario}")
                address = f"Current projection {scenario} address"
                async with self.session_factory() as session:
                    web_result = await registration.complete_invitation_registration(
                        session,
                        registration.AuthoritativeRegistrationRequest.for_web(
                            invitation_token=invitation.token,
                            address=address,
                        ),
                    )
                user_id = int(web_result.authoritative_user_id)

                async with self.session_factory() as session:
                    user = await session.get(User, user_id)
                    if scenario == "watch":
                        user.role = UserRole.WATCH
                    elif scenario == "inactive":
                        user.account_status = UserAccountStatus.INACTIVE
                    elif scenario == "deleted":
                        user.is_deleted = True
                        user.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    elif scenario in {"accountant", "tier2"}:
                        owner = User(
                            account_name=f"projection_owner_{scenario}_{uuid4().hex[:8]}",
                            mobile_number=f"094{uuid4().int % 100000000:08d}",
                            full_name="Projection owner",
                            address="Projection owner address",
                            role=UserRole.SUPER_ADMIN,
                            home_server="iran",
                            must_change_password=False,
                        )
                        session.add(owner)
                        await session.flush()
                        if scenario == "accountant":
                            session.add(
                                AccountantRelation(
                                    owner_user_id=owner.id,
                                    accountant_user_id=user.id,
                                    created_by_user_id=owner.id,
                                    invitation_token=f"ACCT-current-{uuid4().hex}",
                                    global_account_name=user.account_name,
                                    relation_display_name="Current accountant",
                                    mobile_number=user.mobile_number,
                                    status=AccountantRelationStatus.ACTIVE,
                                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                                    activated_at=datetime.now(timezone.utc),
                                )
                            )
                        else:
                            session.add(
                                CustomerRelation(
                                    owner_user_id=owner.id,
                                    customer_user_id=user.id,
                                    created_by_user_id=owner.id,
                                    invitation_token=f"CUST-current-{uuid4().hex}",
                                    management_name="Current tier two customer",
                                    customer_tier=CustomerTier.TIER_2,
                                    status=CustomerRelationStatus.ACTIVE,
                                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                                    activated_at=datetime.now(timezone.utc),
                                )
                            )
                    await session.commit()

                command = _telegram_command(
                    invitation,
                    telegram_id=2_300_000_000 + invitation.id,
                )
                async with self.session_factory() as session:
                    telegram_result = await registration.complete_invitation_registration(
                        session,
                        registration.AuthoritativeRegistrationRequest.for_telegram(
                            command=command,
                            source_server=SERVER_FOREIGN,
                            received_at=command.local_completed_at,
                        ),
                    )
                self.assertEqual(telegram_result.outcome, expected_outcome)
                async with self.session_factory() as session:
                    stored_user = await session.get(User, user_id)
                    self.assertIsNone(stored_user.telegram_id)

    async def test_two_live_sessions_same_command_create_one_receipt_and_one_outbox_event(self):
        invitation = await self._seed_invitation("same_command")
        await self._seed_telegram_recipient("same_command")
        command = _telegram_command(
            invitation,
            telegram_id=2_200_000_000 + invitation.id,
        )
        first_locked = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        original_prepare = registration.prepare_registration_command_receipt

        async def prepare_receipt(db, *, command, source_server):
            if asyncio.current_task().get_name() == "stage2-same-command-second":
                second_started.set()
            return await original_prepare(
                db,
                command=command,
                source_server=source_server,
            )

        async def first_checkpoint(name):
            if name == "after_invitation_lock":
                first_locked.set()
                await release_first.wait()

        async def run_command(checkpoint=None):
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_telegram(
                        command=command,
                        source_server=SERVER_FOREIGN,
                        received_at=command.local_completed_at,
                    ),
                    checkpoint=checkpoint,
                )

        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=prepare_receipt,
        ):
            first = asyncio.create_task(
                run_command(first_checkpoint),
                name="stage2-same-command-first",
            )
            await first_locked.wait()
            second = asyncio.create_task(
                run_command(),
                name="stage2-same-command-second",
            )
            await second_started.wait()
            self.assertFalse(second.done())
            release_first.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(first_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(second_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertTrue(second_result.replayed)
        user = await self._assert_single_completed_user(invitation)
        async with self.session_factory() as session:
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
            outbox_rows = list(
                (
                    await session.execute(
                        select(
                            TelegramNotificationOutbox.recipient_user_id,
                            TelegramNotificationOutbox.dedupe_key,
                        ).where(
                            TelegramNotificationOutbox.source_type == "project_user_joined",
                            TelegramNotificationOutbox.source_id == str(user.id),
                        )
                    )
                ).all()
            )
            self.assertTrue(outbox_rows)
            self.assertEqual(len(outbox_rows), len({row.dedupe_key for row in outbox_rows}))
            self.assertEqual(
                len(outbox_rows),
                len({row.recipient_user_id for row in outbox_rows}),
            )

    async def test_two_different_commands_for_one_invitation_have_one_stable_winner(self):
        invitation = await self._seed_invitation("different_commands")
        await self._seed_telegram_recipient("different_commands")
        first_command = _telegram_command(
            invitation,
            telegram_id=2_240_000_000 + invitation.id,
        )
        second_command = _telegram_command(
            invitation,
            telegram_id=2_250_000_000 + invitation.id,
        )
        first_locked = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        original_loader = registration._load_invitation_for_update

        async def observed_loader(db, token):
            if asyncio.current_task().get_name() == "stage2-different-command-second":
                second_started.set()
            return await original_loader(db, token)

        async def first_checkpoint(name):
            if name == "after_invitation_lock":
                first_locked.set()
                await release_first.wait()

        async def run_command(command, checkpoint=None):
            async with self.session_factory() as session:
                return await registration.complete_invitation_registration(
                    session,
                    registration.AuthoritativeRegistrationRequest.for_telegram(
                        command=command,
                        source_server=SERVER_FOREIGN,
                        received_at=command.local_completed_at,
                    ),
                    checkpoint=checkpoint,
                )

        with patch.object(registration, "_load_invitation_for_update", new=observed_loader):
            first = asyncio.create_task(
                run_command(first_command, first_checkpoint),
                name="stage2-different-command-first",
            )
            await first_locked.wait()
            second = asyncio.create_task(
                run_command(second_command),
                name="stage2-different-command-second",
            )
            await second_started.wait()
            self.assertFalse(second.done())
            release_first.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(first_result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(
            second_result.outcome,
            TelegramRegistrationOutcome.TELEGRAM_ACCOUNT_CONFLICT,
        )
        user = await self._assert_single_completed_user(invitation)
        self.assertEqual(user.telegram_id, first_command.telegram_id)
        async with self.session_factory() as session:
            receipt_count = int(
                (
                    await session.execute(
                        select(func.count(TelegramRegistrationCommandReceipt.id)).where(
                            TelegramRegistrationCommandReceipt.command_id.in_(
                                [first_command.command_id, second_command.command_id]
                            )
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(receipt_count, 2)

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

    async def test_web_notification_failure_cannot_poison_adapter_session(self):
        new_user = await self._seed_telegram_recipient("notification_source")
        await self._seed_telegram_recipient("notification_recipient")
        failure_injected = False

        async def poison_notification_session(notification_db, *_args, **_kwargs):
            nonlocal failure_injected
            if failure_injected:
                return None
            failure_injected = True
            await notification_db.execute(text("SELECT * FROM stage2_missing_notification_table"))

        async with self.session_factory() as adapter_session:
            adapter_user = await adapter_session.get(User, new_user.id)
            with patch(
                "core.services.registration_notification_service.create_user_notification",
                new=poison_notification_session,
            ):
                await publish_project_user_joined_web_notifications(
                    new_user_id=adapter_user.id,
                    account_name=adapter_user.account_name,
                    full_name=adapter_user.full_name,
                    session_factory=self.session_factory,
                )

            marker = datetime.now(timezone.utc).replace(tzinfo=None)
            adapter_user.last_seen_at = marker
            await adapter_session.commit()

        async with self.session_factory() as verification_session:
            stored = await verification_session.get(User, new_user.id)
            self.assertEqual(stored.last_seen_at, marker)

    async def test_real_web_adapter_issues_session_after_dedicated_notification_failure(self):
        invitation = await self._seed_invitation("real_adapter_notification")
        await self._seed_telegram_recipient("real_adapter_recipient")
        verify_key = f"reg_verified:{invitation.token}"
        redis = _RegistrationProofRedis({verify_key: "1"})
        failure_injected = False

        async def get_test_redis():
            return redis

        async def publish_with_scratch_session(**kwargs):
            return await publish_project_user_joined_web_notifications(
                **kwargs,
                session_factory=self.session_factory,
            )

        async def poison_notification_session(notification_db, *_args, **_kwargs):
            nonlocal failure_injected
            if failure_injected:
                return None
            failure_injected = True
            await notification_db.execute(
                text("SELECT * FROM stage2_missing_adapter_notification_table")
            )

        address = "Real adapter notification failure address"
        async with self.session_factory() as adapter_session:
            with patch.object(auth_router, "get_redis", new=get_test_redis), patch.object(
                auth_router,
                "publish_project_user_joined_web_notifications",
                new=publish_with_scratch_session,
            ), patch(
                "core.services.registration_notification_service.create_user_notification",
                new=poison_notification_session,
            ), patch.object(
                auth_router,
                "create_refresh_token",
                return_value="stage2-real-adapter-refresh",
            ), patch.object(
                auth_router,
                "create_access_token",
                return_value="stage2-real-adapter-access",
            ):
                response = await auth_router.register_complete(
                    auth_router.RegisterComplete(
                        token=invitation.token,
                        address=address,
                    ),
                    raw_request=_registration_http_request(),
                    db=adapter_session,
                )

        self.assertTrue(failure_injected)
        self.assertEqual(response["access_token"], "stage2-real-adapter-access")
        self.assertNotIn(verify_key, redis.values)
        self.assertIn(verify_key, redis.deleted_keys)

        async with self.session_factory() as verification_session:
            stored_invitation = (
                await verification_session.execute(
                    select(Invitation).where(Invitation.id == invitation.id)
                )
            ).scalar_one()
            self.assertTrue(stored_invitation.is_used)
            user_id = int(stored_invitation.registered_user_id)
            session_count = int(
                (
                    await verification_session.execute(
                        select(func.count(UserSession.id)).where(
                            UserSession.user_id == user_id,
                            UserSession.is_active.is_(True),
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(session_count, 1)

    async def test_real_web_adapter_retry_after_session_failure_reuses_committed_user(self):
        invitation = await self._seed_invitation("real_adapter_retry")
        verify_key = f"reg_verified:{invitation.token}"
        redis = _RegistrationProofRedis({verify_key: "1"})
        address = "Real adapter retry address"

        async def get_test_redis():
            return redis

        async def skip_notifications(**_kwargs):
            return None

        async def fail_session_commit(*_args, **_kwargs):
            raise RuntimeError("injected post-registration session failure")

        with patch.object(auth_router, "get_redis", new=get_test_redis), patch.object(
            auth_router,
            "publish_project_user_joined_web_notifications",
            new=skip_notifications,
        ), patch.object(
            auth_router,
            "handle_login_session",
            new=fail_session_commit,
        ), patch.object(
            auth_router,
            "create_refresh_token",
            return_value="stage2-failed-session-refresh",
        ):
            async with self.session_factory() as first_adapter_session:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected post-registration session failure",
                ):
                    await auth_router.register_complete(
                        auth_router.RegisterComplete(
                            token=invitation.token,
                            address=address,
                        ),
                        raw_request=_registration_http_request(),
                        db=first_adapter_session,
                    )

        self.assertIn(verify_key, redis.values)
        self.assertNotIn(verify_key, redis.deleted_keys)

        with patch.object(auth_router, "get_redis", new=get_test_redis), patch.object(
            auth_router,
            "publish_project_user_joined_web_notifications",
            new=skip_notifications,
        ), patch.object(
            auth_router,
            "create_refresh_token",
            return_value="stage2-retry-refresh",
        ), patch.object(
            auth_router,
            "create_access_token",
            return_value="stage2-retry-access",
        ):
            async with self.session_factory() as retry_adapter_session:
                response = await auth_router.register_complete(
                    auth_router.RegisterComplete(
                        token=invitation.token,
                        address=address,
                    ),
                    raw_request=_registration_http_request(),
                    db=retry_adapter_session,
                )

        self.assertEqual(response["access_token"], "stage2-retry-access")
        self.assertNotIn(verify_key, redis.values)
        async with self.session_factory() as verification_session:
            stored_invitation = (
                await verification_session.execute(
                    select(Invitation).where(Invitation.id == invitation.id)
                )
            ).scalar_one()
            user_id = int(stored_invitation.registered_user_id)
            user_count = int(
                (
                    await verification_session.execute(
                        select(func.count(User.id)).where(
                            User.normalized_mobile_number == invitation.mobile_number
                        )
                    )
                ).scalar_one()
            )
            session_count = int(
                (
                    await verification_session.execute(
                        select(func.count(UserSession.id)).where(
                            UserSession.user_id == user_id,
                            UserSession.is_active.is_(True),
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(user_count, 1)
            self.assertEqual(session_count, 1)

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

        reset_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)

        def payload(event_id, *, kind, epoch, deltas, occurred_at):
            return {
                "_counter_event_id": str(event_id),
                "_counter_event_kind": kind,
                "_counter_epoch": epoch,
                "_counter_deltas": deltas,
                "_counter_occurred_at": occurred_at.isoformat(),
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
                    occurred_at=reset_at - timedelta(minutes=5),
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
                    occurred_at=reset_at - timedelta(minutes=5),
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
                    occurred_at=reset_at - timedelta(minutes=5),
                ),
                source_server=SERVER_FOREIGN,
            )
            self.assertEqual(conflicting_replay, "error")

            # A post-reset increment may arrive before the reset event. The
            # later reset rebuilds the period from the local receipt ledger.
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
                        epoch=1,
                        deltas={"channel_messages_count": 2},
                        occurred_at=reset_at + timedelta(minutes=1),
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        reset_id,
                        kind="reset",
                        epoch=2,
                        deltas={},
                        occurred_at=reset_at,
                    ),
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
                        occurred_at=reset_at - timedelta(seconds=1),
                    ),
                    source_server=SERVER_FOREIGN,
                ),
                "ignored",
            )
            disconnected_post_reset_id = uuid4()
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        disconnected_post_reset_id,
                        kind="increment",
                        epoch=1,
                        deltas={"trades_count": 4},
                        occurred_at=reset_at + timedelta(minutes=2),
                    ),
                    source_server=SERVER_FOREIGN,
                ),
                "ok",
            )
            await session.commit()

        async with self.session_factory() as session:
            restart_replay = await _apply_user_counter_event(
                session,
                record_id=user.id,
                data=payload(
                    disconnected_post_reset_id,
                    kind="increment",
                    epoch=1,
                    deltas={"trades_count": 4},
                    occurred_at=reset_at + timedelta(minutes=2),
                ),
                source_server=SERVER_FOREIGN,
            )
            self.assertEqual(restart_replay, "ignored")
            await session.commit()

        async with self.session_factory() as session:
            stored = (
                await session.execute(select(User).where(User.id == user.id))
            ).scalar_one()
            self.assertEqual(stored.counter_epoch, 2)
            self.assertEqual(stored.trades_count, 4)
            self.assertEqual(stored.commodities_traded_count, 0)
            self.assertEqual(stored.channel_messages_count, 2)
            stored.limitations_expire_at = datetime.utcnow() + timedelta(days=1)
            stored.max_daily_trades = 4
            allowed, message = check_user_limits(stored, "trade")
            self.assertFalse(allowed)
            self.assertIn("حداکثر تعداد معاملات", message)
            receipt_count = int(
                (
                    await session.execute(
                        select(func.count(UserCounterEventReceipt.event_id)).where(
                            UserCounterEventReceipt.event_id.in_(
                                [
                                    increment_id,
                                    post_reset_id,
                                    reset_id,
                                    old_epoch_id,
                                    disconnected_post_reset_id,
                                ]
                            )
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(receipt_count, 5)
            excluded_receipt = await session.get(UserCounterEventReceipt, old_epoch_id)
            self.assertEqual(excluded_receipt.outcome, "excluded_pre_boundary")

    async def test_counter_multiple_resets_require_monotonic_sequential_boundaries(self):
        user = await self._seed_telegram_recipient("counter_multi_reset")
        reset_two_at = datetime.now(timezone.utc) - timedelta(hours=3)
        reset_three_at = reset_two_at + timedelta(hours=1)

        def payload(event_id, *, kind, epoch, deltas, occurred_at):
            return {
                "_counter_event_id": str(event_id),
                "_counter_event_kind": kind,
                "_counter_epoch": epoch,
                "_counter_deltas": deltas,
                "_counter_occurred_at": occurred_at.isoformat(),
                "_sync_identity": {
                    "current": {
                        "account_name": user.account_name,
                        "mobile_number": user.mobile_number,
                        "telegram_id": user.telegram_id,
                    },
                    "previous": {},
                },
            }

        accepted_ids = [uuid4() for _ in range(6)]
        invalid_ids = [uuid4() for _ in range(4)]
        async with self.session_factory() as session:
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        accepted_ids[0],
                        kind="increment",
                        epoch=2,
                        deltas={"trades_count": 2},
                        occurred_at=reset_two_at + timedelta(minutes=10),
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        accepted_ids[1],
                        kind="reset",
                        epoch=2,
                        deltas={},
                        occurred_at=reset_two_at,
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            # Equality is explicitly inclusive for increments.
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        accepted_ids[2],
                        kind="increment",
                        epoch=1,
                        deltas={"channel_messages_count": 1},
                        occurred_at=reset_two_at,
                    ),
                    source_server=SERVER_FOREIGN,
                ),
                "ok",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        accepted_ids[3],
                        kind="increment",
                        epoch=2,
                        deltas={"trades_count": 3},
                        occurred_at=reset_two_at + timedelta(minutes=20),
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )

            invalid_resets = (
                (invalid_ids[0], 3, reset_two_at - timedelta(seconds=1), "error"),
                (invalid_ids[1], 3, reset_two_at, "error"),
                (invalid_ids[2], 4, reset_three_at, "deferred"),
                (invalid_ids[3], 2, reset_two_at + timedelta(minutes=30), "error"),
            )
            for event_id, epoch, occurred_at, expected in invalid_resets:
                with self.subTest(epoch=epoch, occurred_at=occurred_at, expected=expected):
                    result = await _apply_user_counter_event(
                        session,
                        record_id=user.id,
                        data=payload(
                            event_id,
                            kind="reset",
                            epoch=epoch,
                            deltas={},
                            occurred_at=occurred_at,
                        ),
                        source_server=SERVER_IRAN,
                    )
                    self.assertEqual(result, expected)

            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        accepted_ids[4],
                        kind="increment",
                        epoch=3,
                        deltas={"commodities_traded_count": 4},
                        occurred_at=reset_three_at + timedelta(minutes=5),
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            self.assertEqual(
                await _apply_user_counter_event(
                    session,
                    record_id=user.id,
                    data=payload(
                        accepted_ids[5],
                        kind="reset",
                        epoch=3,
                        deltas={},
                        occurred_at=reset_three_at,
                    ),
                    source_server=SERVER_IRAN,
                ),
                "ok",
            )
            await session.commit()

        async with self.session_factory() as session:
            stored = await session.get(User, user.id)
            self.assertEqual(stored.counter_epoch, 3)
            self.assertEqual(stored.trades_count, 0)
            self.assertEqual(stored.channel_messages_count, 0)
            self.assertEqual(stored.commodities_traded_count, 4)
            invalid_receipts = int(
                (
                    await session.execute(
                        select(func.count(UserCounterEventReceipt.event_id)).where(
                            UserCounterEventReceipt.event_id.in_(invalid_ids)
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(invalid_receipts, 0)
            reset_receipts = list(
                (
                    await session.execute(
                        select(UserCounterEventReceipt).where(
                            UserCounterEventReceipt.user_id == user.id,
                            UserCounterEventReceipt.event_kind == "reset",
                        )
                    )
                ).scalars().all()
            )
            self.assertEqual(
                {(row.event_epoch, row.outcome) for row in reset_receipts},
                {(2, "applied"), (3, "applied")},
            )

    async def test_counter_nonterminal_error_leaves_no_receipt_and_retry_can_apply(self):
        user = await self._seed_telegram_recipient("counter_retry_after_repair")
        boundary = datetime.now(timezone.utc) - timedelta(hours=1)
        increment_at = boundary + timedelta(minutes=5)
        increment_id = uuid4()

        def increment_payload():
            return {
                "_counter_event_id": str(increment_id),
                "_counter_event_kind": "increment",
                "_counter_epoch": 2,
                "_counter_deltas": {"trades_count": 1},
                "_counter_occurred_at": increment_at.isoformat(),
                "_sync_identity": {
                    "current": {
                        "account_name": user.account_name,
                        "mobile_number": user.mobile_number,
                        "telegram_id": user.telegram_id,
                    },
                    "previous": {},
                },
            }

        async with self.session_factory() as session:
            stored = await session.get(User, user.id)
            stored.counter_epoch = 2
            await session.commit()

        async with self.session_factory() as session:
            first = await _apply_user_counter_event(
                session,
                record_id=user.id,
                data=increment_payload(),
                source_server=SERVER_FOREIGN,
            )
            self.assertEqual(first, "error")
            await session.commit()
            receipt = await session.get(UserCounterEventReceipt, increment_id)
            self.assertIsNone(receipt)

        async with self.session_factory() as session:
            session.add(
                UserCounterEventReceipt(
                    event_id=uuid4(),
                    source_server=SERVER_IRAN,
                    user_id=user.id,
                    event_hash="a" * 64,
                    event_kind="reset",
                    event_epoch=2,
                    occurred_at=boundary,
                    deltas={},
                    outcome="applied",
                )
            )
            await session.commit()

        async with self.session_factory() as session:
            retry = await _apply_user_counter_event(
                session,
                record_id=user.id,
                data=increment_payload(),
                source_server=SERVER_FOREIGN,
            )
            self.assertEqual(retry, "ok")
            await session.commit()

        async with self.session_factory() as session:
            stored = await session.get(User, user.id)
            self.assertEqual(stored.trades_count, 1)
            receipt = await session.get(UserCounterEventReceipt, increment_id)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.outcome, "applied")


if __name__ == "__main__":
    unittest.main()
