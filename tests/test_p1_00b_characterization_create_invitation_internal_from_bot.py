"""Hermetic direct characterization for create_invitation_internal_from_bot handler.

Stage: P1-00b
Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-INVITATIONS-CREATE-INVITATION-INTERNAL-FROM-BOT-L329
Target: api.routers.invitations.create_invitation_internal_from_bot

Note: This unit provides direct characterization for seed
API-IDENTITY-POST-INVITATIONS-CREATE-INVITATION-INTERNAL-FROM-BOT-L329 and the actual
api.routers.invitations.create_invitation_internal_from_bot handler under Stage P1-00b
network isolation. This unit neither updates inventory counts nor closes the gap
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE or Stage P1-00b.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException

from api.routers.invitations import (
    InvitationCreate,
    create_invitation_internal_from_bot,
)
from core.invitation_creation_contracts import (
    InternalInvitationCreateRequest,
    InvitationRequesterIdentity,
    build_standard_invitation_idempotency_key,
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


def _build_valid_payload_and_headers(
    *,
    header_source: str = SERVER_FOREIGN,
) -> tuple[InternalInvitationCreateRequest, bytes, dict[str, str]]:
    requester_identity = InvitationRequesterIdentity(
        account_name="test_admin_requester",
        mobile_number="09121112233",
        telegram_id=987654321,
    )
    account_name = "test_invitee_user"
    mobile_number = "09129998877"
    role = UserRole.WATCH
    idempotency_key = build_standard_invitation_idempotency_key(
        requester_identity=requester_identity,
        account_name=account_name,
        mobile_number=mobile_number,
        role=role,
    )
    payload = InternalInvitationCreateRequest(
        requester_identity=requester_identity,
        account_name=account_name,
        mobile_number=mobile_number,
        role=role,
        source_server=SERVER_FOREIGN,
        idempotency_key=idempotency_key,
    )
    exact_body = b'{"action": "create_invitation_internal", "data": "test_payload"}'
    exact_headers = {
        "x-timestamp": "1726176000",
        "x-signature": "test_signature_hex_digest_value",
        "x-api-key": "test_internal_api_key_secret",
        "x-source-server": header_source,
    }
    return payload, exact_body, exact_headers


class P100bCreateInvitationInternalFromBotCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic direct characterization tests for create_invitation_internal_from_bot."""

    async def test_create_invitation_internal_from_bot_success_forwards_arguments_and_returns_result_identity(
        self,
    ) -> None:
        payload, exact_body, exact_headers = _build_valid_payload_and_headers()
        raw_request = DummyRawRequest(body=exact_body, headers=exact_headers)

        sentinel_db = object()
        sentinel_admin = mock.Mock(role=UserRole.SUPER_ADMIN)
        sentinel_result = object()

        mock_verify = mock.Mock(return_value=True)
        mock_normalize_server = mock.Mock(side_effect=normalize_server)
        mock_current_server = mock.Mock(return_value=SERVER_IRAN)
        mock_resolve = mock.AsyncMock(return_value=sentinel_admin)
        mock_create = mock.AsyncMock(return_value=sentinel_result)

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
            "api.routers.invitations.resolve_current_invitation_requester",
            new=mock_resolve,
        ), mock.patch(
            "api.routers.invitations._create_standard_invitation",
            new=mock_create,
        ):
            result = await create_invitation_internal_from_bot(
                payload=payload,
                raw_request=raw_request,  # type: ignore[arg-type]
                db=sentinel_db,  # type: ignore[arg-type]
            )

        self.assertIs(result, sentinel_result)

        mock_verify.assert_called_once_with(
            exact_body,
            exact_headers["x-timestamp"],
            exact_headers["x-signature"],
            exact_headers["x-api-key"],
        )
        mock_normalize_server.assert_called_once_with(
            SERVER_FOREIGN,
            default="",
        )
        mock_current_server.assert_called_once_with()

        mock_resolve.assert_awaited_once_with(
            sentinel_db,
            identity=payload.requester_identity,
        )
        self.assertEqual(mock_resolve.await_count, 1)
        resolve_args, resolve_kwargs = mock_resolve.await_args
        self.assertIs(resolve_args[0], sentinel_db)
        self.assertIs(resolve_kwargs["identity"], payload.requester_identity)

        mock_create.assert_awaited_once()
        self.assertEqual(mock_create.await_count, 1)
        _, create_kwargs = mock_create.await_args
        created_invite = create_kwargs["invite"]
        self.assertIsInstance(created_invite, InvitationCreate)
        self.assertEqual(created_invite.account_name, payload.account_name)
        self.assertEqual(created_invite.mobile_number, payload.mobile_number)
        self.assertEqual(created_invite.role, payload.role)
        self.assertIs(create_kwargs["db"], sentinel_db)
        self.assertIs(create_kwargs["admin"], sentinel_admin)

    async def test_create_invitation_internal_from_bot_rejected_signature_short_circuits(
        self,
    ) -> None:
        payload, exact_body, exact_headers = _build_valid_payload_and_headers()
        raw_request = DummyRawRequest(body=exact_body, headers=exact_headers)

        sentinel_db = object()

        mock_verify = mock.Mock(return_value=False)
        mock_normalize_server = mock.Mock(side_effect=normalize_server)
        mock_current_server = mock.Mock(return_value=SERVER_IRAN)
        mock_build_key = mock.Mock(wraps=build_standard_invitation_idempotency_key)
        mock_resolve = mock.AsyncMock()
        mock_create = mock.AsyncMock()

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
            new=mock_create,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await create_invitation_internal_from_bot(
                    payload=payload,
                    raw_request=raw_request,  # type: ignore[arg-type]
                    db=sentinel_db,  # type: ignore[arg-type]
                )

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Invalid internal invitation signature")

        mock_verify.assert_called_once_with(
            exact_body,
            exact_headers["x-timestamp"],
            exact_headers["x-signature"],
            exact_headers["x-api-key"],
        )

        mock_normalize_server.assert_not_called()
        mock_current_server.assert_not_called()
        mock_build_key.assert_not_called()
        mock_resolve.assert_not_called()
        mock_create.assert_not_called()

    async def test_create_invitation_internal_from_bot_rejects_wrong_authority_or_header_source(
        self,
    ) -> None:
        sentinel_db = object()

        # Case 1: valid signed Finland-source request on SERVER_FOREIGN authority -> HTTP 403
        with self.subTest("foreign_authority_rejected_403"):
            payload, exact_body, exact_headers = _build_valid_payload_and_headers(
                header_source=SERVER_FOREIGN,
            )
            raw_request = DummyRawRequest(body=exact_body, headers=exact_headers)

            mock_verify = mock.Mock(return_value=True)
            mock_normalize_server = mock.Mock(side_effect=normalize_server)
            mock_current_server = mock.Mock(return_value=SERVER_FOREIGN)
            mock_build_key = mock.Mock(wraps=build_standard_invitation_idempotency_key)
            mock_resolve = mock.AsyncMock()
            mock_create = mock.AsyncMock()

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
                new=mock_create,
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await create_invitation_internal_from_bot(
                        payload=payload,
                        raw_request=raw_request,  # type: ignore[arg-type]
                        db=sentinel_db,  # type: ignore[arg-type]
                    )

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(
                ctx.exception.detail,
                "Invitation creation is Iran-authoritative",
            )

            mock_verify.assert_called_once_with(
                exact_body,
                exact_headers["x-timestamp"],
                exact_headers["x-signature"],
                exact_headers["x-api-key"],
            )
            mock_normalize_server.assert_called_once_with(
                SERVER_FOREIGN,
                default="",
            )
            mock_current_server.assert_called_once_with()
            mock_build_key.assert_not_called()
            mock_resolve.assert_not_called()
            mock_create.assert_not_called()

        # Case 2: valid signed payload on SERVER_IRAN authority with non-Finland header -> HTTP 401
        with self.subTest("non_finland_header_source_rejected_401"):
            payload, exact_body, exact_headers = _build_valid_payload_and_headers(
                header_source="iran",
            )
            raw_request = DummyRawRequest(body=exact_body, headers=exact_headers)

            mock_verify = mock.Mock(return_value=True)
            mock_normalize_server = mock.Mock(side_effect=normalize_server)
            mock_current_server = mock.Mock(return_value=SERVER_IRAN)
            mock_build_key = mock.Mock(wraps=build_standard_invitation_idempotency_key)
            mock_resolve = mock.AsyncMock()
            mock_create = mock.AsyncMock()

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
                new=mock_create,
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await create_invitation_internal_from_bot(
                        payload=payload,
                        raw_request=raw_request,  # type: ignore[arg-type]
                        db=sentinel_db,  # type: ignore[arg-type]
                    )

            self.assertEqual(ctx.exception.status_code, 401)
            self.assertEqual(
                ctx.exception.detail,
                "Invalid internal invitation source",
            )

            mock_verify.assert_called_once_with(
                exact_body,
                exact_headers["x-timestamp"],
                exact_headers["x-signature"],
                exact_headers["x-api-key"],
            )
            mock_normalize_server.assert_called_once_with(
                "iran",
                default="",
            )
            mock_current_server.assert_called_once_with()
            mock_build_key.assert_not_called()
            mock_resolve.assert_not_called()
            mock_create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
