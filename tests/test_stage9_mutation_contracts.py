from __future__ import annotations

import unittest
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
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from core.services import authoritative_registration_service as registration
from core.services.canonical_invitation_creation_service import _is_exact_base_retry
from core.services.otp_delivery_state_service import (
    OTP_FALLBACK_DUE_KEY,
    _CONSUME_SCRIPT,
    _mobile_request_key,
    consume_otp_code,
)
from core.services.telegram_registration_intent_service import _intent_matches_command
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


if __name__ == "__main__":
    unittest.main()
