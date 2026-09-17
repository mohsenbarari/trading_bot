"""Hermetic batch characterization for STANDARD-role invitation creation service boundaries.

Stage: P1-00b
Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
  - API-IDENTITY-POST-INVITATIONS-CREATE-INVITATION-L320
  - API-IDENTITY-POST-INVITATIONS-CREATE-INVITATION-INTERNAL-FROM-BOT-L329
Targets:
  - api.routers.invitations.create_invitation
  - api.routers.invitations.create_invitation_internal_from_bot
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import tests.test_p1_00_characterization_network_isolation
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

tests.test_p1_00_characterization_network_isolation.install_p1_00_network_fence()
install_p1_00_network_fence()

from api.routers.invitations import (
    InvitationCreate,
    create_invitation,
    create_invitation_internal_from_bot,
)
from core.invitation_creation_contracts import (
    InternalInvitationCreateRequest,
    InvitationRequesterIdentity,
)
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, normalize_server
from models.user import UserRole


class DummyRawRequest:
    """Hermetic raw request double carrying exact body and internal headers."""

    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = body
        self.headers = headers

    async def body(self) -> bytes:
        return self._body


class InvitationsStandardCreateServiceBoundaryCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic batch characterization tests for STANDARD-role creation handlers."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.sentinel_db = object()
        self.sentinel_result = object()
        self.standard_account_name = "standard_test_account"
        self.standard_mobile_number = "09123456789"
        self.standard_role = UserRole.STANDARD

        self.standard_invite = InvitationCreate(
            account_name=self.standard_account_name,
            mobile_number=self.standard_mobile_number,
            role=self.standard_role,
        )

        self.requester_identity = InvitationRequesterIdentity(
            account_name="synthetic_middle_manager",
            mobile_number="09121112233",
            telegram_id=987654321,
        )
        self.hardcoded_idempotency_key = (
            "standard-invitation:0123456789abcdef0123456789abcdef01234567"
        )
        self.internal_request_payload = InternalInvitationCreateRequest(
            requester_identity=self.requester_identity,
            account_name=self.standard_account_name,
            mobile_number=self.standard_mobile_number,
            role=self.standard_role,
            source_server=SERVER_FOREIGN,
            idempotency_key=self.hardcoded_idempotency_key,
        )
        self.raw_request_body = (
            b'{"action": "create_standard_invitation_internal", "tag": "synthetic_test"}'
        )
        self.raw_request_headers = {
            "x-timestamp": "1726500000",
            "x-signature": "synthetic_signature_hex_digest_value_123",
            "x-api-key": "synthetic_internal_api_key_secret_456",
            "x-source-server": SERVER_FOREIGN,
        }
        self.raw_request = DummyRawRequest(
            body=self.raw_request_body,
            headers=self.raw_request_headers,
        )
        self.admin_middle_manager = mock.Mock(role=UserRole.MIDDLE_MANAGER)
        self.shared_create_boundary = mock.AsyncMock(return_value=self.sentinel_result)

    async def test_create_invitation_forwards_standard_role_to_shared_creation_boundary(
        self,
    ) -> None:
        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with mock.patch(
            "api.routers.invitations._create_standard_invitation",
            new=self.shared_create_boundary,
        ):
            result = await create_invitation(
                invite=self.standard_invite,
                db=self.sentinel_db,  # type: ignore[arg-type]
                admin=self.admin_middle_manager,  # type: ignore[arg-type]
            )

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertIs(result, self.sentinel_result)
        self.assertIs(self.standard_invite.role, UserRole.STANDARD)
        self.shared_create_boundary.assert_awaited_once_with(
            invite=self.standard_invite,
            db=self.sentinel_db,
            admin=self.admin_middle_manager,
        )
        self.assertEqual(self.shared_create_boundary.await_count, 1)
        _, kwargs = self.shared_create_boundary.call_args
        self.assertIs(kwargs["invite"], self.standard_invite)
        self.assertIs(kwargs["invite"].role, UserRole.STANDARD)
        self.assertIs(kwargs["db"], self.sentinel_db)
        self.assertIs(kwargs["admin"], self.admin_middle_manager)

    async def test_internal_create_forwards_middle_manager_standard_role_to_shared_creation_boundary(
        self,
    ) -> None:
        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        mock_verify = mock.Mock(return_value=True)
        mock_normalize_server = mock.Mock(side_effect=normalize_server)
        mock_current_server = mock.Mock(return_value=SERVER_IRAN)
        mock_build_key = mock.Mock(return_value=self.hardcoded_idempotency_key)
        mock_resolve = mock.AsyncMock(return_value=self.admin_middle_manager)

        with mock.patch(
            "api.routers.invitations.verify_internal_signature",
            new=mock_verify,
        ), mock.patch(
            "api.routers.invitations.normalize_server",
            new=mock_normalize_server,
        ), mock.patch(
            "api.routers.invitations.current_server",
            new=mock_current_server,
        ), mock.patch(
            "api.routers.invitations.build_standard_invitation_idempotency_key",
            new=mock_build_key,
        ), mock.patch(
            "api.routers.invitations.resolve_current_invitation_requester",
            new=mock_resolve,
        ), mock.patch(
            "api.routers.invitations._create_standard_invitation",
            new=self.shared_create_boundary,
        ):
            result = await create_invitation_internal_from_bot(
                payload=self.internal_request_payload,
                raw_request=self.raw_request,  # type: ignore[arg-type]
                db=self.sentinel_db,  # type: ignore[arg-type]
            )

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertIs(result, self.sentinel_result)

        mock_verify.assert_called_once_with(
            self.raw_request_body,
            "1726500000",
            "synthetic_signature_hex_digest_value_123",
            "synthetic_internal_api_key_secret_456",
        )
        self.assertEqual(mock_verify.call_count, 1)

        mock_normalize_server.assert_called_once_with(
            SERVER_FOREIGN,
            default="",
        )
        self.assertEqual(mock_normalize_server.call_count, 1)

        mock_current_server.assert_called_once_with()
        self.assertEqual(mock_current_server.call_count, 1)

        mock_build_key.assert_called_once_with(
            requester_identity=self.requester_identity,
            account_name="standard_test_account",
            mobile_number="09123456789",
            role=UserRole.STANDARD,
        )
        self.assertEqual(mock_build_key.call_count, 1)

        mock_resolve.assert_awaited_once_with(
            self.sentinel_db,
            identity=self.requester_identity,
        )
        self.assertEqual(mock_resolve.await_count, 1)
        resolve_args, resolve_kwargs = mock_resolve.await_args
        self.assertIs(resolve_args[0], self.sentinel_db)
        self.assertIs(resolve_kwargs["identity"], self.requester_identity)

        self.shared_create_boundary.assert_awaited_once()
        self.assertEqual(self.shared_create_boundary.await_count, 1)
        _, create_kwargs = self.shared_create_boundary.await_args
        created_invite = create_kwargs["invite"]
        self.assertIsInstance(created_invite, InvitationCreate)
        self.assertEqual(created_invite.account_name, "standard_test_account")
        self.assertEqual(created_invite.mobile_number, "09123456789")
        self.assertIs(created_invite.role, UserRole.STANDARD)
        self.assertIs(create_kwargs["db"], self.sentinel_db)
        self.assertIs(create_kwargs["admin"], self.admin_middle_manager)


if __name__ == "__main__":
    unittest.main()
