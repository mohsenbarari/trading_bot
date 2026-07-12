from __future__ import annotations

import unittest
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from core.registration_contracts import (
    REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE,
    RegistrationIdentityProofType,
    RegistrationSourceSurface,
    TelegramRegistrationCommand,
)
from core.registration_identity import _checked_sql_identifier
from core.registration_sync_policy import (
    USER_SYNC_FOREIGN_FIELDS,
    USER_SYNC_IDENTITY_FIELDS,
    USER_SYNC_METADATA_FIELDS,
    USER_SYNC_SHARED_FIELDS,
    allowed_user_fields_for_source,
)
from core.background_job_authority import (
    BackgroundJobAuthorityDecision,
    JOB_OTP_SMS_FALLBACK,
    JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
    check_background_job_authority,
)
from core.invitation_sms_policy import invitation_sms_enabled
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from core.services import authoritative_registration_service as registration
from core.services import telegram_registration_intent_service as intent_service
from core.services.canonical_invitation_creation_service import _is_exact_base_retry
from core.services.otp_delivery_state_service import (
    OTP_SMS_MINIMUM_SEND_TTL_SECONDS,
    OTP_FALLBACK_DUE_KEY,
    _CLAIM_SCRIPT,
    _CONSUME_SCRIPT,
    _mobile_request_key,
    _state_key,
    build_otp_delivery_state,
    claim_sms_delivery,
    consume_otp_code,
)
from core.services.registration_command_receipt_service import registration_command_lock_keys
from core.services.telegram_registration_intent_service import _intent_matches_command
from core.enums import UserAccountStatus
from models.customer_relation import CustomerTier
from models.invitation import InvitationKind
from models.user import UserRole


def _command() -> TelegramRegistrationCommand:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return TelegramRegistrationCommand(
        command_id=uuid4(),
        idempotency_key="stage9-mutation-command",
        invitation_token="USER-stage9-mutation-token",
        mobile_number="09121112233",
        telegram_id=9121112233,
        telegram_username="stage9-user",
        telegram_full_name="Stage Nine User",
        address="Stage nine valid address",
        contact_verified_at=now - timedelta(seconds=1),
        local_completed_at=now,
        invitation_expires_at_snapshot=now + timedelta(days=2),
    )


def _intent_for(command: TelegramRegistrationCommand) -> SimpleNamespace:
    return SimpleNamespace(
        id=command.command_id,
        idempotency_key=command.idempotency_key,
        invitation_token=command.invitation_token,
        normalized_mobile=command.mobile_number,
        telegram_id=command.telegram_id,
        telegram_username=command.telegram_username,
        telegram_full_name=command.telegram_full_name,
        address=command.address,
        contact_verified_at=command.contact_verified_at,
        completed_at=command.local_completed_at,
        invitation_expires_at_snapshot=command.invitation_expires_at_snapshot,
    )


class _ProjectionRows:
    def __init__(self, values):
        self.values = list(values)

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


def _projection_db(*rows):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=tuple(_ProjectionRows(values) for values in rows))
    )


def _canonical_sql(statement) -> str:
    return " ".join(str(statement).split())


