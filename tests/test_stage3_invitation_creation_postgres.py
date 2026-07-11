import asyncio
import os
import re
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch
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
from core.services.user_deletion_service import _close_owned_customer_relations
from core.services.invitation_sms_delivery_service import (
    deliver_invitation_sms_once,
    prepare_invitation_sms_delivery,
)
from core.registration_contracts import InvitationDerivedState, InvitationSMSStatus
from core.sms import SMSDeliveryOutcome
from core.utils import utc_now
from core.services.invitation_lifecycle_service import derive_invitation_state
from core.services.invitation_requester_service import resolve_current_invitation_requester
from core.invitation_creation_contracts import InvitationRequesterIdentity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.invitation_identity_reservation import InvitationIdentityReservation
from models.invitation_sms_delivery import InvitationSMSDelivery
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

    async def test_exact_retry_and_terminal_transition_share_advisory_before_row_order(self):
        owner = await self._owner("cross_order")
        account, mobile = self._identity("cross_order")
        _, invitation_id, token = await self._create_standard(owner.id, account, mobile)
        retry_has_locks = asyncio.Event()
        transition_started = asyncio.Event()

        from core.services import canonical_invitation_creation_service as canonical

        original_find = canonical.find_identity_reservation

        async def barrier_find(db, identity):
            retry_has_locks.set()
            await transition_started.wait()
            return await original_find(db, identity)

        async def exact_retry():
            async with self.session_factory() as session:
                with patch.object(canonical, "find_identity_reservation", new=barrier_find):
                    result = await canonical.create_or_reuse_canonical_invitation(
                        session,
                        creator_user_id=owner.id,
                        account_name=account,
                        mobile_number=mobile,
                        role=UserRole.STANDARD,
                        kind=InvitationKind.STANDARD,
                    )
                    await session.commit()
                    return result.created

        async def revoke_after_retry_locks():
            await retry_has_locks.wait()
            transition_started.set()
            async with self.session_factory() as session:
                await _soft_revoke_pending_invitation(
                    session,
                    invitation_token=token,
                    now=datetime.utcnow(),
                )
                await session.commit()

        retry_created, _ = await asyncio.wait_for(
            asyncio.gather(exact_retry(), revoke_after_retry_locks()),
            timeout=5,
        )
        self.assertFalse(retry_created)
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            self.assertIsNotNone(invitation.revoked_at)

    async def test_owner_relation_close_never_stale_overwrites_completed_relation_as_revoked(self):
        owner = await self._owner("relation_close")
        account, mobile = self._identity("relation_close")
        async with self.session_factory() as session:
            owner_row = await session.get(User, owner.id)
            created = await create_or_reuse_owner_customer_relation(
                session,
                owner_user=owner_row,
                account_name=account,
                management_name="Stage 3 relation close",
                mobile_number=mobile,
                customer_tier=CustomerTier.TIER_1,
            )
            invitation_id = created.invitation.id
            relation_id = created.relation.id

        async def complete_relation():
            async with self.session_factory() as session:
                invitation = await lock_invitation_for_transition(
                    session,
                    invitation_id=invitation_id,
                )
                relation = (
                    await session.execute(
                        select(CustomerRelation)
                        .where(CustomerRelation.id == relation_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one()
                if derive_invitation_state(invitation) != InvitationDerivedState.PENDING:
                    await session.rollback()
                    return "revoked_first"
                suffix = uuid4().hex
                customer = User(
                    account_name=account,
                    mobile_number=mobile,
                    full_name="Stage 3 customer",
                    address="Stage 3 customer address",
                    role=UserRole.STANDARD,
                    home_server=SERVER_IRAN,
                    must_change_password=False,
                )
                session.add(customer)
                await session.flush()
                complete_invitation(
                    invitation,
                    registered_user_id=customer.id,
                    completed_via=InvitationCompletionSurface.WEB,
                )
                relation.customer_user_id = customer.id
                relation.status = CustomerRelationStatus.ACTIVE
                relation.activated_at = datetime.now(timezone.utc)
                await release_invitation_identity(session, invitation_id=invitation.id)
                await session.commit()
                return "completed_first"

        async def close_owner_relations():
            async with self.session_factory() as session:
                owner_row = await session.get(User, owner.id)
                with patch(
                    "core.services.user_deletion_service._delete_user_account_in_transaction",
                    new=AsyncMock(),
                ):
                    await _close_owned_customer_relations(
                        session,
                        owner_row,
                        processed_user_ids=set(),
                        effects=[],
                    )
                    await session.commit()
                return "closed"

        await asyncio.wait_for(
            asyncio.gather(complete_relation(), close_owner_relations()),
            timeout=5,
        )
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            relation = await session.get(CustomerRelation, relation_id)
            if invitation.completed_at is not None:
                self.assertNotEqual(str(getattr(relation.status, "value", relation.status)), "revoked")
            else:
                self.assertIsNotNone(invitation.revoked_at)
                self.assertEqual(str(getattr(relation.status, "value", relation.status)), "revoked")

    async def test_sms_delivery_and_requester_identity_are_durable_on_postgres(self):
        owner = await self._owner("sms_principal")
        async with self.session_factory() as session:
            owner_row = await session.get(User, owner.id)
            owner_row.telegram_id = 8_900_000_000 + owner.id
            await session.commit()
            identity = InvitationRequesterIdentity(
                account_name=owner_row.account_name,
                mobile_number=owner_row.mobile_number,
                telegram_id=owner_row.telegram_id,
            )

        async with self.session_factory() as session:
            resolved = await resolve_current_invitation_requester(session, identity=identity)
            self.assertEqual(resolved.id, owner.id)
            await session.rollback()

        account, mobile = self._identity("sms")
        _, invitation_id, _ = await self._create_standard(owner.id, account, mobile)
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            await prepare_invitation_sms_delivery(
                session,
                invitation=invitation,
                enabled=True,
                newly_created=True,
            )
            await session.commit()
            sender = Mock(return_value=True)
            status = await deliver_invitation_sms_once(
                session,
                invitation_id=invitation_id,
                newly_created=True,
                sender=sender,
            )
            self.assertEqual(status, InvitationSMSStatus.ACCEPTED)
            replay_sender = Mock()
            replay = await deliver_invitation_sms_once(
                session,
                invitation_id=invitation_id,
                newly_created=False,
                sender=replay_sender,
            )
            self.assertEqual(replay, InvitationSMSStatus.ACCEPTED)
            replay_sender.assert_not_called()
            delivery = (
                await session.execute(
                    select(InvitationSMSDelivery).where(
                        InvitationSMSDelivery.invitation_id == invitation_id
                    )
                )
            ).scalar_one()
            self.assertEqual(delivery.attempt_count, 1)

    async def test_live_sms_claim_retry_stays_pending_and_owner_records_acceptance(self):
        owner = await self._owner("sms_live_claim")
        account, mobile = self._identity("sms_live_claim")
        _, invitation_id, _ = await self._create_standard(owner.id, account, mobile)
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            await prepare_invitation_sms_delivery(
                session,
                invitation=invitation,
                enabled=True,
                newly_created=True,
            )
            await session.commit()

        provider_started = threading.Event()
        provider_release = threading.Event()
        provider_calls = 0

        def blocked_sender():
            nonlocal provider_calls
            provider_calls += 1
            provider_started.set()
            if not provider_release.wait(timeout=5):
                raise TimeoutError("test provider release timed out")
            return SMSDeliveryOutcome.ACCEPTED

        async def first_delivery():
            async with self.session_factory() as session:
                return await deliver_invitation_sms_once(
                    session,
                    invitation_id=invitation_id,
                    newly_created=True,
                    sender=blocked_sender,
                )

        first_task = asyncio.create_task(first_delivery())
        self.assertTrue(await asyncio.to_thread(provider_started.wait, 2))
        async with self.session_factory() as retry_session:
            retry_sender = Mock()
            retry_status = await deliver_invitation_sms_once(
                retry_session,
                invitation_id=invitation_id,
                newly_created=False,
                sender=retry_sender,
            )
        self.assertEqual(retry_status, InvitationSMSStatus.PENDING)
        retry_sender.assert_not_called()

        provider_release.set()
        self.assertEqual(await first_task, InvitationSMSStatus.ACCEPTED)
        self.assertEqual(provider_calls, 1)
        async with self.session_factory() as session:
            delivery = (
                await session.execute(
                    select(InvitationSMSDelivery).where(
                        InvitationSMSDelivery.invitation_id == invitation_id
                    )
                )
            ).scalar_one()
            self.assertEqual(delivery.status, InvitationSMSStatus.ACCEPTED.value)
            self.assertEqual(delivery.attempt_count, 1)

    async def test_live_sms_claim_owner_records_explicit_failure(self):
        owner = await self._owner("sms_live_failure")
        account, mobile = self._identity("sms_live_failure")
        _, invitation_id, _ = await self._create_standard(owner.id, account, mobile)
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            await prepare_invitation_sms_delivery(
                session,
                invitation=invitation,
                enabled=True,
                newly_created=True,
            )
            await session.commit()

        provider_started = threading.Event()
        provider_release = threading.Event()
        provider_calls = 0

        def blocked_sender():
            nonlocal provider_calls
            provider_calls += 1
            provider_started.set()
            if not provider_release.wait(timeout=5):
                raise TimeoutError("test provider release timed out")
            return SMSDeliveryOutcome.FAILED

        async def first_delivery():
            async with self.session_factory() as session:
                return await deliver_invitation_sms_once(
                    session,
                    invitation_id=invitation_id,
                    newly_created=True,
                    sender=blocked_sender,
                )

        first_task = asyncio.create_task(first_delivery())
        self.assertTrue(await asyncio.to_thread(provider_started.wait, 2))
        async with self.session_factory() as retry_session:
            retry_sender = Mock()
            retry_status = await deliver_invitation_sms_once(
                retry_session,
                invitation_id=invitation_id,
                newly_created=False,
                sender=retry_sender,
            )
        self.assertEqual(retry_status, InvitationSMSStatus.PENDING)
        retry_sender.assert_not_called()

        provider_release.set()
        self.assertEqual(await first_task, InvitationSMSStatus.FAILED)
        self.assertEqual(provider_calls, 1)
        async with self.session_factory() as session:
            delivery = (
                await session.execute(
                    select(InvitationSMSDelivery).where(
                        InvitationSMSDelivery.invitation_id == invitation_id
                    )
                )
            ).scalar_one()
            self.assertEqual(delivery.status, InvitationSMSStatus.FAILED.value)
            self.assertEqual(delivery.attempt_count, 1)

    async def test_stale_sms_claim_becomes_ambiguous_without_resend(self):
        owner = await self._owner("sms_stale_claim")
        account, mobile = self._identity("sms_stale_claim")
        _, invitation_id, _ = await self._create_standard(owner.id, account, mobile)
        async with self.session_factory() as session:
            invitation = await session.get(Invitation, invitation_id)
            delivery = await prepare_invitation_sms_delivery(
                session,
                invitation=invitation,
                enabled=True,
                newly_created=True,
            )
            delivery.attempt_count = 1
            delivery.claimed_at = utc_now() - timedelta(minutes=1)
            await session.commit()

        sender = Mock()
        async with self.session_factory() as session:
            status = await deliver_invitation_sms_once(
                session,
                invitation_id=invitation_id,
                newly_created=False,
                sender=sender,
            )
        self.assertEqual(status, InvitationSMSStatus.AMBIGUOUS)
        sender.assert_not_called()
        async with self.session_factory() as session:
            delivery = await session.scalar(
                select(InvitationSMSDelivery).where(
                    InvitationSMSDelivery.invitation_id == invitation_id
                )
            )
            self.assertEqual(delivery.status, InvitationSMSStatus.AMBIGUOUS.value)
            self.assertEqual(delivery.attempt_count, 1)


if __name__ == "__main__":
    unittest.main()
