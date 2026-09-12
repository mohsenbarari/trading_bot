"""Hermetic direct characterization for lookup_invitation invalid short-code rejection.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382
Target: api.routers.invitations.lookup_invitation

Note: This narrow unit neither promotes inventory evidence nor closes P1-00b.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from api.routers.invitations import lookup_invitation
from core.invitation_contract_service import (
    is_canonical_invitation_short_code as real_is_canonical_invitation_short_code,
)
from core.services.invitation_public_access_service import (
    PUBLIC_INVITATION_SECURITY_HEADERS,
)


class P100bLookupInvitationInvalidShortCodeCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic handler-level characterization tests for lookup_invitation invalid short-code rejection."""

    async def _assert_invalid_short_code_rejected_before_downstream(
        self,
        invalid_short_code: str,
    ) -> None:
        sentinel_db = mock.AsyncMock(spec=AsyncSession)

        mock_validator = mock.Mock(wraps=real_is_canonical_invitation_short_code)
        mock_select = mock.Mock(name="select")
        mock_derive_state = mock.Mock(name="derive_invitation_state")
        mock_is_accountant_token = mock.Mock(name="is_accountant_invitation_token")
        mock_is_customer_token = mock.Mock(name="is_customer_invitation_token")
        mock_get_pending_accountant = mock.AsyncMock(
            name="get_pending_accountant_relation_by_invitation_token"
        )
        mock_get_pending_customer = mock.AsyncMock(
            name="get_pending_customer_relation_by_invitation_token"
        )
        mock_utc_now = mock.Mock(name="utc_now_naive")
        mock_audit = mock.Mock(name="audit_log")
        mock_model_dump = mock.Mock(name="contract_model_dump")
        contract_sentinel = mock.Mock(
            name="contract_sentinel",
            model_dump=mock_model_dump,
        )
        mock_build_contract = mock.Mock(
            name="build_public_invitation_contract_v2",
            return_value=contract_sentinel,
        )

        with (
            mock.patch(
                "api.routers.invitations.is_canonical_invitation_short_code",
                new=mock_validator,
            ),
            mock.patch(
                "api.routers.invitations.select",
                new=mock_select,
            ),
            mock.patch(
                "api.routers.invitations.derive_invitation_state",
                new=mock_derive_state,
            ),
            mock.patch(
                "api.routers.invitations.is_accountant_invitation_token",
                new=mock_is_accountant_token,
            ),
            mock.patch(
                "api.routers.invitations.is_customer_invitation_token",
                new=mock_is_customer_token,
            ),
            mock.patch(
                "api.routers.invitations.get_pending_accountant_relation_by_invitation_token",
                new=mock_get_pending_accountant,
            ),
            mock.patch(
                "api.routers.invitations.get_pending_customer_relation_by_invitation_token",
                new=mock_get_pending_customer,
            ),
            mock.patch(
                "api.routers.invitations.utc_now_naive",
                new=mock_utc_now,
            ),
            mock.patch(
                "api.routers.invitations.audit_log",
                new=mock_audit,
            ),
            mock.patch(
                "api.routers.invitations.build_public_invitation_contract_v2",
                new=mock_build_contract,
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await lookup_invitation(
                    short_code=invalid_short_code,
                    db=sentinel_db,
                )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "Invalid short code")
        self.assertEqual(
            ctx.exception.headers,
            PUBLIC_INVITATION_SECURITY_HEADERS,
        )
        self.assertIsNot(
            ctx.exception.headers,
            PUBLIC_INVITATION_SECURITY_HEADERS,
        )
        self.assertEqual(
            PUBLIC_INVITATION_SECURITY_HEADERS,
            {
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

        mock_validator.assert_called_once_with(invalid_short_code)
        self.assertEqual(mock_validator.call_count, 1)

        mock_select.assert_not_called()
        self.assertEqual(mock_select.call_count, 0)

        sentinel_db.execute.assert_not_awaited()
        self.assertEqual(sentinel_db.execute.await_count, 0)
        self.assertEqual(sentinel_db.execute.call_count, 0)
        sentinel_db.assert_not_awaited()
        self.assertEqual(sentinel_db.await_count, 0)
        self.assertEqual(sentinel_db.call_count, 0)

        mock_derive_state.assert_not_called()
        self.assertEqual(mock_derive_state.call_count, 0)

        mock_is_accountant_token.assert_not_called()
        self.assertEqual(mock_is_accountant_token.call_count, 0)

        mock_is_customer_token.assert_not_called()
        self.assertEqual(mock_is_customer_token.call_count, 0)

        mock_get_pending_accountant.assert_not_called()
        self.assertEqual(mock_get_pending_accountant.call_count, 0)
        mock_get_pending_accountant.assert_not_awaited()
        self.assertEqual(mock_get_pending_accountant.await_count, 0)

        mock_get_pending_customer.assert_not_called()
        self.assertEqual(mock_get_pending_customer.call_count, 0)
        mock_get_pending_customer.assert_not_awaited()
        self.assertEqual(mock_get_pending_customer.await_count, 0)

        mock_utc_now.assert_not_called()
        self.assertEqual(mock_utc_now.call_count, 0)

        mock_audit.assert_not_called()
        self.assertEqual(mock_audit.call_count, 0)

        mock_build_contract.assert_not_called()
        self.assertEqual(mock_build_contract.call_count, 0)

        mock_model_dump.assert_not_called()
        self.assertEqual(mock_model_dump.call_count, 0)

    async def test_lookup_invitation_rejects_code_shorter_than_eight_alphanumeric_characters(
        self,
    ) -> None:
        short_code_too_short = "ABC1234"
        self.assertLess(len(short_code_too_short), 8)
        self.assertTrue(short_code_too_short.isalnum())
        await self._assert_invalid_short_code_rejected_before_downstream(
            short_code_too_short
        )

    async def test_lookup_invitation_rejects_eight_character_code_containing_punctuation(
        self,
    ) -> None:
        short_code_with_punctuation = "ABC!1234"
        self.assertEqual(len(short_code_with_punctuation), 8)
        self.assertFalse(short_code_with_punctuation.isalnum())
        await self._assert_invalid_short_code_rejected_before_downstream(
            short_code_with_punctuation
        )


if __name__ == "__main__":
    unittest.main()