class Stage9MutationContractTests(unittest.IsolatedAsyncioTestCase):
    def test_sql_identifier_guard_accepts_only_simple_or_qualified_identifiers(self):
        for value in ("mobile", "users.mobile", "_private", "Table_1.Column2"):
            with self.subTest(valid=value):
                self.assertEqual(_checked_sql_identifier(value), value)
        for value in (
            "",
            "9mobile",
            ".mobile",
            "users.",
            "users.mobile.extra",
            "users-mobile",
            "users mobile",
            "users.mobile;drop table users",
            'users."mobile"',
        ):
            with self.subTest(invalid=value):
                with self.assertRaises(ValueError) as raised:
                    _checked_sql_identifier(value)
                self.assertEqual(
                    str(raised.exception),
                    "canonical identity SQL requires a simple or qualified column identifier",
                )

    def test_registration_request_shape_guards_every_surface_boundary(self):
        now = datetime.now(timezone.utc)
        valid_web = registration.AuthoritativeRegistrationRequest(
            invitation_token="USER-stage9",
            source_surface=RegistrationSourceSurface.WEBAPP,
            identity_proof_type=RegistrationIdentityProofType.WEB_OTP,
            address="Stage nine valid address",
            received_at=now,
        )
        with patch.object(registration, "current_server", return_value=SERVER_IRAN):
            self.assertIsNone(registration._validate_request_shape(valid_web))

            class _IndeterminateTimezone(tzinfo):
                def utcoffset(self, dt):
                    return None

                def dst(self, dt):
                    return None

            invalid_cases = (
                (replace(valid_web, invitation_token=""), ValueError, "invitation_token_required"),
                (replace(valid_web, address="short"), ValueError, REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE),
                (replace(valid_web, received_at=now.replace(tzinfo=None)), ValueError, "received_at_timezone_required"),
                (replace(valid_web, received_at=now.replace(tzinfo=_IndeterminateTimezone())), ValueError, "received_at_timezone_required"),
                (replace(valid_web, identity_proof_type=RegistrationIdentityProofType.TELEGRAM_CONTACT), ValueError, "web_identity_proof_invalid"),
                (replace(valid_web, telegram_command=object()), ValueError, "web_request_contains_telegram_context"),
                (replace(valid_web, source_server=SERVER_FOREIGN), ValueError, "web_request_contains_telegram_context"),
                (replace(valid_web, source_surface="unknown"), ValueError, "registration_source_invalid"),
            )
            for request, exception_type, expected in invalid_cases:
                with self.subTest(expected=expected):
                    with self.assertRaises(exception_type) as raised:
                        registration._validate_request_shape(request)
                    self.assertEqual(str(raised.exception), expected)

            valid_telegram = registration.AuthoritativeRegistrationRequest(
                invitation_token="USER-stage9",
                source_surface=RegistrationSourceSurface.TELEGRAM_BOT,
                identity_proof_type=RegistrationIdentityProofType.TELEGRAM_CONTACT,
                address="Stage nine valid address",
                received_at=now,
                telegram_command=object(),
                source_server=SERVER_FOREIGN,
            )
            self.assertIsNone(registration._validate_request_shape(valid_telegram))
            for updates, expected in (
                ({"identity_proof_type": RegistrationIdentityProofType.WEB_OTP}, "telegram_identity_proof_invalid"),
                ({"telegram_command": None}, "telegram_command_required"),
            ):
                request = replace(valid_telegram, **updates)
                with self.assertRaises(ValueError) as raised:
                    registration._validate_request_shape(request)
                self.assertEqual(str(raised.exception), expected)

        with patch.object(registration, "current_server", return_value=SERVER_FOREIGN):
            with self.assertRaises(RuntimeError) as raised:
                registration._validate_request_shape(valid_web)
            self.assertEqual(str(raised.exception), "authoritative_registration_requires_iran")

    def test_exact_invitation_retry_requires_every_natural_and_policy_key(self):
        invitation = SimpleNamespace(
            created_by_id=17,
            kind=InvitationKind.STANDARD,
            role=UserRole.STANDARD,
            mobile_number="۰۹۱۲۱۱۱۲۲۳۳",
            account_name="Stage9Account",
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        )
        kwargs = {
            "creator_user_id": 17,
            "kind": InvitationKind.STANDARD,
            "role": UserRole.STANDARD,
            "normalized_mobile": "09121112233",
            "normalized_account_name": "stage9account",
        }
        self.assertTrue(_is_exact_base_retry(invitation, **kwargs))
        variations = (
            {"creator_user_id": 18},
            {"kind": InvitationKind.ACCOUNTANT},
            {"role": UserRole.SUPER_ADMIN},
            {"normalized_mobile": "09121112234"},
            {"normalized_account_name": "different"},
        )
        for changes in variations:
            with self.subTest(changes=changes):
                self.assertFalse(_is_exact_base_retry(invitation, **{**kwargs, **changes}))
        invitation.revoked_at = datetime.now(timezone.utc)
        self.assertFalse(_is_exact_base_retry(invitation, **kwargs))
        invitation.revoked_at = None
        invitation.created_by_id = None
        self.assertFalse(
            _is_exact_base_retry(invitation, **{**kwargs, "creator_user_id": 1})
        )

    def test_intent_match_ignores_only_retry_snapshots_and_rejects_business_drift(self):
        command = _command()
        intent = _intent_for(command)
        self.assertTrue(_intent_matches_command(intent, command))
        retry = command.model_copy(
            update={
                "telegram_username": "new-profile",
                "telegram_full_name": "New Profile",
                "contact_verified_at": command.contact_verified_at + timedelta(seconds=1),
                "local_completed_at": command.local_completed_at + timedelta(seconds=1),
            }
        )
        self.assertTrue(_intent_matches_command(intent, retry))
        for field, value in (
            ("idempotency_key", "changed-key"),
            ("invitation_token", "USER-other-token"),
            ("mobile_number", "09121112234"),
            ("telegram_id", command.telegram_id + 1),
            ("address", "Different valid address"),
            ("invitation_expires_at_snapshot", command.invitation_expires_at_snapshot + timedelta(seconds=1)),
        ):
            with self.subTest(field=field):
                self.assertFalse(_intent_matches_command(intent, command.model_copy(update={field: value})))
        intent.address = None
        self.assertFalse(_intent_matches_command(intent, command))

    async def test_consume_otp_normalizes_keys_and_accepts_only_atomic_one(self):
        redis = SimpleNamespace(eval=AsyncMock(side_effect=(1, 0, None, "1")))
        with patch(
            "core.services.otp_delivery_state_service.settings.otp_delivery_state_secret",
            "stage9-mutation-state-secret-0123456789",
        ):
            outcomes = [
                await consume_otp_code(redis, mobile="۰۹۱۲۱۱۱۲۲۳۳", expected_code="12345")
                for _ in range(4)
            ]
            mobile_request_key = _mobile_request_key("09121112233")
        self.assertEqual(outcomes, [True, False, False, True])
        expected_args = (
            _CONSUME_SCRIPT,
            5,
            "otp:09121112233",
            "otp_limit:09121112233",
            "sms_limit:09121112233",
            mobile_request_key,
            OTP_FALLBACK_DUE_KEY,
            "12345",
            "otp_delivery:request:",
        )
        for call in redis.eval.await_args_list:
            self.assertEqual(call.args, expected_args)

    def test_registration_command_lock_keys_are_stable_distinct_and_ordered(self):
        command_id = uuid4()
        first = registration_command_lock_keys(
            command_id=command_id,
            idempotency_key="stage9-lock-key-0001",
        )
        second = registration_command_lock_keys(
            command_id=command_id,
            idempotency_key="stage9-lock-key-0001",
        )
        changed = registration_command_lock_keys(
            command_id=command_id,
            idempotency_key="stage9-lock-key-0002",
        )
        self.assertEqual(first, second)
        self.assertEqual(first, tuple(sorted(first)))
        self.assertEqual(len(first), 2)
        self.assertEqual(len(set(first)), 2)
        self.assertNotEqual(first, changed)
        self.assertTrue(all(value.startswith("telegram-registration:") for value in first))
        self.assertTrue(all(len(value) == len("telegram-registration:") + 64 for value in first))
        self.assertTrue(all("stage9-lock-key" not in value for value in first))
        expected = tuple(
            sorted(
                (
                    "telegram-registration:"
                    + hashlib.sha256(f"command:{command_id}".encode("utf-8")).hexdigest(),
                    "telegram-registration:"
                    + hashlib.sha256(
                        b"idempotency:stage9-lock-key-0001"
                    ).hexdigest(),
                )
            )
        )
        self.assertEqual(first, expected)

    def test_invitation_expiry_boundaries_cover_web_and_telegram_grace(self):
        expires_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        invitation = SimpleNamespace(expires_at=expires_at)
        web = registration.AuthoritativeRegistrationRequest.for_web(
            invitation_token="USER-stage9-expiry",
            address="Stage nine valid address",
            received_at=expires_at,
        )
        self.assertIsNone(registration._validate_invitation_time(web, invitation))
        with self.assertRaises(registration.AuthoritativeRegistrationError) as expired:
            registration._validate_invitation_time(
                replace(web, received_at=expires_at + timedelta(microseconds=1)),
                invitation,
            )
        self.assertEqual(expired.exception.outcome, registration.TelegramRegistrationOutcome.INVITATION_EXPIRED)
        self.assertEqual(str(expired.exception), "invitation_expired")
        self.assertEqual(expired.exception.public_detail, "دعوت‌نامه منقضی شده است")

        command = _command().model_copy(
            update={
                "invitation_expires_at_snapshot": expires_at,
                "contact_verified_at": expires_at - timedelta(seconds=2),
                "local_completed_at": expires_at,
            }
        )
        telegram = registration.AuthoritativeRegistrationRequest.for_telegram(
            command=command,
            source_server=SERVER_FOREIGN,
            received_at=expires_at + timedelta(seconds=1),
        )
        with patch.object(
            registration,
            "is_post_expiry_reconciliation_allowed",
            return_value=True,
        ) as allowed:
            self.assertIsNone(registration._validate_invitation_time(telegram, invitation))
        allowed.assert_called_once_with(
            invitation,
            proof_completed_at=command.local_completed_at,
            received_at=telegram.received_at,
            grace_seconds=registration.settings.telegram_registration_post_expiry_grace_seconds,
        )
        with patch.object(
            registration,
            "is_post_expiry_reconciliation_allowed",
            return_value=False,
        ) as denied:
            with self.assertRaises(registration.AuthoritativeRegistrationError) as reconciled_expired:
                registration._validate_invitation_time(telegram, invitation)
        self.assertEqual(
            reconciled_expired.exception.outcome,
            registration.TelegramRegistrationOutcome.INVITATION_EXPIRED,
        )
        self.assertEqual(
            str(reconciled_expired.exception),
            "invitation_expired",
        )
        self.assertEqual(
            reconciled_expired.exception.public_detail,
            "دعوت‌نامه منقضی شده است",
        )
        denied.assert_called_once_with(
            invitation,
            proof_completed_at=command.local_completed_at,
            received_at=telegram.received_at,
            grace_seconds=registration.settings.telegram_registration_post_expiry_grace_seconds,
        )

        received_before_expiry = replace(
            telegram,
            received_at=expires_at - timedelta(microseconds=1),
        )
        with patch.object(
            registration,
            "is_post_expiry_reconciliation_allowed",
        ) as unused:
            self.assertIsNone(
                registration._validate_invitation_time(received_before_expiry, invitation)
            )
        unused.assert_not_called()

        received_at_expiry = replace(telegram, received_at=expires_at)
        with patch.object(
            registration,
            "is_post_expiry_reconciliation_allowed",
        ) as exact_boundary_unused:
            self.assertIsNone(
                registration._validate_invitation_time(received_at_expiry, invitation)
            )
        exact_boundary_unused.assert_not_called()

        completed_after_expiry = replace(
            telegram,
            telegram_command=command.model_copy(
                update={"local_completed_at": expires_at + timedelta(microseconds=1)}
            ),
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError) as locally_expired:
            registration._validate_invitation_time(completed_after_expiry, invitation)
        self.assertEqual(
            locally_expired.exception.outcome,
            registration.TelegramRegistrationOutcome.INVITATION_EXPIRED,
        )
        self.assertEqual(
            str(locally_expired.exception),
            "invitation_expired",
        )
        self.assertEqual(locally_expired.exception.public_detail, "دعوت‌نامه منقضی شده است")

        mismatched = replace(
            telegram,
            telegram_command=command.model_copy(
                update={"invitation_expires_at_snapshot": expires_at + timedelta(seconds=1)}
            ),
        )
        with self.assertRaises(registration.AuthoritativeRegistrationError) as invalid:
            registration._validate_invitation_time(mismatched, invitation)
        self.assertEqual(invalid.exception.outcome, registration.TelegramRegistrationOutcome.INVALID_IDENTITY_PROOF)
        self.assertEqual(
            str(invalid.exception),
            "invalid_identity_proof",
        )
        self.assertEqual(invalid.exception.public_detail, "اطلاعات تایید هویت معتبر نیست")

    def test_sync_field_allowlist_is_exact_and_unknown_sources_are_empty(self):
        self.assertEqual(
            allowed_user_fields_for_source(" iran "),
            USER_SYNC_IDENTITY_FIELDS | USER_SYNC_SHARED_FIELDS | USER_SYNC_METADATA_FIELDS,
        )
        self.assertEqual(
            allowed_user_fields_for_source("FOREIGN"),
            USER_SYNC_FOREIGN_FIELDS | USER_SYNC_SHARED_FIELDS | USER_SYNC_METADATA_FIELDS,
        )
        self.assertEqual(allowed_user_fields_for_source("unknown"), frozenset())
        self.assertEqual(allowed_user_fields_for_source(""), frozenset())
        self.assertEqual(allowed_user_fields_for_source(None), frozenset())

    async def test_projection_gate_requires_exact_completed_standard_projection(self):
        command = _command()
        invitation = SimpleNamespace(
            token=command.invitation_token,
            is_used=True,
            revoked_at=None,
            completed_at=command.local_completed_at,
            mobile_number=command.mobile_number,
            account_name="stage9account",
            role=UserRole.STANDARD,
            kind=InvitationKind.STANDARD,
            registered_user_id=41,
        )
        user = SimpleNamespace(
            id=41,
            mobile_number=command.mobile_number,
            normalized_mobile_number=command.mobile_number,
            telegram_id=command.telegram_id,
            account_name="stage9account",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )

        async def resolve(
            invitation_rows=(invitation,),
            user_rows=(user,),
            accountant_rows=(),
            customer_rows=(),
            *,
            allowed=True,
        ):
            db = _projection_db(
                invitation_rows,
                user_rows,
                accountant_rows,
                customer_rows,
            )
            decision = SimpleNamespace(allowed=allowed)
            with patch.object(
                intent_service,
                "evaluate_bot_access_projection",
                return_value=decision,
            ) as evaluator:
                result = await intent_service.registration_projection_is_ready(
                    db,
                    command=command,
                )
            return result, db, evaluator

        resolution, db, evaluator = await resolve()
        self.assertEqual(
            resolution,
            intent_service.RegistrationProjectionResolution(
                local_user_id=41,
                authoritative_completed_at=command.local_completed_at,
            ),
        )
        evaluator.assert_called_once_with(
            user,
            is_accountant=False,
            customer_relation_present=False,
            customer_tier=None,
        )
        statements = tuple(
            _canonical_sql(call.args[0]) for call in db.execute.await_args_list
        )
        self.assertEqual(len(statements), 4)
        self.assertTrue(statements[0].startswith("SELECT invitations.id"))
        self.assertIn("FROM invitations WHERE invitations.token = :token_1", statements[0])
        self.assertTrue(statements[1].startswith("SELECT users.id"))
        self.assertIn(
            "WHERE users.normalized_mobile_number = :normalized_mobile_number_1 OR users.telegram_id = :telegram_id_1",
            statements[1],
        )
        self.assertTrue(statements[2].startswith("SELECT accountant_relations.id"))
        self.assertIn(
            "accountant_relations.invitation_token = :invitation_token_1 AND accountant_relations.status = :status_1 AND accountant_relations.deleted_at IS NULL",
            statements[2],
        )
        self.assertTrue(statements[3].startswith("SELECT customer_relations.id"))
        self.assertIn(
            "customer_relations.invitation_token = :invitation_token_1 AND customer_relations.status = :status_1 AND customer_relations.deleted_at IS NULL",
            statements[3],
        )

        for name, changed in (
            ("missing", None),
            ("unused", SimpleNamespace(**{**vars(invitation), "is_used": False})),
            (
                "revoked",
                SimpleNamespace(
                    **{**vars(invitation), "revoked_at": command.local_completed_at}
                ),
            ),
            ("incomplete", SimpleNamespace(**{**vars(invitation), "completed_at": None})),
        ):
            with self.subTest(invitation_state=name):
                rows = () if changed is None else (changed,)
                result, rejected_db, rejected_evaluator = await resolve(
                    invitation_rows=rows
                )
                self.assertIsNone(result)
                self.assertEqual(rejected_db.execute.await_count, 1)
                rejected_evaluator.assert_not_called()

        for name, changed in (
            (
                "mobile",
                SimpleNamespace(**{**vars(invitation), "mobile_number": "09129999999"}),
            ),
            (
                "account",
                SimpleNamespace(**{**vars(invitation), "account_name": "  "}),
            ),
        ):
            with self.subTest(invitation_identity=name):
                result, rejected_db, rejected_evaluator = await resolve(
                    invitation_rows=(changed,)
                )
                self.assertIsNone(result)
                self.assertEqual(rejected_db.execute.await_count, 1)
                rejected_evaluator.assert_not_called()

        conflicting_user = SimpleNamespace(**{**vars(user), "id": 42})
        for name, users in (
            ("none", ()),
            ("duplicate", (user, conflicting_user)),
            (
                "mobile_mismatch",
                (SimpleNamespace(**{**vars(user), "mobile_number": "09129999999"}),),
            ),
            (
                "telegram_mismatch",
                (SimpleNamespace(**{**vars(user), "telegram_id": user.telegram_id + 1}),),
            ),
        ):
            with self.subTest(user_match=name):
                result, rejected_db, rejected_evaluator = await resolve(user_rows=users)
                self.assertIsNone(result)
                self.assertEqual(rejected_db.execute.await_count, 2)
                rejected_evaluator.assert_not_called()

        for name, changed in (
            ("role", SimpleNamespace(**{**vars(user), "role": UserRole.WATCH})),
            ("account", SimpleNamespace(**{**vars(user), "account_name": "different"})),
        ):
            with self.subTest(user_projection=name):
                result, rejected_db, rejected_evaluator = await resolve(
                    user_rows=(changed,)
                )
                self.assertIsNone(result)
                self.assertEqual(rejected_db.execute.await_count, 2)
                rejected_evaluator.assert_not_called()
        wrong_registered = SimpleNamespace(
            **{**vars(invitation), "registered_user_id": user.id + 1}
        )
        result, rejected_db, rejected_evaluator = await resolve(
            invitation_rows=(wrong_registered,)
        )
        self.assertIsNone(result)
        self.assertEqual(rejected_db.execute.await_count, 2)
        rejected_evaluator.assert_not_called()

        result, _, relation_evaluator = await resolve(
            accountant_rows=(SimpleNamespace(), SimpleNamespace())
        )
        self.assertIsNone(result)
        relation_evaluator.assert_not_called()
        result, _, relation_evaluator = await resolve(
            customer_rows=(SimpleNamespace(), SimpleNamespace())
        )
        self.assertIsNone(result)
        relation_evaluator.assert_not_called()

        invalid_kind = SimpleNamespace(**{**vars(invitation), "kind": "future"})
        result, _, invalid_kind_evaluator = await resolve(
            invitation_rows=(invalid_kind,)
        )
        self.assertIsNone(result)
        invalid_kind_evaluator.assert_not_called()

        result, _, denied_evaluator = await resolve(allowed=False)
        self.assertIsNone(result)
        denied_evaluator.assert_called_once()

        standard_with_relation = SimpleNamespace(customer_user_id=user.id)
        result, _, standard_relation_evaluator = await resolve(
            customer_rows=(standard_with_relation,)
        )
        self.assertIsNone(result)
        standard_relation_evaluator.assert_not_called()

        customer_invitation = SimpleNamespace(
            **{**vars(invitation), "kind": InvitationKind.CUSTOMER}
        )
        customer_relation = SimpleNamespace(
            customer_user_id=user.id,
            customer_tier=CustomerTier.TIER_1,
        )
        customer_resolution, _, customer_evaluator = await resolve(
            invitation_rows=(customer_invitation,),
            customer_rows=(customer_relation,),
        )
        self.assertEqual(customer_resolution.local_user_id, user.id)
        self.assertEqual(
            customer_resolution.authoritative_completed_at,
            command.local_completed_at,
        )
        customer_evaluator.assert_called_once_with(
            user,
            is_accountant=False,
            customer_relation_present=True,
            customer_tier=CustomerTier.TIER_1,
        )

        wrong_customer_relation = SimpleNamespace(
            customer_user_id=user.id + 1,
            customer_tier=CustomerTier.TIER_1,
        )
        result, _, wrong_customer_evaluator = await resolve(
            invitation_rows=(customer_invitation,),
            customer_rows=(wrong_customer_relation,),
        )
        self.assertIsNone(result)
        wrong_customer_evaluator.assert_not_called()
        result, _, missing_customer_evaluator = await resolve(
            invitation_rows=(customer_invitation,),
        )
        self.assertIsNone(result)
        missing_customer_evaluator.assert_not_called()

        accountant_invitation = SimpleNamespace(
            **{**vars(invitation), "kind": InvitationKind.ACCOUNTANT}
        )
        result, _, accountant_evaluator = await resolve(
            invitation_rows=(accountant_invitation,),
            accountant_rows=(SimpleNamespace(accountant_user_id=user.id),),
        )
        self.assertIsNone(result)
        accountant_evaluator.assert_not_called()

    async def test_sms_claim_uses_exact_due_flag_lease_and_atomic_result(self):
        claimed_at = datetime(2026, 7, 12, 12, 0, 40, tzinfo=timezone.utc)
        with patch(
            "core.services.otp_delivery_state_service.settings.otp_delivery_state_secret",
            "stage9-mutation-state-secret-0123456789",
        ):
            state = build_otp_delivery_state(
                mobile="09121112233",
                ttl_seconds=120,
                now=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
            )
            redis = SimpleNamespace(eval=AsyncMock(return_value=[1, "12345"]))
            claim_id = uuid4()
            claim = await claim_sms_delivery(
                redis,
                state=state,
                require_due=True,
                now=claimed_at,
                lease_seconds=30,
                claim_id=claim_id,
            )
        self.assertIsNotNone(claim)
        self.assertEqual(claim.claim_id, claim_id)
        self.assertEqual(claim.request_id, state.otp_request_id)
        self.assertEqual(claim.otp_code, "12345")
        self.assertEqual(claim.mobile_number, "09121112233")
        lease_until = datetime(2026, 7, 12, 12, 1, 10, tzinfo=timezone.utc)
        self.assertEqual(claim.lease_until, lease_until)
        self.assertEqual(
            redis.eval.await_args.args,
            (
                _CLAIM_SCRIPT,
                3,
                _state_key(state.otp_request_id),
                OTP_FALLBACK_DUE_KEY,
                "otp:09121112233",
                str(state.otp_request_id),
                claimed_at.timestamp(),
                "1",
                str(claim_id),
                claimed_at.isoformat(),
                lease_until.isoformat(),
                lease_until.timestamp(),
                OTP_SMS_MINIMUM_SEND_TTL_SECONDS,
            ),
        )

        minimum_lease_claim_id = uuid4()
        minimum_lease_redis = SimpleNamespace(eval=AsyncMock(return_value=[1, b"54321"]))
        with patch(
            "core.services.otp_delivery_state_service.settings.otp_delivery_state_secret",
            "stage9-mutation-state-secret-0123456789",
        ):
            minimum_lease_claim = await claim_sms_delivery(
                minimum_lease_redis,
                state=state,
                require_due=False,
                now=claimed_at,
                lease_seconds=0,
                claim_id=minimum_lease_claim_id,
            )
        self.assertEqual(minimum_lease_claim.otp_code, "54321")
        self.assertEqual(
            minimum_lease_claim.lease_until,
            claimed_at + timedelta(seconds=1),
        )
        self.assertEqual(minimum_lease_redis.eval.await_args.args[7], "0")
        self.assertEqual(
            minimum_lease_redis.eval.await_args.args[8],
            str(minimum_lease_claim_id),
        )

        generated_claim_id = uuid4()
        generated_id_redis = SimpleNamespace(eval=AsyncMock(return_value=[1, None]))
        with patch(
            "core.services.otp_delivery_state_service.settings.otp_delivery_state_secret",
            "stage9-mutation-state-secret-0123456789",
        ), patch(
            "core.services.otp_delivery_state_service.uuid4",
            return_value=generated_claim_id,
        ) as generate_claim_id:
            generated_claim = await claim_sms_delivery(
                generated_id_redis,
                state=state,
                require_due=False,
                now=claimed_at,
            )
        generate_claim_id.assert_called_once_with()
        self.assertEqual(generated_claim.claim_id, generated_claim_id)
        self.assertEqual(generated_claim.otp_code, "")

        for rejected_result in (None, [], [0, "not_due"], [None, "not_due"]):
            with self.subTest(rejected_result=rejected_result):
                rejected_redis = SimpleNamespace(
                    eval=AsyncMock(return_value=rejected_result)
                )
                with patch(
                    "core.services.otp_delivery_state_service.settings.otp_delivery_state_secret",
                    "stage9-mutation-state-secret-0123456789",
                ):
                    rejected = await claim_sms_delivery(
                        rejected_redis,
                        state=state,
                        require_due=False,
                        now=claimed_at,
                        claim_id=claim_id,
                    )
                self.assertIsNone(rejected)

    def test_background_job_server_guards_are_exact_and_fail_closed(self):
        foreign = check_background_job_authority(
            JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
            server_mode=SERVER_FOREIGN,
        )
        self.assertEqual(
            foreign,
            BackgroundJobAuthorityDecision(
                ok=True,
                job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                current_server=SERVER_FOREIGN,
                allowed_servers=(SERVER_FOREIGN,),
                reason=None,
            ),
        )
        rejected = check_background_job_authority(
            JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
            server_mode=SERVER_IRAN,
        )
        self.assertEqual(
            rejected,
            BackgroundJobAuthorityDecision(
                ok=False,
                job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                current_server=SERVER_IRAN,
                allowed_servers=(SERVER_FOREIGN,),
                reason="background_job_not_allowed_on_server",
            ),
        )
        self.assertEqual(
            check_background_job_authority(
                JOB_OTP_SMS_FALLBACK,
                server_mode=SERVER_IRAN,
            ),
            BackgroundJobAuthorityDecision(
                ok=True,
                job_name=JOB_OTP_SMS_FALLBACK,
                current_server=SERVER_IRAN,
                allowed_servers=(SERVER_IRAN,),
                reason=None,
            ),
        )
        for raw_name, normalized_name in (("unknown", "unknown"), ("  unknown  ", "unknown"), (None, ""), ("", "")):
            with self.subTest(raw_name=raw_name):
                unknown = check_background_job_authority(
                    raw_name,
                    server_mode=SERVER_IRAN,
                )
                self.assertEqual(
                    unknown,
                    BackgroundJobAuthorityDecision(
                        ok=False,
                        job_name=normalized_name,
                        current_server=SERVER_IRAN,
                        allowed_servers=(),
                        reason="unknown_background_job",
                    ),
                )

        with patch(
            "core.background_job_authority.current_server",
            return_value=SERVER_FOREIGN,
        ) as current:
            implicit = check_background_job_authority(
                JOB_TELEGRAM_REGISTRATION_RECONCILIATION
            )
        current.assert_called_once_with()
        self.assertEqual(implicit.current_server, SERVER_FOREIGN)

        with patch(
            "core.background_job_authority.current_server",
            return_value=SERVER_IRAN,
        ):
            invalid_server = check_background_job_authority(
                JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                server_mode="unknown-server",
            )
        self.assertEqual(invalid_server.current_server, SERVER_IRAN)
        self.assertIs(invalid_server.ok, False)

    def test_invitation_sms_category_policy_is_independent(self):
        cases = (
            (InvitationKind.STANDARD, None, "invitation_sms_standard_enabled"),
            (" standard ", None, "invitation_sms_standard_enabled"),
            (InvitationKind.ACCOUNTANT, None, "invitation_sms_accountant_enabled"),
            ("ACCOUNTANT", None, "invitation_sms_accountant_enabled"),
            (
                InvitationKind.CUSTOMER,
                CustomerTier.TIER_1,
                "invitation_sms_customer_tier1_enabled",
            ),
            ("customer", " tier1 ", "invitation_sms_customer_tier1_enabled"),
            (
                InvitationKind.CUSTOMER,
                CustomerTier.TIER_2,
                "invitation_sms_customer_tier2_enabled",
            ),
            ("CUSTOMER", "TIER2", "invitation_sms_customer_tier2_enabled"),
        )
        for kind, tier, enabled_field in cases:
            for expected in (False, True):
                values = {
                    "invitation_sms_standard_enabled": not expected,
                    "invitation_sms_accountant_enabled": not expected,
                    "invitation_sms_customer_tier1_enabled": not expected,
                    "invitation_sms_customer_tier2_enabled": not expected,
                }
                values[enabled_field] = expected
                with self.subTest(
                    kind=kind,
                    tier=tier,
                    enabled_field=enabled_field,
                    expected=expected,
                ):
                    self.assertIs(
                        invitation_sms_enabled(
                            kind,
                            customer_tier=tier,
                            settings_obj=SimpleNamespace(**values),
                        ),
                        expected,
                    )

        disabled = SimpleNamespace(
            invitation_sms_standard_enabled=True,
            invitation_sms_accountant_enabled=True,
            invitation_sms_customer_tier1_enabled=True,
            invitation_sms_customer_tier2_enabled=True,
        )
        for kind, tier in (
            (None, None),
            ("", None),
            ("unknown", None),
            (InvitationKind.CUSTOMER, None),
            (InvitationKind.CUSTOMER, ""),
            (InvitationKind.CUSTOMER, "unknown"),
        ):
            with self.subTest(kind=kind, tier=tier):
                self.assertIs(
                    invitation_sms_enabled(
                        kind,
                        customer_tier=tier,
                        settings_obj=disabled,
                    ),
                    False,
                )

    async def test_commit_boundary_orders_checkpoints_and_marks_first_terminal_transition(self):
        db = SimpleNamespace(commit=AsyncMock())
        checkpoints = []

        async def checkpoint(name):
            checkpoints.append(name)

        result = registration.AuthoritativeRegistrationResult(
            outcome=registration.TelegramRegistrationOutcome.CREATED,
            authoritative_user_id=41,
        )
        committed = await registration._commit_result(db, result, checkpoint=checkpoint)
        db.commit.assert_awaited_once_with()
        self.assertEqual(checkpoints, ["before_commit", "after_commit"])
        self.assertFalse(result.first_terminal_transition)
        self.assertTrue(committed.first_terminal_transition)
        self.assertEqual(committed.authoritative_user_id, 41)


if __name__ == "__main__":
    unittest.main()
