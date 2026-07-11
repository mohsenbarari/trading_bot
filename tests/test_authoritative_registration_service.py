import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from core.enums import UserAccountStatus
from core.registration_contracts import (
    TelegramRegistrationCommand,
    TelegramRegistrationOutcome,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services import authoritative_registration_service as registration
from core.services.registration_command_receipt_service import RegistrationCommandReplayConflict
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
from models.user import User, UserRole


class _FakeDB:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock(side_effect=self._flush)

    def add(self, item):
        self.added.append(item)

    def add_all(self, items):
        self.added.extend(items)

    async def execute(self, _statement, _params=None):
        raise AssertionError("Unexpected execute; query boundary should be patched in this unit test")

    async def _flush(self):
        next_id = 77
        for item in self.added:
            if isinstance(item, User) and item.id is None:
                item.id = next_id
                next_id += 1


class _CaptureResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _CaptureDB:
    def __init__(self, value=None):
        self.value = value
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return _CaptureResult(self.value)


def _invitation(
    *,
    token="INV-stage2-unit-123456",
    kind=InvitationKind.STANDARD,
    role=UserRole.STANDARD,
    expires_at=None,
    is_used=False,
    registered_user_id=None,
    completed_at=None,
    completed_via=None,
    revoked_at=None,
):
    return Invitation(
        id=11,
        token=token,
        short_code="STG20001",
        account_name="stage2_user",
        mobile_number="09120000001",
        role=role,
        kind=kind,
        created_by_id=5,
        is_used=is_used,
        expires_at=expires_at or (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)),
        registered_user_id=registered_user_id,
        completed_at=completed_at,
        completed_via=completed_via,
        revoked_at=revoked_at,
    )


def _telegram_command(invitation, *, telegram_id=123456789, command_id=None, idempotency_key=None):
    expiry = invitation.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    completed_at = expiry - timedelta(minutes=30)
    return TelegramRegistrationCommand(
        command_id=command_id or uuid4(),
        idempotency_key=idempotency_key or f"stage2:{uuid4()}",
        invitation_token=invitation.token,
        mobile_number=invitation.mobile_number,
        telegram_id=telegram_id,
        telegram_username="stage2_tg",
        telegram_full_name="Stage Two User",
        address="Tehran, Stage Two address",
        contact_verified_at=completed_at - timedelta(minutes=1),
        local_completed_at=completed_at,
        invitation_expires_at_snapshot=expiry,
    )


def _existing_user(*, user_id=55, telegram_id=None):
    return User(
        id=user_id,
        account_name="stage2_user",
        mobile_number="09120000001",
        telegram_id=telegram_id,
        username="preserved_username",
        full_name="Authoritative Web Profile",
        address="Authoritative Web Address",
        role=UserRole.STANDARD,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
        deleted_at=None,
        has_bot_access=False,
        home_server="iran",
        must_change_password=False,
    )


class AuthoritativeRegistrationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._server_override = override_current_server(SERVER_IRAN)
        self._server_override.__enter__()

    def tearDown(self):
        self._server_override.__exit__(None, None, None)

    def test_relation_contract_fails_closed_on_identity_owner_role_and_tier_drift(self):
        def accountant_projection():
            invitation = _invitation(
                token="ACCT-stage2-contract",
                kind=InvitationKind.ACCOUNTANT,
                role=UserRole.WATCH,
            )
            relation = AccountantRelation(
                owner_user_id=5,
                created_by_user_id=5,
                invitation_token=invitation.token,
                global_account_name=invitation.account_name,
                relation_display_name="Accountant",
                mobile_number=invitation.mobile_number,
                status=AccountantRelationStatus.PENDING,
                expires_at=invitation.expires_at,
            )
            identity = registration.normalize_invitation_identity(
                mobile_number=invitation.mobile_number,
                account_name=invitation.account_name,
            )
            return invitation, relation, identity

        valid_invitation, valid_accountant, valid_identity = accountant_projection()
        registration._validate_relation_contract(
            invitation=valid_invitation,
            identity=valid_identity,
            accountant_relation=valid_accountant,
            customer_relation=None,
        )
        accountant_drift = (
            ("mobile", "relation", "mobile_number", "09129999999"),
            ("account", "relation", "global_account_name", "different_account"),
            ("owner", "relation", "owner_user_id", 999),
            ("creator", "relation", "created_by_user_id", 999),
            ("token", "relation", "invitation_token", "ACCT-different-token"),
            (
                "expiry",
                "relation",
                "expires_at",
                valid_invitation.expires_at + timedelta(seconds=1),
            ),
            ("kind", "invitation", "kind", InvitationKind.STANDARD),
            ("role", "invitation", "role", UserRole.STANDARD),
        )
        for label, target_name, attribute, value in accountant_drift:
            with self.subTest(relation="accountant", drift=label):
                invitation, relation, identity = accountant_projection()
                target = relation if target_name == "relation" else invitation
                setattr(target, attribute, value)
                with self.assertRaises(registration.AuthoritativeRegistrationError):
                    registration._validate_relation_contract(
                        invitation=invitation,
                        identity=identity,
                        accountant_relation=relation,
                        customer_relation=None,
                    )

        def customer_projection():
            invitation = _invitation(
                token="CUST-stage2-contract",
                kind=InvitationKind.CUSTOMER,
                role=UserRole.STANDARD,
            )
            relation = CustomerRelation(
                owner_user_id=5,
                created_by_user_id=5,
                invitation_token=invitation.token,
                management_name="Customer",
                customer_tier=CustomerTier.TIER_1,
                status=CustomerRelationStatus.PENDING,
                expires_at=invitation.expires_at,
            )
            identity = registration.normalize_invitation_identity(
                mobile_number=invitation.mobile_number,
                account_name=invitation.account_name,
            )
            return invitation, relation, identity

        valid_invitation, valid_customer, valid_identity = customer_projection()
        registration._validate_relation_contract(
            invitation=valid_invitation,
            identity=valid_identity,
            accountant_relation=None,
            customer_relation=valid_customer,
        )
        customer_drift = (
            ("owner", "relation", "owner_user_id", 999),
            ("creator", "relation", "created_by_user_id", 999),
            ("token", "relation", "invitation_token", "CUST-different-token"),
            ("tier", "relation", "customer_tier", "tier3"),
            (
                "expiry",
                "relation",
                "expires_at",
                valid_invitation.expires_at + timedelta(seconds=1),
            ),
            ("kind", "invitation", "kind", InvitationKind.STANDARD),
            ("role", "invitation", "role", UserRole.WATCH),
        )
        for label, target_name, attribute, value in customer_drift:
            with self.subTest(relation="customer", drift=label):
                invitation, relation, identity = customer_projection()
                target = relation if target_name == "relation" else invitation
                setattr(target, attribute, value)
                with self.assertRaises(registration.AuthoritativeRegistrationError):
                    registration._validate_relation_contract(
                        invitation=invitation,
                        identity=identity,
                        accountant_relation=None,
                        customer_relation=relation,
                    )

    async def test_relation_loader_rejects_cross_kind_token_projection(self):
        accountant = SimpleNamespace(id=1)
        with patch.object(
            registration,
            "lock_accountant_relation_for_registration",
            new=AsyncMock(return_value=accountant),
        ), patch.object(
            registration,
            "lock_customer_relation_for_registration",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(registration.AuthoritativeRegistrationError):
                await registration._load_relation_for_registration(
                    _FakeDB(),
                    invitation=_invitation(),
                    kind=InvitationKind.STANDARD,
                )

    async def test_foreign_server_is_rejected_before_database_access(self):
        db = _FakeDB()
        invitation = _invitation()
        request = registration.AuthoritativeRegistrationRequest.for_web(
            invitation_token=invitation.token,
            address="Tehran, Stage Two address",
        )

        with override_current_server(SERVER_FOREIGN):
            with self.assertRaisesRegex(RuntimeError, "authoritative_registration_requires_iran"):
                await registration.complete_invitation_registration(db, request)

        db.flush.assert_not_awaited()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()

    async def test_invitation_relation_and_identity_locks_are_explicit_and_commit_free(self):
        invitation_db = _CaptureDB()
        await registration._load_invitation_for_update(invitation_db, "INV-stage2-unit-123456")
        invitation_sql = str(
            invitation_db.calls[0][0].compile(dialect=postgresql.dialect())
        ).upper()
        self.assertIn("FOR UPDATE", invitation_sql)

        accountant_db = _CaptureDB()
        await registration.lock_accountant_relation_for_registration(
            accountant_db,
            "ACCT-stage2-unit-123",
        )
        accountant_sql = str(
            accountant_db.calls[0][0].compile(dialect=postgresql.dialect())
        ).upper()
        self.assertIn("FOR UPDATE", accountant_sql)

        customer_db = _CaptureDB()
        await registration.lock_customer_relation_for_registration(
            customer_db,
            "CUST-stage2-unit-123",
        )
        customer_sql = str(
            customer_db.calls[0][0].compile(dialect=postgresql.dialect())
        ).upper()
        self.assertIn("FOR UPDATE", customer_sql)

        lock_db = _CaptureDB()
        identity = registration.normalize_invitation_identity(
            mobile_number="09120000001",
            account_name="stage2_user",
        )
        await registration._acquire_registration_identity_locks(
            lock_db,
            identity=identity,
            telegram_id=123456789,
        )
        lock_keys = [params["lock_key"] for _, params in lock_db.calls]
        self.assertEqual(lock_keys, sorted(lock_keys))
        self.assertEqual(len(lock_keys), 3)
        self.assertNotIn(identity.mobile_number, " ".join(lock_keys))
        self.assertNotIn(identity.account_name, " ".join(lock_keys))
        self.assertNotIn("123456789", " ".join(lock_keys))

    async def test_web_creation_uses_literal_iran_owner_and_one_transaction(self):
        db = _FakeDB()
        invitation = _invitation()
        checkpoints = []

        async def checkpoint(name):
            checkpoints.append(name)

        async def enqueue_outbox(_db, *, new_user):
            checkpoints.append(f"outbox:{new_user.id}")
            return []

        with patch.object(
            registration,
            "_load_invitation_for_update",
            new=AsyncMock(return_value=invitation),
        ), patch.object(
            registration,
            "_acquire_registration_identity_locks",
            new=AsyncMock(),
        ), patch.object(
            registration,
            "_load_matching_users_for_update",
            new=AsyncMock(return_value=[]),
        ), patch.object(
            registration,
            "_load_relation_for_registration",
            new=AsyncMock(return_value=(None, None)),
        ), patch.object(
            registration,
            "release_invitation_identity",
            new=AsyncMock(),
        ) as release_mock, patch.object(
            registration,
            "ensure_mandatory_channel_membership",
            new=AsyncMock(),
        ) as membership_mock, patch.object(
            registration,
            "enqueue_project_user_joined_telegram_outbox",
            new=AsyncMock(side_effect=enqueue_outbox),
        ):
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_web(
                    invitation_token=invitation.token,
                    address="Tehran, Stage Two address",
                ),
                checkpoint=checkpoint,
            )

        self.assertEqual(result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertTrue(result.first_terminal_transition)
        self.assertTrue(result.announce_project_user)
        user = result.user
        self.assertIsNotNone(user)
        self.assertEqual(User.__table__.c.home_server.default.arg, "foreign")
        self.assertEqual(user.home_server, "iran")
        self.assertFalse(user.must_change_password)
        self.assertIsNone(user.telegram_id)
        self.assertTrue(user.has_bot_access)
        self.assertEqual(user.max_sessions, 1)
        self.assertTrue(invitation.is_used)
        self.assertEqual(invitation.registered_user_id, user.id)
        self.assertEqual(invitation.completed_via, InvitationCompletionSurface.WEB)
        self.assertIsNotNone(invitation.completed_at)
        release_mock.assert_awaited_once_with(db, invitation_id=11)
        membership_mock.assert_awaited_once_with(db, user=user)
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
        self.assertLess(checkpoints.index("outbox:77"), checkpoints.index("before_commit"))
        self.assertEqual(checkpoints[-1], "after_commit")

    async def test_web_customer_creation_preserves_relation_projection_and_profile_defaults(self):
        db = _FakeDB()
        invitation = _invitation(token="CUST-stage2-unit-123", kind=InvitationKind.CUSTOMER)
        relation = CustomerRelation(
            id=21,
            owner_user_id=5,
            created_by_user_id=5,
            invitation_token=invitation.token,
            management_name="Customer Management Name",
            customer_tier=CustomerTier.TIER_1,
            status=CustomerRelationStatus.PENDING,
            expires_at=invitation.expires_at.replace(tzinfo=timezone.utc),
            customer_user_id=None,
            deleted_at=None,
        )

        with patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, relation))
        ), patch.object(registration, "release_invitation_identity", new=AsyncMock()), patch.object(
            registration, "ensure_mandatory_channel_membership", new=AsyncMock()
        ), patch.object(
            registration, "enqueue_project_user_joined_telegram_outbox", new=AsyncMock()
        ) as enqueue_mock:
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_web(
                    invitation_token=invitation.token,
                    address="Tehran, Customer address",
                ),
            )

        self.assertEqual(result.user.full_name, "Customer Management Name")
        self.assertFalse(result.user.has_bot_access)
        self.assertEqual(relation.customer_user_id, result.user.id)
        self.assertEqual(relation.status, CustomerRelationStatus.ACTIVE)
        self.assertIsNotNone(relation.activated_at)
        self.assertFalse(result.announce_project_user)
        enqueue_mock.assert_not_awaited()

    async def test_web_accountant_creation_preserves_relation_projection(self):
        db = _FakeDB()
        invitation = _invitation(
            token="ACCT-stage2-unit-123",
            kind=InvitationKind.ACCOUNTANT,
            role=UserRole.WATCH,
        )
        relation = AccountantRelation(
            id=22,
            owner_user_id=5,
            created_by_user_id=5,
            invitation_token=invitation.token,
            global_account_name=invitation.account_name,
            relation_display_name="Accountant Display Name",
            mobile_number=invitation.mobile_number,
            status=AccountantRelationStatus.PENDING,
            expires_at=invitation.expires_at.replace(tzinfo=timezone.utc),
            accountant_user_id=None,
            deleted_at=None,
        )

        with patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(relation, None))
        ), patch.object(registration, "release_invitation_identity", new=AsyncMock()), patch.object(
            registration, "ensure_mandatory_channel_membership", new=AsyncMock()
        ), patch.object(registration, "enqueue_project_user_joined_telegram_outbox", new=AsyncMock()):
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_web(
                    invitation_token=invitation.token,
                    address="Tehran, Accountant address",
                ),
            )

        self.assertEqual(result.user.full_name, "Accountant Display Name")
        self.assertFalse(result.user.has_bot_access)
        self.assertEqual(relation.accountant_user_id, result.user.id)
        self.assertEqual(relation.status, AccountantRelationStatus.ACTIVE)

    async def test_web_natural_key_conflict_is_deterministic_and_rolls_back(self):
        db = _FakeDB()
        invitation = _invitation()
        existing = _existing_user(user_id=44)

        with patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[existing])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, None))
        ):
            with self.assertRaises(registration.AuthoritativeRegistrationError) as raised:
                await registration.complete_invitation_registration(
                    db,
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address="Tehran, Stage Two address",
                    ),
                )

        self.assertEqual(raised.exception.outcome, TelegramRegistrationOutcome.MOBILE_CONFLICT)
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()
        self.assertFalse(invitation.is_used)

    async def test_telegram_creation_finalizes_receipt_without_enabling_an_adapter(self):
        db = _FakeDB()
        invitation = _invitation()
        command = _telegram_command(invitation)
        receipt = SimpleNamespace(
            outcome_code=None,
            authoritative_user_id=None,
            completed_at=None,
        )

        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, None))
        ), patch.object(registration, "release_invitation_identity", new=AsyncMock()), patch.object(
            registration, "ensure_mandatory_channel_membership", new=AsyncMock()
        ), patch.object(
            registration, "enqueue_project_user_joined_telegram_outbox", new=AsyncMock(return_value=[])
        ):
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )

        user = result.user
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(user.telegram_id, command.telegram_id)
        self.assertEqual(user.username, command.telegram_username)
        self.assertEqual(user.full_name, command.telegram_full_name)
        self.assertEqual(user.home_server, "iran")
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.has_bot_access)
        self.assertEqual(invitation.completed_via, InvitationCompletionSurface.TELEGRAM)
        self.assertEqual(receipt.outcome_code, TelegramRegistrationOutcome.CREATED.value)
        self.assertEqual(receipt.authoritative_user_id, user.id)
        self.assertIsNotNone(receipt.completed_at)

    async def test_telegram_tier2_invitation_is_terminally_web_only(self):
        db = _FakeDB()
        invitation = _invitation(token="CUST-stage2-tier2-123", kind=InvitationKind.CUSTOMER)
        relation = CustomerRelation(
            id=31,
            owner_user_id=5,
            invitation_token=invitation.token,
            management_name="Tier Two Customer",
            customer_tier=CustomerTier.TIER_2,
            status=CustomerRelationStatus.PENDING,
            expires_at=invitation.expires_at.replace(tzinfo=timezone.utc),
            customer_user_id=None,
            deleted_at=None,
        )
        command = _telegram_command(invitation)
        receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)

        with patch.object(
            registration, "prepare_registration_command_receipt", new=AsyncMock(return_value=(receipt, False))
        ), patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, relation))
        ):
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )

        self.assertEqual(result.outcome, TelegramRegistrationOutcome.INVALID_RELATION)
        self.assertTrue(result.first_terminal_transition)
        self.assertEqual(receipt.outcome_code, TelegramRegistrationOutcome.INVALID_RELATION.value)
        self.assertFalse(invitation.is_used)
        self.assertFalse(any(isinstance(item, User) for item in db.added))

    async def test_telegram_post_expiry_boundary_is_inclusive_then_terminal(self):
        expiry = datetime.now(timezone.utc).replace(microsecond=0)
        accepted_invitation = _invitation(expires_at=expiry.replace(tzinfo=None))
        accepted_command = _telegram_command(accepted_invitation)
        accepted_receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)
        accepted_db = _FakeDB()

        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(accepted_receipt, False)),
        ), patch.object(
            registration, "_load_invitation_for_update", new=AsyncMock(return_value=accepted_invitation)
        ), patch.object(registration, "_acquire_registration_identity_locks", new=AsyncMock()), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, None))
        ), patch.object(registration, "release_invitation_identity", new=AsyncMock()), patch.object(
            registration, "ensure_mandatory_channel_membership", new=AsyncMock()
        ), patch.object(
            registration, "enqueue_project_user_joined_telegram_outbox", new=AsyncMock(return_value=[])
        ):
            accepted = await registration.complete_invitation_registration(
                accepted_db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=accepted_command,
                    source_server=SERVER_FOREIGN,
                    received_at=expiry + timedelta(seconds=86_400),
                ),
            )

        self.assertEqual(accepted.outcome, TelegramRegistrationOutcome.CREATED)

        expired_invitation = _invitation(expires_at=expiry.replace(tzinfo=None))
        expired_command = _telegram_command(expired_invitation)
        expired_receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)
        expired_db = _FakeDB()
        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(expired_receipt, False)),
        ), patch.object(
            registration, "_load_invitation_for_update", new=AsyncMock(return_value=expired_invitation)
        ):
            expired = await registration.complete_invitation_registration(
                expired_db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=expired_command,
                    source_server=SERVER_FOREIGN,
                    received_at=expiry + timedelta(seconds=86_400, microseconds=1),
                ),
            )

        self.assertEqual(expired.outcome, TelegramRegistrationOutcome.INVITATION_EXPIRED)
        self.assertEqual(expired_receipt.outcome_code, TelegramRegistrationOutcome.INVITATION_EXPIRED.value)
        self.assertFalse(expired_invitation.is_used)

    async def test_telegram_links_web_winner_without_overwriting_web_profile(self):
        db = _FakeDB()
        existing = _existing_user()
        invitation = _invitation(
            is_used=True,
            registered_user_id=existing.id,
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            completed_via=InvitationCompletionSurface.WEB,
        )
        command = _telegram_command(invitation)
        receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)

        with patch.object(
            registration, "prepare_registration_command_receipt", new=AsyncMock(return_value=(receipt, False))
        ), patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[existing])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, None))
        ), patch.object(
            registration, "_validate_current_telegram_eligibility", new=AsyncMock()
        ), patch.object(
            registration, "ensure_mandatory_channel_membership", new=AsyncMock()
        ) as membership_mock, patch.object(
            registration, "enqueue_project_user_joined_telegram_outbox", new=AsyncMock()
        ) as enqueue_mock:
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )

        self.assertEqual(result.outcome, TelegramRegistrationOutcome.LINKED_EXISTING)
        self.assertEqual(existing.telegram_id, command.telegram_id)
        self.assertEqual(existing.username, command.telegram_username)
        self.assertEqual(existing.full_name, "Authoritative Web Profile")
        self.assertEqual(existing.address, "Authoritative Web Address")
        self.assertEqual(invitation.completed_via, InvitationCompletionSurface.WEB)
        self.assertTrue(existing.has_bot_access)
        membership_mock.assert_awaited_once_with(db, user=existing)
        enqueue_mock.assert_not_awaited()
        self.assertEqual(receipt.outcome_code, TelegramRegistrationOutcome.LINKED_EXISTING.value)

    async def test_web_retry_recovers_only_same_completed_web_payload(self):
        existing = _existing_user()
        invitation = _invitation(
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
            is_used=True,
            registered_user_id=existing.id,
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            completed_via=InvitationCompletionSurface.WEB,
        )
        with patch.object(
            registration,
            "_load_invitation_for_update",
            new=AsyncMock(return_value=invitation),
        ), patch.object(
            registration,
            "_acquire_registration_identity_locks",
            new=AsyncMock(),
        ), patch.object(
            registration,
            "_load_matching_users_for_update",
            new=AsyncMock(return_value=[existing]),
        ), patch.object(
            registration,
            "_load_relation_for_registration",
            new=AsyncMock(return_value=(None, None)),
        ):
            result = await registration.complete_invitation_registration(
                _FakeDB(),
                registration.AuthoritativeRegistrationRequest.for_web(
                    invitation_token=invitation.token,
                    address=existing.address,
                ),
            )

        self.assertTrue(result.replayed)
        self.assertFalse(result.first_terminal_transition)
        self.assertFalse(result.announce_project_user)
        self.assertIs(result.user, existing)

        invitation.completed_via = InvitationCompletionSurface.TELEGRAM
        with patch.object(
            registration,
            "_load_invitation_for_update",
            new=AsyncMock(return_value=invitation),
        ):
            with self.assertRaises(registration.AuthoritativeRegistrationError):
                await registration.complete_invitation_registration(
                    _FakeDB(),
                    registration.AuthoritativeRegistrationRequest.for_web(
                        invitation_token=invitation.token,
                        address=existing.address,
                    ),
                )

    async def test_telegram_receipt_replay_returns_prior_result_before_invitation_lock(self):
        db = _FakeDB()
        invitation = _invitation()
        command = _telegram_command(invitation)
        receipt = SimpleNamespace(
            outcome_code=TelegramRegistrationOutcome.CREATED.value,
            authoritative_user_id=91,
            completed_at=datetime.now(timezone.utc),
        )

        with patch.object(
            registration, "prepare_registration_command_receipt", new=AsyncMock(return_value=(receipt, True))
        ), patch.object(registration, "_load_invitation_for_update", new=AsyncMock()) as invitation_loader:
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )

        self.assertTrue(result.replayed)
        self.assertFalse(result.first_terminal_transition)
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.CREATED)
        self.assertEqual(result.authoritative_user_id, 91)
        invitation_loader.assert_not_awaited()
        db.commit.assert_awaited_once()

    async def test_changed_payload_replay_is_terminal_without_mutation(self):
        db = _FakeDB()
        invitation = _invitation()
        command = _telegram_command(invitation)

        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(
                side_effect=RegistrationCommandReplayConflict(
                    TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY.value
                )
            ),
        ):
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )

        self.assertEqual(result.outcome, TelegramRegistrationOutcome.CHANGED_PAYLOAD_REPLAY)
        self.assertTrue(result.replayed)
        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()

    async def test_outbox_failure_rolls_back_user_invitation_and_receipt_transaction(self):
        db = _FakeDB()
        invitation = _invitation()
        command = _telegram_command(invitation)
        receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)

        with patch.object(
            registration, "prepare_registration_command_receipt", new=AsyncMock(return_value=(receipt, False))
        ), patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration, "_load_relation_for_registration", new=AsyncMock(return_value=(None, None))
        ), patch.object(registration, "release_invitation_identity", new=AsyncMock()), patch.object(
            registration, "ensure_mandatory_channel_membership", new=AsyncMock()
        ), patch.object(
            registration,
            "enqueue_project_user_joined_telegram_outbox",
            new=AsyncMock(side_effect=RuntimeError("outbox unavailable")),
        ):
            with self.assertRaisesRegex(RuntimeError, "outbox unavailable"):
                await registration.complete_invitation_registration(
                    db,
                    registration.AuthoritativeRegistrationRequest.for_telegram(
                        command=command,
                        source_server=SERVER_FOREIGN,
                        received_at=command.local_completed_at,
                    ),
                )

        db.rollback.assert_awaited_once()
        db.commit.assert_not_awaited()
        self.assertIsNone(receipt.outcome_code)

    async def test_revoked_telegram_command_commits_one_terminal_receipt_only(self):
        db = _FakeDB()
        invitation = _invitation(revoked_at=datetime.now(timezone.utc))
        command = _telegram_command(invitation)
        receipt = SimpleNamespace(outcome_code=None, authoritative_user_id=None, completed_at=None)

        with patch.object(
            registration, "prepare_registration_command_receipt", new=AsyncMock(return_value=(receipt, False))
        ), patch.object(registration, "_load_invitation_for_update", new=AsyncMock(return_value=invitation)):
            result = await registration.complete_invitation_registration(
                db,
                registration.AuthoritativeRegistrationRequest.for_telegram(
                    command=command,
                    source_server=SERVER_FOREIGN,
                    received_at=command.local_completed_at,
                ),
            )

        self.assertEqual(result.outcome, TelegramRegistrationOutcome.INVITATION_REVOKED)
        self.assertTrue(result.first_terminal_transition)
        self.assertEqual(receipt.outcome_code, TelegramRegistrationOutcome.INVITATION_REVOKED.value)
        self.assertIsNone(receipt.authoritative_user_id)
        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
