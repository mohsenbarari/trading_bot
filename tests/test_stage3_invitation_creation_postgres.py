import asyncio
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.server_routing import SERVER_IRAN, override_current_server
from core.services.canonical_invitation_creation_service import (
    CanonicalInvitationCreationError,
    create_or_reuse_canonical_invitation,
)
from core.services.customer_relation_service import create_or_reuse_owner_customer_relation
from core.services.invitation_identity_reservation_service import release_invitation_identity
from core.services.invitation_lifecycle_service import complete_invitation
from core.services.invitation_transition_lock_service import lock_invitation_for_transition
from core.services.user_deletion_service import _soft_revoke_pending_invitation
from models.customer_relation import CustomerRelation, CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.invitation_identity_reservation import InvitationIdentityReservation
from models.user import User, UserRole


STAGE3_DATABASE_NAME_PATTERN = re.compile(r"^stage3_registration_[a-z0-9_]+$")


def _require_stage3_scratch_database(url):
    database_name = str(url.database or "").strip().lower()
    if not STAGE3_DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise RuntimeError("Stage 3 PostgreSQL tests require a stage3_registration_* scratch database")
    return url


def _stage3_database_url() -> str | None:
    explicit = str(os.getenv("STAGE3_TEST_DATABASE_URL", "")).strip()
    if not explicit:
        return None
    target = _require_stage3_scratch_database(make_url(explicit))
    return target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


STAGE3_DATABASE_URL = _stage3_database_url()


class Stage3DatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with self.assertRaisesRegex(RuntimeError, "stage3_registration_\\*"):
            _require_stage3_scratch_database(make_url("postgresql+asyncpg:///trading_bot_db"))


