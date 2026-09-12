"""Hermetic direct characterization for create_invitation handler.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-POST-INVITATIONS-CREATE-INVITATION-L320
Target: api.routers.invitations.create_invitation

Note: This unit provides direct characterization for seed
API-IDENTITY-POST-INVITATIONS-CREATE-INVITATION-L320 and the actual
api.routers.invitations.create_invitation handler under P1-00b network isolation.
This unit does not update inventory counts or close P1-00b.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException
from api.routers.invitations import InvitationCreate, create_invitation
from models.user import UserRole


class P100bCreateInvitationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic handler-level characterization tests for create_invitation."""

    async def test_create_invitation_success_forwards_arguments_and_returns_helper_result(
        self,
    ) -> None:
        invite = InvitationCreate(
            account_name="test_characterization_account",
            mobile_number="09123456789",
            role=UserRole.WATCH,
        )
        sentinel_db = object()
        sentinel_admin = object()
        sentinel_result = object()

        mock_helper = mock.AsyncMock(return_value=sentinel_result)

        with mock.patch(
            "api.routers.invitations._create_standard_invitation",
            new=mock_helper,
        ):
            result = await create_invitation(
                invite=invite,
                db=sentinel_db,
                admin=sentinel_admin,
            )

        self.assertIs(result, sentinel_result)
        mock_helper.assert_awaited_once_with(
            invite=invite,
            db=sentinel_db,
            admin=sentinel_admin,
        )
        self.assertEqual(mock_helper.await_count, 1)
        _, kwargs = mock_helper.call_args
        self.assertIs(kwargs["invite"], invite)
        self.assertIs(kwargs["db"], sentinel_db)
        self.assertIs(kwargs["admin"], sentinel_admin)

    async def test_create_invitation_propagates_http_exception_unchanged(self) -> None:
        invite = InvitationCreate(
            account_name="test_characterization_http_error",
            mobile_number="09123456788",
            role=UserRole.WATCH,
        )
        sentinel_db = object()
        sentinel_admin = object()
        expected_exc = HTTPException(
            status_code=409,
            detail="sentinel_http_conflict_error",
        )

        mock_helper = mock.AsyncMock(side_effect=expected_exc)

        with mock.patch(
            "api.routers.invitations._create_standard_invitation",
            new=mock_helper,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await create_invitation(
                    invite=invite,
                    db=sentinel_db,
                    admin=sentinel_admin,
                )

        self.assertIs(ctx.exception, expected_exc)
        mock_helper.assert_awaited_once_with(
            invite=invite,
            db=sentinel_db,
            admin=sentinel_admin,
        )
        self.assertEqual(mock_helper.await_count, 1)
        _, kwargs = mock_helper.call_args
        self.assertIs(kwargs["invite"], invite)
        self.assertIs(kwargs["db"], sentinel_db)
        self.assertIs(kwargs["admin"], sentinel_admin)

    async def test_create_invitation_propagates_unexpected_dependency_failure_unchanged(
        self,
    ) -> None:
        invite = InvitationCreate(
            account_name="test_characterization_runtime_error",
            mobile_number="09123456787",
            role=UserRole.WATCH,
        )
        sentinel_db = object()
        sentinel_admin = object()
        expected_exc = RuntimeError("sentinel_unexpected_dependency_failure")

        mock_helper = mock.AsyncMock(side_effect=expected_exc)

        with mock.patch(
            "api.routers.invitations._create_standard_invitation",
            new=mock_helper,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await create_invitation(
                    invite=invite,
                    db=sentinel_db,
                    admin=sentinel_admin,
                )

        self.assertIs(ctx.exception, expected_exc)
        mock_helper.assert_awaited_once_with(
            invite=invite,
            db=sentinel_db,
            admin=sentinel_admin,
        )
        self.assertEqual(mock_helper.await_count, 1)
        _, kwargs = mock_helper.call_args
        self.assertIs(kwargs["invite"], invite)
        self.assertIs(kwargs["db"], sentinel_db)
        self.assertIs(kwargs["admin"], sentinel_admin)


if __name__ == "__main__":
    unittest.main()
