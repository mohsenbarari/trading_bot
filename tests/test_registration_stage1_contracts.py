import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from pydantic import ValidationError

from core.invitation_contract_service import (
    build_invitation_contract_v2,
    build_public_invitation_contract_v2,
    invitation_surface_availability,
)
from core.invitation_creation_contracts import (
    InternalInvitationCreateRequest,
    InvitationRequesterIdentity,
)
from core.invitation_sms_policy import invitation_sms_enabled
from core.public_webapp_url import (
    PublicWebAppURLConfigurationError,
    user_facing_webapp_url,
    validate_public_webapp_url,
)
from core.registration_contracts import (
    REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE,
    InvitationSMSStatus,
    OTPDeliveryStateContract,
    RegistrationIdentityProofType,
    RegistrationSourceSurface,
    TelegramRegistrationCommand,
    TelegramOTPDeliveryCommand,
    canonical_registration_command_bytes,
    invitation_token_hash,
    registration_command_hash,
)
from core.services.invitation_lifecycle_service import (
    complete_invitation,
    derive_invitation_state,
    get_new_invitation_expiry,
    is_post_expiry_reconciliation_allowed,
    soft_revoke_invitation,
    validate_registration_address,
)
from core.enums import UserRole
from models.customer_relation import CustomerTier
from models.invitation import InvitationCompletionSurface, InvitationKind


