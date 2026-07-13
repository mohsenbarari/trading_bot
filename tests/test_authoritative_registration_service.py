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

    def test_stage9_validation_boundaries_fail_closed_without_database_mutation(self):
        self.assertFalse(registration._same_utc_instant(None, datetime.now(timezone.utc)))

        for inv in (
            _invitation(token="INV-invalid-kind"),
            _invitation(token="CUST-kind-mismatch", kind=InvitationKind.STANDARD),
            _invitation(token="INV-legacy", kind=InvitationKind.LEGACY_UNKNOWN),
        ):
            if inv.token == "INV-invalid-kind":
                inv.kind = "future-kind"
            with self.subTest(token=inv.token), self.assertRaises(
                registration.AuthoritativeRegistrationError
            ):
                registration._validate_invitation_kind(inv)

        inv = _invitation()
        command = _telegram_command(inv)
        request = registration.AuthoritativeRegistrationRequest.for_telegram(
            command=command,
            source_server=SERVER_FOREIGN,
        )
        inv.expires_at = inv.expires_at + timedelta(seconds=1)
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._validate_invitation_time(request, inv)

        inv = _invitation()
        command = _telegram_command(inv)
        command = command.model_copy(
            update={"local_completed_at": command.invitation_expires_at_snapshot + timedelta(seconds=1)}
        )
        request = registration.AuthoritativeRegistrationRequest.for_telegram(
            command=command,
            source_server=SERVER_FOREIGN,
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._validate_invitation_time(request, inv)

        identity = registration.normalize_invitation_identity(
            mobile_number="09120000001",
            account_name="stage2_user",
        )
        invalid_accountant = SimpleNamespace(
            mobile_number="invalid",
            global_account_name="",
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._validate_relation_contract(
                invitation=_invitation(
                    token="ACCT-invalid-relation",
                    kind=InvitationKind.ACCOUNTANT,
                    role=UserRole.WATCH,
                ),
                identity=identity,
                accountant_relation=invalid_accountant,
                customer_relation=None,
            )

        pending_accountant_invitation = _invitation(
            token="ACCT-pending-invalid",
            kind=InvitationKind.ACCOUNTANT,
            role=UserRole.WATCH,
        )
        pending_accountant = AccountantRelation(
            owner_user_id=5,
            created_by_user_id=5,
            invitation_token=pending_accountant_invitation.token,
            global_account_name=pending_accountant_invitation.account_name,
            relation_display_name="Accountant",
            mobile_number=pending_accountant_invitation.mobile_number,
            status=AccountantRelationStatus.ACTIVE,
            expires_at=pending_accountant_invitation.expires_at,
        )
        web_request = registration.AuthoritativeRegistrationRequest.for_web(
            invitation_token=pending_accountant_invitation.token,
            address="Tehran valid address",
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._validate_pending_relation(
                request=web_request,
                invitation=pending_accountant_invitation,
                identity=identity,
                accountant_relation=pending_accountant,
                customer_relation=None,
            )

        with patch.object(
            registration,
            "evaluate_invitation_bot_access",
            return_value=SimpleNamespace(allowed=False),
        ), self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._validate_telegram_projection_eligibility(
                invitation=_invitation(),
                accountant_relation=None,
                customer_relation=None,
            )

    async def test_stage9_relation_loading_and_current_projection_cardinality(self):
        cases = (
            (InvitationKind.ACCOUNTANT, None, None),
            (InvitationKind.ACCOUNTANT, object(), object()),
            (InvitationKind.CUSTOMER, None, None),
            (InvitationKind.CUSTOMER, object(), object()),
        )
        for kind, accountant, customer in cases:
            with self.subTest(kind=kind, accountant=accountant), patch.object(
                registration,
                "lock_accountant_relation_for_registration",
                new=AsyncMock(return_value=accountant),
            ), patch.object(
                registration,
                "lock_customer_relation_for_registration",
                new=AsyncMock(return_value=customer),
            ), self.assertRaises(registration.AuthoritativeRegistrationError):
                await registration._load_relation_for_registration(
                    _FakeDB(),
                    invitation=_invitation(kind=kind),
                    kind=kind,
                )

        class Rows:
            def __init__(self, values):
                self.values = values

            def scalars(self):
                return SimpleNamespace(all=lambda: self.values)

        user = _existing_user()
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[Rows([object(), object()]), Rows([])])
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            await registration._validate_current_telegram_eligibility(db, user=user)

        accountant = object()
        with patch.object(
            registration,
            "lock_accountant_relation_for_registration",
            new=AsyncMock(return_value=accountant),
        ), patch.object(
            registration,
            "lock_customer_relation_for_registration",
            new=AsyncMock(return_value=None),
        ):
            loaded = await registration._load_relation_for_registration(
                _FakeDB(),
                invitation=_invitation(kind=InvitationKind.ACCOUNTANT),
                kind=InvitationKind.ACCOUNTANT,
            )
        self.assertEqual(loaded, (accountant, None))

    async def test_integrity_conflict_receipt_recovery_is_deterministic(self):
        inv = _invitation()
        command = _telegram_command(inv)
        request = registration.AuthoritativeRegistrationRequest.for_telegram(
            command=command,
            source_server=SERVER_FOREIGN,
        )
        conflict = registration._error(
            TelegramRegistrationOutcome.MOBILE_CONFLICT,
            "conflict",
        )

        web_request = registration.AuthoritativeRegistrationRequest.for_web(
            invitation_token=inv.token,
            address="Tehran valid address",
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            await registration._persist_integrity_conflict_receipt(
                _FakeDB(),
                request=web_request,
                conflict=conflict,
                checkpoint=None,
            )

        replay_receipt = SimpleNamespace(
            completed_at=datetime.now(timezone.utc),
            outcome_code=TelegramRegistrationOutcome.MOBILE_CONFLICT.value,
            authoritative_user_id=None,
        )
        replay_db = _FakeDB()
        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(replay_receipt, True)),
        ):
            replay = await registration._persist_integrity_conflict_receipt(
                replay_db,
                request=request,
                conflict=conflict,
                checkpoint=None,
            )
        self.assertTrue(replay.replayed)
        replay_db.commit.assert_awaited_once()

        for invitation_value, expected in (
            (None, TelegramRegistrationOutcome.INVITATION_NOT_FOUND),
            (
                _invitation(revoked_at=datetime.now(timezone.utc).replace(tzinfo=None)),
                TelegramRegistrationOutcome.INVITATION_REVOKED,
            ),
        ):
            receipt = SimpleNamespace(completed_at=None, outcome_code=None)
            db = _FakeDB()
            with self.subTest(expected=expected), patch.object(
                registration,
                "prepare_registration_command_receipt",
                new=AsyncMock(return_value=(receipt, False)),
            ), patch.object(
                registration,
                "_load_invitation_for_update",
                new=AsyncMock(return_value=invitation_value),
            ), patch.object(
                registration, "finalize_registration_command_receipt"
            ) as finalize:
                result = await registration._persist_integrity_conflict_receipt(
                    db,
                    request=request,
                    conflict=conflict,
                    checkpoint=None,
                )
            self.assertEqual(result.outcome, expected)
            finalize.assert_called_once()

        completed = _invitation(
            is_used=True,
            registered_user_id=55,
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            completed_via=InvitationCompletionSurface.WEB,
        )
        for telegram_id, expected, expected_user_id in (
            (command.telegram_id, TelegramRegistrationOutcome.ALREADY_LINKED, 55),
            (999, TelegramRegistrationOutcome.TELEGRAM_ACCOUNT_CONFLICT, None),
            (None, TelegramRegistrationOutcome.MOBILE_CONFLICT, None),
        ):
            user = _existing_user(telegram_id=telegram_id)
            receipt = SimpleNamespace(completed_at=None, outcome_code=None)
            with self.subTest(expected=expected), patch.object(
                registration,
                "prepare_registration_command_receipt",
                new=AsyncMock(return_value=(receipt, False)),
            ), patch.object(
                registration,
                "_load_invitation_for_update",
                new=AsyncMock(return_value=completed),
            ), patch.object(
                registration, "_acquire_registration_identity_locks", new=AsyncMock()
            ), patch.object(
                registration,
                "_load_matching_users_for_update",
                new=AsyncMock(return_value=[user]),
            ), patch.object(registration, "finalize_registration_command_receipt"):
                result = await registration._persist_integrity_conflict_receipt(
                    _FakeDB(),
                    request=request,
                    conflict=conflict,
                    checkpoint=None,
                )
            self.assertEqual(result.outcome, expected)
            self.assertEqual(result.authoritative_user_id, expected_user_id)

        deleted_user = _existing_user(telegram_id=command.telegram_id)
        deleted_user.is_deleted = True
        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(SimpleNamespace(completed_at=None, outcome_code=None), False)),
        ), patch.object(
            registration,
            "_load_invitation_for_update",
            new=AsyncMock(return_value=completed),
        ), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration,
            "_load_matching_users_for_update",
            new=AsyncMock(return_value=[deleted_user]),
        ), patch.object(registration, "finalize_registration_command_receipt"):
            deleted_result = await registration._persist_integrity_conflict_receipt(
                _FakeDB(),
                request=request,
                conflict=conflict,
                checkpoint=None,
            )
        self.assertEqual(deleted_result.outcome, TelegramRegistrationOutcome.ACCOUNT_DELETED)
        self.assertIsNone(deleted_result.authoritative_user_id)

        pending = _invitation()
        receipt = SimpleNamespace(completed_at=None, outcome_code=None)
        current_conflict = registration._error(
            TelegramRegistrationOutcome.ACCOUNT_NAME_CONFLICT,
            "conflict",
        )
        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(
            registration,
            "_load_invitation_for_update",
            new=AsyncMock(return_value=pending),
        ), patch.object(
            registration, "_acquire_registration_identity_locks", new=AsyncMock()
        ), patch.object(
            registration, "_load_matching_users_for_update", new=AsyncMock(return_value=[])
        ), patch.object(
            registration,
            "_validate_pending_natural_keys",
            side_effect=current_conflict,
        ), patch.object(registration, "finalize_registration_command_receipt"):
            result = await registration._persist_integrity_conflict_receipt(
                _FakeDB(),
                request=request,
                conflict=conflict,
                checkpoint=None,
            )
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.ACCOUNT_NAME_CONFLICT)

        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(
            registration,
            "_load_invitation_for_update",
            new=AsyncMock(return_value=pending),
        ), patch.object(
            registration,
            "normalize_invitation_identity",
            side_effect=ValueError("bad"),
        ), patch.object(registration, "finalize_registration_command_receipt"):
            result = await registration._persist_integrity_conflict_receipt(
                _FakeDB(),
                request=request,
                conflict=conflict,
                checkpoint=None,
            )
        self.assertEqual(result.outcome, TelegramRegistrationOutcome.LEGACY_STATE_AMBIGUOUS)

    async def test_complete_registration_terminal_guards_and_integrity_recovery(self):
        missing_db = _FakeDB()
        web_request = registration.AuthoritativeRegistrationRequest.for_web(
            invitation_token="INV-missing",
            address="Tehran valid address",
        )
        with patch.object(
            registration, "_load_invitation_for_update", new=AsyncMock(return_value=None)
        ), self.assertRaises(registration.AuthoritativeRegistrationError) as exc:
            await registration.complete_invitation_registration(missing_db, web_request)
        self.assertEqual(exc.exception.outcome, TelegramRegistrationOutcome.INVITATION_NOT_FOUND)
        missing_db.rollback.assert_awaited_once()

        inv = _invitation()
        command = _telegram_command(inv).model_copy(update={"mobile_number": "09129999999"})
        telegram_request = registration.AuthoritativeRegistrationRequest.for_telegram(
            command=command,
            source_server=SERVER_FOREIGN,
        )
        receipt = SimpleNamespace(completed_at=None, outcome_code=None)
        mismatch_db = _FakeDB()
        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(receipt, False)),
        ), patch.object(
            registration, "_load_invitation_for_update", new=AsyncMock(return_value=inv)
        ), patch.object(registration, "finalize_registration_command_receipt"):
            mismatch = await registration.complete_invitation_registration(
                mismatch_db,
                telegram_request,
            )
        self.assertEqual(mismatch.outcome, TelegramRegistrationOutcome.CONTACT_MOBILE_MISMATCH)

        completed = _invitation(
            is_used=True,
            registered_user_id=55,
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            completed_via=InvitationCompletionSurface.WEB,
        )
        completed_command = _telegram_command(completed)
        completed_request = registration.AuthoritativeRegistrationRequest.for_telegram(
            command=completed_command,
            source_server=SERVER_FOREIGN,
        )
        for duplicate, expected in (
            (False, TelegramRegistrationOutcome.ALREADY_LINKED),
            (True, TelegramRegistrationOutcome.TELEGRAM_ID_ALREADY_USED),
        ):
            registered = _existing_user(telegram_id=completed_command.telegram_id)
            users = [registered]
            if duplicate:
                users.append(_existing_user(user_id=56, telegram_id=completed_command.telegram_id))
            db = _FakeDB()
            receipt = SimpleNamespace(completed_at=None, outcome_code=None)
            with self.subTest(duplicate=duplicate), patch.object(
                registration,
                "prepare_registration_command_receipt",
                new=AsyncMock(return_value=(receipt, False)),
            ), patch.object(
                registration,
                "_load_invitation_for_update",
                new=AsyncMock(return_value=completed),
            ), patch.object(
                registration, "_acquire_registration_identity_locks", new=AsyncMock()
            ), patch.object(
                registration,
                "_load_matching_users_for_update",
                new=AsyncMock(return_value=users),
            ), patch.object(
                registration,
                "_load_relation_for_registration",
                new=AsyncMock(return_value=(None, None)),
            ), patch.object(registration, "_validate_completed_relation"), patch.object(
                registration, "_validate_telegram_projection_eligibility"
            ), patch.object(
                registration, "validate_current_telegram_eligibility", new=AsyncMock()
            ), patch.object(registration, "finalize_registration_command_receipt"):
                result = await registration.complete_invitation_registration(
                    db,
                    completed_request,
                )
            self.assertEqual(result.outcome, expected)

        failing_db = _FakeDB()
        failing_db.flush = AsyncMock(side_effect=RuntimeError("outbox failed"))
        with patch.object(
            registration,
            "prepare_registration_command_receipt",
            new=AsyncMock(return_value=(SimpleNamespace(completed_at=None, outcome_code=None), False)),
        ), patch.object(
            registration, "_load_invitation_for_update", new=AsyncMock(return_value=inv)
        ), patch.object(registration, "finalize_registration_command_receipt"):
            with self.assertRaisesRegex(RuntimeError, "outbox failed"):
                await registration.complete_invitation_registration(
                    failing_db,
                    telegram_request,
                )
        self.assertGreaterEqual(failing_db.rollback.await_count, 1)

        integrity_error = registration.IntegrityError("insert", {}, RuntimeError("constraint"))
        conflict = registration._error(
            TelegramRegistrationOutcome.MOBILE_CONFLICT,
            "conflict",
        )
        for request, detected_conflict, persist_error, expected_exception in (
            (web_request, conflict, None, registration.AuthoritativeRegistrationError),
            (web_request, None, None, registration.IntegrityError),
            (completed_request, conflict, RuntimeError("recovery failed"), RuntimeError),
        ):
            db = _FakeDB()
            with self.subTest(request=request.source_surface), patch.object(
                registration,
                "prepare_registration_command_receipt",
                new=AsyncMock(return_value=(SimpleNamespace(completed_at=None, outcome_code=None), False)),
            ), patch.object(
                registration,
                "_load_invitation_for_update",
                new=AsyncMock(side_effect=integrity_error),
            ), patch.object(
                registration, "_integrity_conflict", return_value=detected_conflict
            ), patch.object(
                registration,
                "_persist_integrity_conflict_receipt",
                new=AsyncMock(side_effect=persist_error),
            ):
                with self.assertRaises(expected_exception):
                    await registration.complete_invitation_registration(db, request)
            self.assertGreaterEqual(db.rollback.await_count, 1)

    def test_stage9_identity_completion_receipt_and_constraint_boundaries(self):
        identity = registration.normalize_invitation_identity(
            mobile_number="09120000001",
            account_name="stage2_user",
        )
        unrelated = _existing_user()
        unrelated.account_name = "different"
        unrelated.mobile_number = "09129999999"
        with self.assertRaises(registration.AuthoritativeRegistrationError) as exc:
            registration._validate_pending_natural_keys(
                [unrelated],
                identity=identity,
                telegram_id=999,
            )
        self.assertEqual(exc.exception.outcome, TelegramRegistrationOutcome.IDENTITY_CONFLICT)

        incomplete = _invitation(is_used=True)
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._registered_user_for_completed_invitation(
                incomplete,
                [],
                identity=identity,
            )

        completed = _invitation(
            is_used=True,
            registered_user_id=55,
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            completed_via=InvitationCompletionSurface.WEB,
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._registered_user_for_completed_invitation(
                completed,
                [],
                identity=identity,
            )
        malformed_user = _existing_user()
        malformed_user.mobile_number = "invalid"
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._registered_user_for_completed_invitation(
                completed,
                [malformed_user],
                identity=identity,
            )
        drifted_user = _existing_user()
        drifted_user.account_name = "different"
        with self.assertRaises(registration.AuthoritativeRegistrationError):
            registration._registered_user_for_completed_invitation(
                completed,
                [drifted_user],
                identity=identity,
            )

        for relation_name in ("accountant", "customer"):
            inv = _invitation(
                token="ACCT-completed" if relation_name == "accountant" else "CUST-completed",
                kind=InvitationKind.ACCOUNTANT if relation_name == "accountant" else InvitationKind.CUSTOMER,
                role=UserRole.WATCH if relation_name == "accountant" else UserRole.STANDARD,
            )
            user = _existing_user()
            if relation_name == "accountant":
                relation = AccountantRelation(
                    owner_user_id=5,
                    created_by_user_id=5,
                    invitation_token=inv.token,
                    global_account_name=inv.account_name,
                    relation_display_name="Accountant",
                    mobile_number=inv.mobile_number,
                    status=AccountantRelationStatus.PENDING,
                    expires_at=inv.expires_at,
                )
                kwargs = {"accountant_relation": relation, "customer_relation": None}
            else:
                relation = CustomerRelation(
                    owner_user_id=5,
                    created_by_user_id=5,
                    invitation_token=inv.token,
                    management_name="Customer",
                    customer_tier=CustomerTier.TIER_1,
                    status=CustomerRelationStatus.PENDING,
                    expires_at=inv.expires_at,
                )
                kwargs = {"accountant_relation": None, "customer_relation": relation}
            with self.subTest(relation=relation_name), self.assertRaises(
                registration.AuthoritativeRegistrationError
            ):
                registration._validate_completed_relation(
                    invitation=inv,
                    identity=identity,
                    user=user,
                    **kwargs,
                )

        diagnostic = SimpleNamespace(constraint_name="ux_users_normalized_mobile_number")
        orig = SimpleNamespace(diag=diagnostic, constraint_name=None)
        error = registration.IntegrityError("insert", {}, orig)
        self.assertEqual(registration._constraint_name(error), diagnostic.constraint_name)
        self.assertEqual(
            registration._integrity_conflict(error).outcome,
            TelegramRegistrationOutcome.MOBILE_CONFLICT,
        )
        unknown = registration.IntegrityError("insert", {}, SimpleNamespace())
        self.assertIsNone(registration._constraint_name(unknown))
        self.assertIsNone(registration._integrity_conflict(unknown))

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            registration._receipt_replay_result(
                SimpleNamespace(outcome_code=None, completed_at=None)
            )
        with self.assertRaisesRegex(RuntimeError, "outcome_invalid"):
            registration._receipt_replay_result(
                SimpleNamespace(
                    outcome_code="future",
                    completed_at=datetime.now(timezone.utc),
                )
            )

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

        valid_invitation, _, _ = accountant_projection()
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
        invitation_sql = [
            str(call[0].compile(dialect=postgresql.dialect())).upper()
            for call in invitation_db.calls
        ]
        row_lock_index = next(
            index for index, sql in enumerate(invitation_sql) if "FOR UPDATE" in sql
        )
        advisory_index = next(
            index for index, sql in enumerate(invitation_sql) if "PG_ADVISORY_XACT_LOCK" in sql
        )
        self.assertLess(advisory_index, row_lock_index)

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
        self.assertEqual(user.full_name, invitation.account_name)
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
