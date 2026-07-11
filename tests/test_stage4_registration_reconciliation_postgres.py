import asyncio
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.registration_contracts import TelegramRegistrationOutcome
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.authoritative_telegram_account_link_service import (
    complete_authoritative_telegram_account_link,
)
from core.services.telegram_registration_intent_service import (
    TelegramRegistrationIntentError,
    claim_due_registration_intents,
    create_or_reuse_ready_registration_intent,
    finalize_registration_intent,
    registration_projection_is_ready,
    schedule_registration_intent_retry,
)
from core.telegram_account_link_contracts import build_telegram_account_link_command
from core.utils import utc_now
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.telegram_link_token import TelegramLinkToken, TelegramLinkTokenStatus
from models.telegram_registration_command_receipt import TelegramRegistrationCommandReceipt
from models.telegram_registration_intent import (
    TelegramRegistrationIntent,
    TelegramRegistrationIntentStatus,
)
from models.user import User, UserRole


STAGE4_DATABASE_NAME_PATTERN = re.compile(r"^stage4_registration_[a-z0-9_]+$")


def _require_stage4_scratch_database(url):
    database_name = str(url.database or "").strip().lower()
    if not STAGE4_DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise RuntimeError("Stage 4 PostgreSQL tests require a stage4_registration_* scratch database")
    return url


def _stage4_database_url() -> str | None:
    explicit = str(os.getenv("STAGE4_TEST_DATABASE_URL", "")).strip()
    if not explicit:
        return None
    target = _require_stage4_scratch_database(make_url(explicit))
    return target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


STAGE4_DATABASE_URL = _stage4_database_url()


class Stage4DatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with self.assertRaisesRegex(RuntimeError, "stage4_registration_\\*"):
            _require_stage4_scratch_database(make_url("postgresql+asyncpg:///trading_bot_db"))