def _url_settings(**overrides):
    values = {
        "environment": "staging",
        "iran_server_aliases": "",
        "iran_server_domain": "staging.gold-trade.ir",
        "iran_server_url": "https://staging.gold-trade.ir",
        "foreign_server_aliases": "",
        "foreign_server_domain": "staging.362514.ir",
        "foreign_server_url": "https://staging.362514.ir",
        "germany_server_url": None,
        "peer_server_url": None,
        "public_webapp_url": "https://staging.gold-trade.ir",
        "frontend_url": "https://legacy.example",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _command_payload(**overrides):
    verified_at = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    values = {
        "command_id": "11111111-2222-4333-8444-555555555555",
        "idempotency_key": "telegram-registration:test-0001",
        "invitation_token": "INV-1234567890abcdef",
        "source_surface": "telegram_bot",
        "identity_proof_type": "telegram_contact",
        "mobile_number": "09120000000",
        "telegram_id": 123456789,
        "telegram_username": "sample_user",
        "telegram_full_name": "Sample User",
        "address": "1234567890",
        "contact_verified_at": verified_at.isoformat(),
        "local_completed_at": (verified_at + timedelta(seconds=5)).isoformat(),
        "invitation_expires_at_snapshot": (verified_at + timedelta(hours=1)).isoformat(),
    }
    values.update(overrides)
    return values


class RegistrationStage1ContractTests(unittest.IsolatedAsyncioTestCase):
    def test_invitation_request_contracts_reject_empty_canonical_identity(self):
        with self.assertRaisesRegex(ValueError, "هویت نام کاربری نامعتبر است"):
            InvitationRequesterIdentity.normalize_account("")
        with self.assertRaisesRegex(ValueError, "هویت شماره موبایل نامعتبر است"):
            InvitationRequesterIdentity.normalize_mobile("123")
        with self.assertRaisesRegex(ValueError, "نام کاربری نامعتبر است"):
            InternalInvitationCreateRequest.normalize_account("ab")
        with self.assertRaisesRegex(ValueError, "شماره موبایل نامعتبر است"):
            InternalInvitationCreateRequest.normalize_mobile("123")

        requester = InvitationRequesterIdentity(
            account_name="requester",
            mobile_number="09120000000",
            telegram_id=1,
        )
        base = {
            "requester_identity": requester,
            "account_name": "valid-account",
            "mobile_number": "09121112233",
            "source_server": "foreign",
            "idempotency_key": "standard-invitation:" + "a" * 40,
        }
        for changes in ({"account_name": "ab"}, {"mobile_number": "123"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                InternalInvitationCreateRequest.model_validate({**base, **changes})

        spaced_name_request = InternalInvitationCreateRequest.model_validate(
            {**base, "account_name": "محمد یگانه"}
        )
        self.assertEqual(spaced_name_request.account_name, "محمد یگانه")

    def test_otp_contract_rejects_naive_invalid_or_partial_timeline(self):
        now = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
        base = {
            "otp_request_id": uuid4(),
            "identity_digest": "a" * 64,
            "delivery_target_ciphertext": "ciphertext-" + "b" * 32,
            "created_at": now,
            "expires_at": now + timedelta(seconds=120),
        }
        invalid = (
            {"created_at": now.replace(tzinfo=None)},
            {"expires_at": now},
            {"sms_claim_id": uuid4()},
            {
                "sms_claim_id": uuid4(),
                "sms_claimed_at": now + timedelta(seconds=1),
                "sms_claim_lease_until": now + timedelta(seconds=1),
            },
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                OTPDeliveryStateContract.model_validate({**base, **changes})

        with self.assertRaises(ValidationError):
            TelegramOTPDeliveryCommand(
                otp_request_id=uuid4(),
                telegram_id=1,
                otp_code="12345",
                expires_at=now.replace(tzinfo=None),
            )

    def test_user_facing_webapp_url_preserves_legacy_mode_and_requires_iran_for_new_flow(self):
        legacy = _url_settings(invitation_contract_v2_enabled=False)
        self.assertEqual(user_facing_webapp_url(settings_obj=legacy), "https://legacy.example")

        enabled = _url_settings(invitation_contract_v2_enabled=True)
        self.assertEqual(
            user_facing_webapp_url(settings_obj=enabled),
            "https://staging.gold-trade.ir",
        )
        otp_only = _url_settings(
            invitation_contract_v2_enabled=False,
            telegram_login_otp_enabled=True,
        )
        self.assertEqual(
            user_facing_webapp_url(settings_obj=otp_only),
            "https://staging.gold-trade.ir",
        )

    def test_address_validator_matches_existing_minimum_without_normalizing(self):
        with self.assertRaisesRegex(ValueError, REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE):
            validate_registration_address("123456789")

        self.assertEqual(validate_registration_address("1234567890"), "1234567890")
        self.assertEqual(validate_registration_address("          "), "          ")
        self.assertEqual(validate_registration_address("  address value  "), "  address value  ")

    def test_telegram_command_is_strict_normalized_and_canonical(self):
        payload = _command_payload(mobile_number="۰۹۱۲۰۰۰۰۰۰۰")
        command = TelegramRegistrationCommand.model_validate(payload)

        self.assertEqual(command.mobile_number, "09120000000")
        self.assertEqual(command.source_surface, RegistrationSourceSurface.TELEGRAM_BOT)
        self.assertEqual(command.identity_proof_type, RegistrationIdentityProofType.TELEGRAM_CONTACT)
        self.assertEqual(command.command_id, UUID(payload["command_id"]))
        self.assertEqual(
            canonical_registration_command_bytes(command),
            canonical_registration_command_bytes(dict(reversed(list(payload.items())))),
        )
        self.assertEqual(len(registration_command_hash(command)), 64)
        self.assertEqual(len(invitation_token_hash(command.invitation_token)), 64)

    def test_telegram_command_preserves_registration_address_exactly(self):
        exact = " 1234567890 "
        command = TelegramRegistrationCommand.model_validate(
            _command_payload(address=exact)
        )
        self.assertEqual(command.address, exact)
        self.assertNotEqual(
            registration_command_hash(command),
            registration_command_hash(
                TelegramRegistrationCommand.model_validate(
                    _command_payload(address=exact.strip())
                )
            ),
        )
        self.assertEqual(
            TelegramRegistrationCommand.model_validate(
                _command_payload(address="          ")
            ).address,
            "          ",
        )
        with self.assertRaisesRegex(ValidationError, REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE):
            TelegramRegistrationCommand.model_validate(
                _command_payload(address="         ")
            )

    def test_telegram_command_rejects_wrong_surface_proof_timeline_and_extra_fields(self):
        invalid_payloads = [
            _command_payload(source_surface="webapp"),
            _command_payload(identity_proof_type="web_otp"),
            _command_payload(local_completed_at="2026-07-11T09:59:59+00:00"),
            _command_payload(local_completed_at="2026-07-11T11:00:01+00:00"),
            _command_payload(unexpected=True),
            _command_payload(contact_verified_at="2026-07-11T10:00:00"),
            _command_payload(address="123456789"),
            _command_payload(telegram_id=0),
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    TelegramRegistrationCommand.model_validate(payload)

    async def test_invitation_lifetime_uses_central_setting_and_snapshots_now(self):
        from unittest.mock import AsyncMock, patch

        now = datetime(2024, 2, 28, 23, 30, tzinfo=timezone.utc)
        with patch(
            "core.services.invitation_lifecycle_service.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(invitation_expiry_days=2)),
        ):
            expires_at = await get_new_invitation_expiry(now=now)

        self.assertEqual(expires_at, datetime(2024, 3, 1, 23, 30))
        self.assertIsNone(expires_at.tzinfo)

    async def test_invitation_lifetime_rejects_invalid_central_values(self):
        from unittest.mock import AsyncMock, patch

        for value in (0, -1, "bad"):
            with self.subTest(value=value), patch(
                "core.services.invitation_lifecycle_service.get_trading_settings_async",
                new=AsyncMock(return_value=SimpleNamespace(invitation_expiry_days=value)),
            ):
                with self.assertRaises(RuntimeError):
                    await get_new_invitation_expiry()

    def test_invitation_derived_state_and_atomic_lifecycle(self):
        now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
        invitation = SimpleNamespace(
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
            expires_at=now + timedelta(minutes=1),
        )
        self.assertEqual(derive_invitation_state(invitation, now=now).value, "pending")

        complete_invitation(
            invitation,
            registered_user_id=7,
            completed_via=InvitationCompletionSurface.WEB,
            completed_at=now,
        )
        self.assertEqual(derive_invitation_state(invitation, now=now).value, "completed")
        with self.assertRaises(ValueError):
            soft_revoke_invitation(invitation, revoked_at=now)

        revoked = SimpleNamespace(
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
            expires_at=now + timedelta(minutes=1),
        )
        soft_revoke_invitation(revoked, revoked_at=now)
        self.assertEqual(derive_invitation_state(revoked, now=now).value, "revoked")
        with self.assertRaises(ValueError):
            complete_invitation(
                revoked,
                registered_user_id=7,
                completed_via=InvitationCompletionSurface.TELEGRAM,
            )

        expired = SimpleNamespace(
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
            expires_at=now,
        )
        self.assertEqual(derive_invitation_state(expired, now=now).value, "pending")
        self.assertEqual(
            derive_invitation_state(expired, now=now + timedelta(microseconds=1)).value,
            "expired",
        )
        with self.assertRaisesRegex(ValueError, "registered_user_id must be positive"):
            complete_invitation(
                expired,
                registered_user_id=0,
                completed_via=InvitationCompletionSurface.WEB,
            )

    def test_post_expiry_reconciliation_boundary_is_inclusive_and_revocation_wins(self):
        expiry = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
        invitation = SimpleNamespace(expires_at=expiry, revoked_at=None)

        self.assertTrue(
            is_post_expiry_reconciliation_allowed(
                invitation,
                proof_completed_at=expiry,
                received_at=expiry + timedelta(seconds=86400),
                grace_seconds=86400,
            )
        )
        self.assertFalse(
            is_post_expiry_reconciliation_allowed(
                invitation,
                proof_completed_at=expiry,
                received_at=expiry + timedelta(seconds=86400, microseconds=1),
                grace_seconds=86400,
            )
        )
        invitation.revoked_at = expiry + timedelta(hours=1)
        self.assertFalse(
            is_post_expiry_reconciliation_allowed(
                invitation,
                proof_completed_at=expiry,
                received_at=expiry + timedelta(hours=2),
                grace_seconds=86400,
            )
        )

    def test_public_webapp_url_rejects_foreign_or_unsafe_origins(self):
        settings_obj = _url_settings()
        self.assertEqual(
            validate_public_webapp_url(settings_obj.public_webapp_url, settings_obj=settings_obj),
            "https://staging.gold-trade.ir",
        )

        for value in (
            "",
            "staging.gold-trade.ir",
            "http://staging.gold-trade.ir",
            "https://staging.362514.ir",
            "https://staging.362514.ir.",
            "https://staging.gold-trade.ir/register",
            "https://staging.gold-trade.ir?token=x",
            "https://user:pass@staging.gold-trade.ir",
            "https://staging.gold-trade.ir:bad",
            "https://staging.gold-trade.ir:0",
            "https://[invalid",
            "https://unconfigured-iran.example",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PublicWebAppURLConfigurationError):
                    validate_public_webapp_url(value, settings_obj=settings_obj)

        local_settings = _url_settings(
            environment="test",
            iran_server_domain="localhost",
            iran_server_url="http://localhost:8000",
            foreign_server_domain=None,
            foreign_server_url=None,
        )
        self.assertEqual(
            validate_public_webapp_url("http://localhost:8000", settings_obj=local_settings),
            "http://localhost:8000",
        )

        separated_settings = _url_settings(
            iran_server_domain="sync.gold-trade.ir",
            iran_server_url="http://iran-app:8000",
            iran_server_aliases="app.gold-trade.ir,iran-app",
            public_webapp_url="https://app.gold-trade.ir",
        )
        self.assertEqual(
            validate_public_webapp_url(
                separated_settings.public_webapp_url,
                settings_obj=separated_settings,
            ),
            "https://app.gold-trade.ir",
        )

        unconfigured_settings = _url_settings(
            iran_server_domain=None,
            iran_server_url=None,
            iran_server_aliases="",
        )
        with self.assertRaises(PublicWebAppURLConfigurationError):
            validate_public_webapp_url(
                "https://app.gold-trade.ir",
                settings_obj=unconfigured_settings,
            )

    def test_invitation_contract_v2_reports_surface_availability_and_aliases(self):
        legacy = invitation_surface_availability(
            InvitationKind.LEGACY_UNKNOWN,
            role=UserRole.STANDARD,
        )
        self.assertFalse(legacy.bot)
        self.assertFalse(legacy.web)
        self.assertTrue(
            invitation_surface_availability(
                InvitationKind.STANDARD,
                role=UserRole.STANDARD,
            ).bot
        )
        self.assertFalse(
            invitation_surface_availability(
                InvitationKind.ACCOUNTANT,
                role=UserRole.STANDARD,
            ).bot
        )

        from core.services.invitation_lifecycle_service import invitation_kind_from_token

        self.assertEqual(
            invitation_kind_from_token("unknown-token"),
            InvitationKind.LEGACY_UNKNOWN,
        )
        self.assertFalse(
            invitation_surface_availability(
                InvitationKind.CUSTOMER,
                role=UserRole.STANDARD,
                customer_tier=CustomerTier.TIER_2,
            ).bot
        )

        expiry = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
        invitation = SimpleNamespace(
            token="INV-contract-test",
            short_code="Ab12Cd34",
            kind=InvitationKind.STANDARD,
            role=UserRole.STANDARD,
            expires_at=expiry,
            is_used=False,
            registered_user_id=None,
            completed_at=None,
            completed_via=None,
            revoked_at=None,
        )
        contract = build_invitation_contract_v2(
            invitation,
            bot_username="@test_bot",
            sms_status=InvitationSMSStatus.DISABLED,
            settings_obj=_url_settings(),
        )

        self.assertEqual(contract.bot_link, "https://t.me/test_bot?start=INV-contract-test")
        self.assertEqual(
            contract.web_link,
            "https://staging.gold-trade.ir/register?token=INV-contract-test",
        )
        self.assertEqual(contract.link, contract.bot_link)
        self.assertEqual(contract.short_link, contract.web_short_link)
        self.assertEqual(contract.state.value, "pending")

        invitation.is_used = True
        invitation.registered_user_id = 7
        invitation.completed_at = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
        invitation.completed_via = "telegram"
        completed_contract = build_invitation_contract_v2(
            invitation,
            bot_username="@test_bot",
            sms_status=InvitationSMSStatus.DISABLED,
            settings_obj=_url_settings(),
        )
        self.assertEqual(completed_contract.state.value, "completed")
        self.assertFalse(completed_contract.bot_available)
        self.assertFalse(completed_contract.web_available)
        self.assertIsNone(completed_contract.bot_link)
        self.assertEqual(completed_contract.web_link, "")
        self.assertIsNone(completed_contract.web_short_link)

        public_contract = build_public_invitation_contract_v2(invitation)
        self.assertFalse(public_contract.valid)
        self.assertIsNone(public_contract.token)
        self.assertIsNone(public_contract.mobile_number)

    def test_unknown_invitation_sms_category_fails_closed(self):
        settings_obj = SimpleNamespace(
            invitation_sms_standard_enabled=True,
            invitation_sms_accountant_enabled=True,
            invitation_sms_customer_tier1_enabled=True,
            invitation_sms_customer_tier2_enabled=True,
        )
        self.assertFalse(
            invitation_sms_enabled("unknown", settings_obj=settings_obj)
        )

    def test_public_webapp_peer_classification_depends_on_local_server_role(self):
        foreign_settings = _url_settings(
            server_mode="foreign",
            public_webapp_url="https://staging.gold-trade.ir",
            peer_server_url="https://staging.gold-trade.ir",
        )
        self.assertEqual(
            validate_public_webapp_url(
                foreign_settings.public_webapp_url,
                settings_obj=foreign_settings,
            ),
            "https://staging.gold-trade.ir",
        )

        iran_settings = _url_settings(
            server_mode="iran",
            public_webapp_url="https://staging.362514.ir",
            iran_server_domain="staging.362514.ir",
            iran_server_url="https://staging.362514.ir",
            foreign_server_domain=None,
            foreign_server_url=None,
            peer_server_url="https://staging.362514.ir",
        )
        with self.assertRaisesRegex(
            PublicWebAppURLConfigurationError,
            "must not target a foreign server",
        ):
            validate_public_webapp_url(
                iran_settings.public_webapp_url,
                settings_obj=iran_settings,
            )


if __name__ == "__main__":
    unittest.main()
