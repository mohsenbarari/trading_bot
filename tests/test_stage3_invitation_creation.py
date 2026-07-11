import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from api.routers.invitations import create_invitation_internal_from_bot
from core.invitation_creation_contracts import (
    InternalInvitationCreateRequest,
    build_standard_invitation_idempotency_key,
)
from core.invitation_creation_forwarding import forward_standard_invitation_to_iran
from core.invitation_sms_policy import invitation_sms_enabled, invitation_sms_status
from core.registration_contracts import InvitationSMSStatus
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from core.services.canonical_invitation_creation_service import (
    CanonicalInvitationCreationError,
    create_or_reuse_canonical_invitation,
)
from models.customer_relation import CustomerTier
from models.invitation import InvitationKind
from models.user import UserRole


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _CanonicalDB:
    def __init__(self, execute_values=()):
        self.execute_values = list(execute_values)
        self.added = []
        self.flush_count = 0

    async def execute(self, _statement):
        if not self.execute_values:
            raise AssertionError("unexpected execute")
        return _ScalarResult(self.execute_values.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = 101


class _InternalDB:
    def __init__(self, user):
        self.user = user

    async def get(self, _model, _user_id):
        return self.user


class _Request:
    def __init__(self, body: bytes, *, source: str = SERVER_FOREIGN):
        self._body = body
        self.headers = {
            "x-timestamp": "1700000000",
            "x-signature": "signature",
            "x-api-key": "key",
            "x-source-server": source,
        }

    async def body(self):
        return self._body


class _Response:
    def __init__(self, status_code=201, payload=None, text=""):
        self.status_code = status_code
        self.payload = payload if payload is not None else {"created": True}
        self.text = text

    def json(self):
        return self.payload


class _Client:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, content, headers):
        self.calls.append((url, content, headers))
        return self.response