@unittest.skipUnless(
    STAGE3_DATABASE_URL,
    "set STAGE3_TEST_DATABASE_URL for Stage 3 PostgreSQL concurrency tests",
)
class Stage3InvitationCreationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._server_override = override_current_server(SERVER_IRAN)
        self._server_override.__enter__()
        self.engine = create_async_engine(STAGE3_DATABASE_URL, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self._expiry_patcher = patch(
            "core.services.canonical_invitation_creation_service.get_new_invitation_expiry",
            new=AsyncMock(
                side_effect=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(days=2)
            ),
        )
        self._expiry_patcher.start()

    async def asyncTearDown(self):
        self._expiry_patcher.stop()
        await self.engine.dispose()
        self._server_override.__exit__(None, None, None)

    async def _owner(self, label: str, *, max_customers: int = 5) -> User:
        suffix = uuid4().hex
        owner = User(
            account_name=f"stage3_owner_{label}_{suffix[:10]}",
            mobile_number=f"098{int(suffix[:8], 16) % 100000000:08d}",
            full_name="Stage 3 owner",
            address="Stage 3 owner address",
            role=UserRole.SUPER_ADMIN,
            home_server=SERVER_IRAN,
            must_change_password=False,
            max_customers=max_customers,
        )
        async with self.session_factory() as session:
            session.add(owner)
            await session.commit()
        return owner

    @staticmethod
    def _identity(label: str) -> tuple[str, str]:
        suffix = uuid4().hex
        account = f"stage3_{label}_{suffix[:12]}"
        mobile = f"095{int(suffix[:8], 16) % 100000000:08d}"
        return account, mobile

    async def _create_standard(self, owner_id: int, account: str, mobile: str):
        async with self.session_factory() as session:
            result = await create_or_reuse_canonical_invitation(
                session,
                creator_user_id=owner_id,
                account_name=account,
                mobile_number=mobile,
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )
            await session.commit()
            return result.created, result.invitation.id, result.invitation.token

    async def test_concurrent_exact_retries_create_one_invitation_and_one_reservation(self):
        owner = await self._owner("retry")
        account, mobile = self._identity("retry")
        results = await asyncio.gather(
            *(self._create_standard(owner.id, account, mobile) for _ in range(6))
        )

        self.assertEqual(sum(1 for created, _, _ in results if created), 1)
        self.assertEqual(len({invitation_id for _, invitation_id, _ in results}), 1)
        self.assertEqual(len({token for _, _, token in results}), 1)
        async with self.session_factory() as session:
            self.assertEqual(
                (await session.execute(select(func.count()).select_from(Invitation).where(Invitation.mobile_number == mobile))).scalar_one(),
                1,
            )
            self.assertEqual(
                (await session.execute(select(func.count()).select_from(InvitationIdentityReservation).where(InvitationIdentityReservation.normalized_mobile == mobile))).scalar_one(),
                1,
            )

    async def test_cross_kind_or_owner_collision_is_terminal_but_exact_normalized_retry_reuses(self):
        owner = await self._owner("collision")
        other_owner = await self._owner("collision_other")
        account, mobile = self._identity("collision")
        first = await self._create_standard(owner.id, account, mobile)

        persian_mobile = mobile.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        async with self.session_factory() as session:
            retry = await create_or_reuse_canonical_invitation(
                session,
                creator_user_id=owner.id,
                account_name=account.upper(),
                mobile_number=persian_mobile,
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )
            await session.commit()
        self.assertFalse(retry.created)
        self.assertEqual(retry.invitation.id, first[1])

        for creator_id, kind, role in (
            (other_owner.id, InvitationKind.STANDARD, UserRole.STANDARD),
            (owner.id, InvitationKind.CUSTOMER, UserRole.STANDARD),
            (owner.id, InvitationKind.STANDARD, UserRole.POLICE),
        ):
            async with self.session_factory() as session:
                with self.assertRaisesRegex(CanonicalInvitationCreationError, "invitation_identity_conflict"):
                    await create_or_reuse_canonical_invitation(
                        session,
                        creator_user_id=creator_id,
                        account_name=account,
                        mobile_number=mobile,
                        role=role,
                        kind=kind,
                    )
                await session.rollback()

    async def test_customer_relation_exact_retry_is_idempotent_and_changed_payload_conflicts(self):
        owner = await self._owner("customer")
        account, mobile = self._identity("customer")

        async def create_customer(management_name: str):
            async with self.session_factory() as session:
                owner_row = await session.get(User, owner.id)
                return await create_or_reuse_owner_customer_relation(
                    session,
                    owner_user=owner_row,
                    account_name=account,
                    management_name=management_name,
                    mobile_number=mobile,
                    customer_tier=CustomerTier.TIER_1,
                )

        results = await asyncio.gather(*(create_customer("Stage 3 customer") for _ in range(4)))
        self.assertEqual(sum(1 for result in results if result.created), 1)
        self.assertEqual(len({result.relation.id for result in results}), 1)
        self.assertEqual(len({result.invitation.id for result in results}), 1)
        def utc_naive(value):
            if value.tzinfo is None or value.utcoffset() is None:
                return value
            return value.astimezone(timezone.utc).replace(tzinfo=None)

        self.assertTrue(
            all(
                utc_naive(result.relation.expires_at)
                == utc_naive(result.invitation.expires_at)
                for result in results
            )
        )

        with self.assertRaises(HTTPException) as exc:
            await create_customer("Changed customer name")
        self.assertEqual(exc.exception.status_code, 409)
        async with self.session_factory() as session:
            self.assertEqual(
                (await session.execute(select(func.count()).select_from(CustomerRelation).where(CustomerRelation.owner_user_id == owner.id))).scalar_one(),
                1,
            )

    async def test_independent_customer_creates_serialize_owner_capacity_without_partial_rows(self):
        owner = await self._owner("capacity", max_customers=1)
        first_account, first_mobile = self._identity("capacity_first")
        second_account, second_mobile = self._identity("capacity_second")

        async def create_customer(account: str, mobile: str):
            async with self.session_factory() as session:
                owner_row = await session.get(User, owner.id)
                return await create_or_reuse_owner_customer_relation(
                    session,
                    owner_user=owner_row,
                    account_name=account,
                    management_name=f"Customer {account[-8:]}",
                    mobile_number=mobile,
                    customer_tier=CustomerTier.TIER_1,
                )

        results = await asyncio.gather(
            create_customer(first_account, first_mobile),
            create_customer(second_account, second_mobile),
            return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(item, Exception) for item in results), 1)
        rejection = next(item for item in results if isinstance(item, Exception))
        self.assertIsInstance(rejection, HTTPException)
        self.assertEqual(rejection.status_code, 400)

        async with self.session_factory() as session:
            invitations = (
                await session.execute(
                    select(Invitation).where(Invitation.created_by_id == owner.id)
                )
            ).scalars().all()
            self.assertEqual(len(invitations), 1)
            self.assertEqual(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(CustomerRelation)
                        .where(CustomerRelation.owner_user_id == owner.id)
                    )
                ).scalar_one(),
                1,
            )
            self.assertEqual(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(InvitationIdentityReservation)
                        .where(InvitationIdentityReservation.invitation_id == invitations[0].id)
                    )
                ).scalar_one(),
                1,
            )

    async def test_deletion_invalidation_race_with_completion_has_one_terminal_state(self):
        owner = await self._owner("delete_race")
        account, mobile = self._identity("delete_race")
        _, invitation_id, token = await self._create_standard(owner.id, account, mobile)

        async def complete():
            async with self.session_factory() as session:
                invitation = await lock_invitation_for_transition(session, invitation_token=token)
                try:
                    complete_invitation(
                        invitation,
                        registered_user_id=owner.id,
                        completed_via=InvitationCompletionSurface.WEB,
                    )
                    await release_invitation_identity(session, invitation_id=invitation.id)
                    await session.commit()
                    return "completed"
                except ValueError:
                    await session.rollback()
                    return "revoked"

        async def revoke():
            async with self.session_factory() as session:
                await _soft_revoke_pending_invitation(
                    session,
                    invitation_token=token,
                    now=datetime.utcnow(),
                )
                await session.commit()
                return "revocation_checked"

        await asyncio.gather(complete(), revoke())
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            completed = bool(invitation.completed_at and invitation.registered_user_id and invitation.is_used)
            revoked = invitation.revoked_at is not None
            self.assertNotEqual(completed, revoked)
            self.assertEqual(
                (await session.execute(select(func.count()).select_from(InvitationIdentityReservation).where(InvitationIdentityReservation.invitation_id == invitation_id))).scalar_one(),
                0,
            )


if __name__ == "__main__":
    unittest.main()