@unittest.skipUnless(
    STAGE4_DATABASE_URL,
    "set STAGE4_TEST_DATABASE_URL for Stage 4 PostgreSQL concurrency tests",
)
class Stage4RegistrationReconciliationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(STAGE4_DATABASE_URL, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        async with self.session_factory() as session:
            await session.execute(delete(TelegramRegistrationIntent))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _intent_values(label: str) -> dict:
        suffix = uuid4().hex
        expiry = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
        return {
            "invitation_token": f"INV-stage4-{label}-{suffix}",
            "mobile_number": f"096{int(suffix[:8], 16) % 100000000:08d}",
            "telegram_id": 8_200_000_000 + int(suffix[:6], 16),
            "telegram_username": f"stage4_{suffix[:8]}",
            "telegram_full_name": "Stage 4 Intent",
            "address": "Stage 4 exact durable address",
            "contact_verified_at": expiry - timedelta(minutes=20),
            "completed_at": expiry - timedelta(minutes=10),
            "invitation_expires_at_snapshot": expiry,
        }

    async def _persist_intent(self, values: dict, *, address: str | None = None):
        with override_current_server(SERVER_FOREIGN):
            async with self.session_factory() as session:
                result = await create_or_reuse_ready_registration_intent(
                    session,
                    **{**values, **({"address": address} if address is not None else {})},
                )
                await session.commit()
                return result.created, result.intent.id

    async def test_concurrent_ready_intent_retry_is_one_row_and_changed_payload_fails(self):
        values = self._intent_values("retry")
        results = await asyncio.gather(*(self._persist_intent(values) for _ in range(6)))
        self.assertEqual(sum(1 for created, _ in results if created), 1)
        self.assertEqual(len({intent_id for _, intent_id in results}), 1)
        retried_snapshot = {
            **values,
            "telegram_username": "changed_after_commit",
            "telegram_full_name": "Changed after durable commit",
            "contact_verified_at": values["contact_verified_at"] + timedelta(seconds=1),
            "completed_at": values["completed_at"] + timedelta(seconds=1),
        }
        reused = await self._persist_intent(retried_snapshot)
        self.assertFalse(reused[0])
        self.assertEqual(reused[1], results[0][1])
        with self.assertRaisesRegex(TelegramRegistrationIntentError, "changed_payload_replay"):
            await self._persist_intent(values, address="A different valid Stage 4 address")
        async with self.session_factory() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(TelegramRegistrationIntent).where(
                        TelegramRegistrationIntent.id == results[0][1]
                    )
                )
            ).scalar_one()
        self.assertEqual(count, 1)

    async def test_claim_lease_generation_blocks_stale_worker_and_recovers_after_restart(self):
        values = self._intent_values("lease")
        _, intent_id = await self._persist_intent(values)

        async def claim(now):
            with override_current_server(SERVER_FOREIGN):
                async with self.session_factory() as session:
                    attempts = await claim_due_registration_intents(
                        session,
                        limit=10,
                        lease_seconds=30,
                        now=now,
                    )
                    await session.commit()
                    return attempts

        first_claims, competing_claims = await asyncio.gather(
            claim(datetime.now(timezone.utc)),
            claim(datetime.now(timezone.utc)),
        )
        attempts = [*first_claims, *competing_claims]
        self.assertEqual(len(attempts), 1)
        first = attempts[0]

        async with self.session_factory() as session:
            intent = await session.get(TelegramRegistrationIntent, intent_id)
            intent.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        second = (await claim(datetime.now(timezone.utc)))[0]
        self.assertEqual(second.attempt, first.attempt + 1)

        with override_current_server(SERVER_FOREIGN):
            async with self.session_factory() as session:
                stale_update = await schedule_registration_intent_retry(
                    session,
                    intent_id=intent_id,
                    attempt=first.attempt,
                    error_code="late_worker",
                    next_retry_at=utc_now() + timedelta(seconds=10),
                )
                current_update = await finalize_registration_intent(
                    session,
                    intent_id=intent_id,
                    attempt=second.attempt,
                    outcome=TelegramRegistrationOutcome.INVITATION_REVOKED,
                    authoritative_user_id=None,
                )
                await session.commit()
        self.assertFalse(stale_update)
        self.assertTrue(current_update)
        async with self.session_factory() as session:
            intent = await session.get(TelegramRegistrationIntent, intent_id)
            self.assertEqual(intent.status, TelegramRegistrationIntentStatus.REJECTED)
            self.assertEqual(intent.last_error_code, "invitation_revoked")

    async def test_poison_intent_is_rejected_without_starving_valid_due_work(self):
        values = self._intent_values("poison_valid")
        _, valid_id = await self._persist_intent(values)
        poison_id = uuid4()
        async with self.session_factory() as session:
            session.add(
                TelegramRegistrationIntent(
                    id=poison_id,
                    idempotency_key=f"telegram-registration:{uuid4().hex}",
                    invitation_token=f"INV-stage4-poison-{uuid4().hex}",
                    normalized_mobile="09120000001",
                    telegram_id=8_250_000_001,
                    telegram_username=None,
                    telegram_full_name=None,
                    address=None,
                    contact_verified_at=utc_now() - timedelta(minutes=2),
                    completed_at=utc_now() - timedelta(minutes=1),
                    invitation_expires_at_snapshot=utc_now() + timedelta(hours=1),
                    status=TelegramRegistrationIntentStatus.READY,
                )
            )
            await session.commit()

        with override_current_server(SERVER_FOREIGN):
            async with self.session_factory() as session:
                attempts = await claim_due_registration_intents(
                    session,
                    limit=10,
                    lease_seconds=30,
                )
                await session.commit()
        self.assertEqual({attempt.intent_id for attempt in attempts}, {valid_id})
        async with self.session_factory() as session:
            poison = await session.get(TelegramRegistrationIntent, poison_id)
            self.assertEqual(poison.status, TelegramRegistrationIntentStatus.REJECTED)
            self.assertEqual(poison.last_error_code, "invalid_local_intent")

    async def _seed_link_user(self, label: str) -> tuple[User, str]:
        suffix = uuid4().hex
        raw_token = f"stage4-link-{suffix}-{uuid4().hex}"
        user = User(
            account_name=f"stage4_link_{label}_{suffix[:10]}",
            mobile_number=f"097{int(suffix[:8], 16) % 100000000:08d}",
            full_name="Preserved Web Full Name",
            address="Preserved Web Address",
            role=UserRole.STANDARD,
            home_server=SERVER_IRAN,
            must_change_password=False,
        )
        from core.services.telegram_link_token_service import hash_telegram_link_token

        async with self.session_factory() as session:
            session.add(user)
            await session.flush()
            session.add(
                TelegramLinkToken(
                    user_id=user.id,
                    token_hash=hash_telegram_link_token(raw_token),
                    status=TelegramLinkTokenStatus.PENDING,
                    issued_by_server=SERVER_IRAN,
                    expires_at=utc_now() + timedelta(minutes=10),
                )
            )
            await session.commit()
        return user, raw_token

    async def test_account_link_response_loss_replays_receipt_and_preserves_web_fields(self):
        user, raw_token = await self._seed_link_user("replay")
        command = build_telegram_account_link_command(
            mode="link_token",
            link_token=raw_token,
            mobile_number=user.mobile_number,
            telegram_id=8_300_000_000 + int(uuid4().hex[:6], 16),
            telegram_username="new_telegram_username",
            telegram_full_name="Must Not Replace Web Name",
            address=None,
            contact_verified_at=utc_now(),
        )

        async def complete(link_command):
            with override_current_server(SERVER_IRAN), patch(
                "core.services.authoritative_telegram_account_link_service.ensure_mandatory_channel_membership",
                new=AsyncMock(),
            ):
                async with self.session_factory() as session:
                    return await complete_authoritative_telegram_account_link(
                        session,
                        command=link_command,
                        source_server=SERVER_FOREIGN,
                    )

        first = await complete(command)
        replay = await complete(command)
        self.assertEqual(first.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        self.assertEqual(replay.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        self.assertTrue(replay.replayed)

        changed_profile = command.model_copy(
            update={
                "telegram_username": "changed_after_response_loss",
                "telegram_full_name": "Changed snapshot must not replace Web name",
                "contact_verified_at": utc_now() + timedelta(seconds=5),
            }
        )
        profile_replay = await complete(changed_profile)
        self.assertEqual(profile_replay.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        self.assertTrue(profile_replay.replayed)

        changed_business_payload = command.model_copy(update={"address": "Different address"})
        conflict = await complete(changed_business_payload)
        self.assertEqual(conflict.outcome, TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY)

        async with self.session_factory() as session:
            stored = await session.get(User, user.id)
            self.assertEqual(stored.telegram_id, command.telegram_id)
            self.assertEqual(stored.username, "new_telegram_username")
            self.assertEqual(stored.full_name, "Preserved Web Full Name")
            self.assertEqual(stored.address, "Preserved Web Address")
            self.assertEqual(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(TelegramRegistrationCommandReceipt)
                        .where(TelegramRegistrationCommandReceipt.command_id == command.command_id)
                    )
                ).scalar_one(),
                1,
            )

    async def test_distinct_account_link_commands_race_to_one_telegram_identity(self):
        user, raw_token = await self._seed_link_user("race")
        telegram_base = 8_400_000_000 + int(uuid4().hex[:6], 16)
        commands = [
            build_telegram_account_link_command(
                mode="link_token",
                link_token=raw_token,
                mobile_number=user.mobile_number,
                telegram_id=telegram_base + offset,
                telegram_username=f"race_{offset}",
                telegram_full_name=None,
                address=None,
                contact_verified_at=utc_now(),
            )
            for offset in (0, 1)
        ]

        async def complete(command):
            with override_current_server(SERVER_IRAN), patch(
                "core.services.authoritative_telegram_account_link_service.ensure_mandatory_channel_membership",
                new=AsyncMock(),
            ):
                async with self.session_factory() as session:
                    return await complete_authoritative_telegram_account_link(
                        session,
                        command=command,
                        source_server=SERVER_FOREIGN,
                    )

        results = await asyncio.gather(*(complete(command) for command in commands))
        self.assertEqual(
            sum(result.outcome == TelegramRegistrationOutcome.LINKED_EXISTING for result in results),
            1,
        )
        self.assertEqual(
            sum(result.outcome == TelegramRegistrationOutcome.LINK_TOKEN_ALREADY_USED for result in results),
            1,
        )
        async with self.session_factory() as session:
            stored = await session.get(User, user.id)
            self.assertIn(stored.telegram_id, {command.telegram_id for command in commands})
            receipt_count = (
                await session.execute(
                    select(func.count()).select_from(TelegramRegistrationCommandReceipt).where(
                        TelegramRegistrationCommandReceipt.command_id.in_(
                            [command.command_id for command in commands]
                        )
                    )
                )
            ).scalar_one()
            self.assertEqual(receipt_count, 2)

    async def test_projection_gate_requires_completed_invitation_and_kind_specific_relation(self):
        suffix = uuid4().hex
        owner = User(
            account_name=f"stage4_owner_{suffix[:10]}",
            mobile_number=f"093{int(suffix[:8], 16) % 100000000:08d}",
            full_name="Stage 4 Owner",
            address="Stage 4 Owner Address",
            role=UserRole.SUPER_ADMIN,
            home_server=SERVER_IRAN,
            must_change_password=False,
        )
        projection_telegram_id = 8_500_000_000 + int(uuid4().hex[:6], 16)
        customer = User(
            account_name=f"stage4_customer_{suffix[:10]}",
            mobile_number=f"094{int(suffix[8:16], 16) % 100000000:08d}",
            full_name="Stage 4 Customer",
            address="Stage 4 Customer Address",
            role=UserRole.STANDARD,
            telegram_id=projection_telegram_id,
            home_server=SERVER_IRAN,
            must_change_password=False,
        )
        token = f"CUST-stage4-{suffix}"
        async with self.session_factory() as session:
            session.add_all([owner, customer])
            await session.flush()
            invitation = Invitation(
                account_name=customer.account_name,
                mobile_number=customer.mobile_number,
                token=token,
                role=UserRole.STANDARD,
                kind=InvitationKind.CUSTOMER,
                created_by_id=owner.id,
                is_used=True,
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
                registered_user_id=customer.id,
                completed_at=utc_now(),
                completed_via=InvitationCompletionSurface.TELEGRAM,
            )
            session.add(invitation)
            await session.commit()

        command = self._intent_values("projection")
        from core.registration_contracts import TelegramRegistrationCommand

        registration_command = TelegramRegistrationCommand(
            command_id=uuid4(),
            idempotency_key=f"telegram-registration:{uuid4().hex}",
            invitation_token=token,
            mobile_number=customer.mobile_number,
            telegram_id=customer.telegram_id,
            telegram_username=None,
            telegram_full_name=None,
            address="Stage 4 Customer Address",
            contact_verified_at=command["contact_verified_at"],
            local_completed_at=command["completed_at"],
            invitation_expires_at_snapshot=command["invitation_expires_at_snapshot"],
        )
        with override_current_server(SERVER_FOREIGN):
            async with self.session_factory() as session:
                self.assertFalse(
                    await registration_projection_is_ready(
                        session,
                        command=registration_command,
                    )
                )
                session.add(
                    CustomerRelation(
                        owner_user_id=owner.id,
                        customer_user_id=customer.id,
                        created_by_user_id=owner.id,
                        invitation_token=token,
                        management_name="Stage 4 Customer",
                        customer_tier=CustomerTier.TIER_1,
                        status=CustomerRelationStatus.ACTIVE,
                        expires_at=utc_now() + timedelta(hours=1),
                        activated_at=utc_now(),
                    )
                )
                await session.commit()
            async with self.session_factory() as session:
                self.assertTrue(
                    await registration_projection_is_ready(
                        session,
                        command=registration_command,
                    )
                )
                stored_invitation = (
                    await session.execute(select(Invitation).where(Invitation.token == token))
                ).scalar_one()
                stored_invitation.account_name = f"different_{suffix[:10]}"
                await session.commit()
            async with self.session_factory() as session:
                self.assertFalse(
                    await registration_projection_is_ready(
                        session,
                        command=registration_command,
                    )
                )


if __name__ == "__main__":
    unittest.main()