def _pending_invitation(**overrides):
    values = {
        "id": 9,
        "created_by_id": 7,
        "account_name": "user123",
        "mobile_number": "09123456789",
        "role": UserRole.STANDARD,
        "kind": InvitationKind.STANDARD,
        "token": "INV-existing",
        "short_code": "SHORT09",
        "expires_at": datetime.utcnow() + timedelta(days=2),
        "is_used": False,
        "registered_user_id": None,
        "completed_at": None,
        "completed_via": None,
        "revoked_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Stage3CanonicalInvitationTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_creation_is_iran_only_and_normalizes_before_reservation(self):
        with override_current_server(SERVER_FOREIGN), self.assertRaisesRegex(
            CanonicalInvitationCreationError,
            "iran_authority_required",
        ):
            await create_or_reuse_canonical_invitation(
                _CanonicalDB(),
                creator_user_id=7,
                account_name="User۱۲۳",
                mobile_number="۰۹۱۲۳۴۵۶۷۸۹",
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )

        db = _CanonicalDB([None])
        expiry = datetime.utcnow() + timedelta(days=2)
        with override_current_server(SERVER_IRAN), patch(
            "core.services.canonical_invitation_creation_service.acquire_invitation_creation_locks",
            new=AsyncMock(),
        ), patch(
            "core.services.canonical_invitation_creation_service.prune_terminal_identity_reservations",
            new=AsyncMock(),
        ), patch(
            "core.services.canonical_invitation_creation_service.find_identity_reservation",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.canonical_invitation_creation_service.get_new_invitation_expiry",
            new=AsyncMock(return_value=expiry),
        ), patch(
            "core.services.canonical_invitation_creation_service.reserve_invitation_identity",
            new=AsyncMock(),
        ) as reserve_mock:
            result = await create_or_reuse_canonical_invitation(
                db,
                creator_user_id=7,
                account_name="\u00a0User۱۲۳\t",
                mobile_number="\u200709۱۲۳۴۵۶۷۸۹\n",
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )

        self.assertTrue(result.created)
        self.assertEqual(result.invitation.account_name, "user123")
        self.assertEqual(result.invitation.mobile_number, "09123456789")
        self.assertEqual(result.invitation.expires_at, expiry)
        reserve_identity = reserve_mock.await_args.kwargs["identity"]
        self.assertEqual(reserve_identity.account_name, "user123")
        self.assertEqual(reserve_identity.mobile_number, "09123456789")

    async def test_exact_retry_reuses_and_changed_policy_fails_without_disclosure(self):
        invitation = _pending_invitation()
        reservation = SimpleNamespace(invitation_id=invitation.id)
        common_patches = (
            patch(
                "core.services.canonical_invitation_creation_service.acquire_invitation_creation_locks",
                new=AsyncMock(),
            ),
            patch(
                "core.services.canonical_invitation_creation_service.prune_terminal_identity_reservations",
                new=AsyncMock(),
            ),
            patch(
                "core.services.canonical_invitation_creation_service.find_identity_reservation",
                new=AsyncMock(return_value=reservation),
            ),
        )
        with override_current_server(SERVER_IRAN), common_patches[0], common_patches[1], common_patches[2]:
            result = await create_or_reuse_canonical_invitation(
                _CanonicalDB([None, invitation]),
                creator_user_id=7,
                account_name="USER۱۲۳",
                mobile_number="۰۹۱۲۳۴۵۶۷۸۹",
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )
        self.assertFalse(result.created)
        self.assertIs(result.invitation, invitation)

        with override_current_server(SERVER_IRAN), patch(
            "core.services.canonical_invitation_creation_service.acquire_invitation_creation_locks",
            new=AsyncMock(),
        ), patch(
            "core.services.canonical_invitation_creation_service.prune_terminal_identity_reservations",
            new=AsyncMock(),
        ), patch(
            "core.services.canonical_invitation_creation_service.find_identity_reservation",
            new=AsyncMock(return_value=reservation),
        ), self.assertRaisesRegex(CanonicalInvitationCreationError, "invitation_identity_conflict"):
            await create_or_reuse_canonical_invitation(
                _CanonicalDB([None, invitation]),
                creator_user_id=8,
                account_name="user123",
                mobile_number="09123456789",
                role=UserRole.STANDARD,
                kind=InvitationKind.STANDARD,
            )

    def test_internal_contract_and_idempotency_are_strict_and_canonical(self):
        first = build_standard_invitation_idempotency_key(
            requester_user_id=7,
            account_name=" User۱۲۳ ",
            mobile_number="۰۹۱۲۳۴۵۶۷۸۹",
            role=UserRole.STANDARD,
        )
        second = build_standard_invitation_idempotency_key(
            requester_user_id=7,
            account_name="user123",
            mobile_number="09123456789",
            role=UserRole.STANDARD.value,
        )
        self.assertEqual(first, second)
        payload = InternalInvitationCreateRequest(
            requester_user_id=7,
            account_name="User۱۲۳",
            mobile_number="۰۹۱۲۳۴۵۶۷۸۹",
            role=UserRole.STANDARD,
            source_server=SERVER_FOREIGN,
            idempotency_key=first,
        )
        self.assertEqual(payload.account_name, "user123")
        self.assertEqual(payload.mobile_number, "09123456789")
        with self.assertRaises(ValidationError):
            InternalInvitationCreateRequest(**{**payload.model_dump(), "unexpected": True})

    def test_sms_flags_are_independent_and_status_is_explicit(self):
        flags = SimpleNamespace(
            invitation_sms_standard_enabled=False,
            invitation_sms_accountant_enabled=True,
            invitation_sms_customer_tier1_enabled=False,
            invitation_sms_customer_tier2_enabled=True,
        )
        self.assertFalse(invitation_sms_enabled(InvitationKind.STANDARD, settings_obj=flags))
        self.assertTrue(invitation_sms_enabled(InvitationKind.ACCOUNTANT, settings_obj=flags))
        self.assertFalse(
            invitation_sms_enabled(
                InvitationKind.CUSTOMER,
                customer_tier=CustomerTier.TIER_1,
                settings_obj=flags,
            )
        )
        self.assertTrue(
            invitation_sms_enabled(
                InvitationKind.CUSTOMER,
                customer_tier=CustomerTier.TIER_2,
                settings_obj=flags,
            )
        )
        self.assertEqual(
            invitation_sms_status(enabled=False, accepted=None),
            InvitationSMSStatus.DISABLED,
        )
        self.assertEqual(
            invitation_sms_status(enabled=True, accepted=True),
            InvitationSMSStatus.ACCEPTED,
        )
        self.assertEqual(
            invitation_sms_status(enabled=True, accepted=False),
            InvitationSMSStatus.FAILED,
        )

    async def test_internal_endpoint_requires_signature_source_authority_and_exact_key(self):
        key = build_standard_invitation_idempotency_key(
            requester_user_id=7,
            account_name="user123",
            mobile_number="09123456789",
            role=UserRole.STANDARD,
        )
        payload = InternalInvitationCreateRequest(
            requester_user_id=7,
            account_name="user123",
            mobile_number="09123456789",
            role=UserRole.STANDARD,
            source_server=SERVER_FOREIGN,
            idempotency_key=key,
        )
        request = _Request(json.dumps(payload.model_dump(mode="json")).encode())
        with patch("api.routers.invitations.verify_internal_signature", return_value=False), self.assertRaises(HTTPException) as exc:
            await create_invitation_internal_from_bot(payload, request, db=_InternalDB(None))
        self.assertEqual(exc.exception.status_code, 401)

        admin = SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN, is_deleted=False)
        expected = {"created": True, "bot_link": "bot", "web_link": "web"}
        with override_current_server(SERVER_IRAN), patch(
            "api.routers.invitations.verify_internal_signature",
            return_value=True,
        ), patch(
            "api.routers.invitations._create_standard_invitation",
            new=AsyncMock(return_value=expected),
        ) as create_mock:
            result = await create_invitation_internal_from_bot(payload, request, db=_InternalDB(admin))
        self.assertEqual(result, expected)
        create_mock.assert_awaited_once()

    async def test_forwarder_reuses_existing_signed_transport_and_rejects_wrong_server(self):
        payload = {
            "requester_user_id": 7,
            "account_name": "user123",
            "mobile_number": "09123456789",
            "role": UserRole.STANDARD.value,
            "source_server": SERVER_FOREIGN,
            "idempotency_key": "standard-invitation:" + "a" * 40,
        }
        with override_current_server(SERVER_IRAN):
            status, _ = await forward_standard_invitation_to_iran(payload)
        self.assertEqual(status, 403)

        calls = []
        with override_current_server(SERVER_FOREIGN), patch(
            "core.invitation_creation_forwarding.peer_server_url_for",
            return_value="https://iran.example",
        ), patch(
            "core.invitation_creation_forwarding.sign_internal_payload",
            return_value="signed",
        ), patch(
            "core.invitation_creation_forwarding.httpx.AsyncClient",
            return_value=_Client(_Response(payload={"created": True}), calls),
        ):
            status, response = await forward_standard_invitation_to_iran(payload)
        self.assertEqual(status, 201)
        self.assertEqual(response, {"created": True})
        self.assertEqual(calls[0][0], "https://iran.example/api/invitations/internal/create")
        self.assertEqual(calls[0][2]["X-Signature"], "signed")
        self.assertNotIn("mobile_number", calls[0][2])


if __name__ == "__main__":
    unittest.main()
