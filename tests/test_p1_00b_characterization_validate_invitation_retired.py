"""Hermetic direct characterization for validate_invitation unconditional retirement.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437
Target: api.routers.invitations.validate_invitation

Note: This narrow unit neither promotes inventory evidence nor closes P1-00b.
"""

from __future__ import annotations

import contextlib
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException, status
from api.routers.invitations import validate_invitation
from core.services.invitation_public_access_service import (
    PUBLIC_INVITATION_SECURITY_HEADERS,
    public_invitation_http_exception as real_public_invitation_http_exception,
)


class P100bValidateInvitationRetiredCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic handler-level characterization tests for validate_invitation retirement."""

    async def _assert_token_unconditionally_retired_without_downstream(
        self,
        token: str,
    ) -> None:
        mock_public_exception = mock.Mock(
            name="public_invitation_http_exception",
            wraps=real_public_invitation_http_exception,
        )
        mock_select = mock.Mock(name="select")
        mock_get_db = mock.Mock(name="get_db")
        mock_audit_log = mock.Mock(name="audit_log")
        mock_utc_now = mock.Mock(name="utc_now_naive")
        mock_is_canonical_short_code = mock.Mock(
            name="is_canonical_invitation_short_code"
        )
        mock_build_contract_v2 = mock.Mock(name="build_invitation_contract_v2")
        mock_build_public_contract_v2 = mock.Mock(
            name="build_public_invitation_contract_v2"
        )
        mock_derive_state = mock.Mock(name="derive_invitation_state")
        mock_get_new_expiry = mock.Mock(name="get_new_invitation_expiry")
        mock_soft_revoke = mock.AsyncMock(name="soft_revoke_invitation")
        mock_lock_transition = mock.AsyncMock(name="lock_invitation_for_transition")
        mock_is_accountant = mock.Mock(name="is_accountant_invitation_token")
        mock_is_customer = mock.Mock(name="is_customer_invitation_token")
        mock_get_pending_accountant = mock.AsyncMock(
            name="get_pending_accountant_relation_by_invitation_token"
        )
        mock_get_pending_customer = mock.AsyncMock(
            name="get_pending_customer_relation_by_invitation_token"
        )
        mock_create_or_reuse = mock.AsyncMock(
            name="create_or_reuse_canonical_invitation"
        )
        mock_generate_code = mock.Mock(name="generate_invitation_short_code")
        mock_generate_token = mock.Mock(name="generate_invitation_token")
        mock_resolve_requester = mock.AsyncMock(
            name="resolve_current_invitation_requester"
        )
        mock_release_identity = mock.AsyncMock(
            name="release_invitation_identity"
        )
        mock_enforce_access = mock.AsyncMock(
            name="enforce_public_invitation_access"
        )
        mock_send_sms = mock.AsyncMock(name="send_invitation_sms_result")
        mock_deliver_sms = mock.AsyncMock(name="deliver_invitation_sms_once")
        mock_load_sms = mock.AsyncMock(name="load_invitation_sms_status_map")
        mock_prepare_sms = mock.Mock(name="prepare_invitation_sms_delivery")
        mock_verify_admin = mock.AsyncMock(name="verify_admin_user")
        mock_verify_internal_sig = mock.Mock(name="verify_internal_signature")
        mock_get_redis = mock.Mock(name="get_redis_client")
        mock_client_ip = mock.Mock(name="client_ip_from_request")

        patch_targets = {
            "api.routers.invitations.public_invitation_http_exception": mock_public_exception,
            "api.routers.invitations.select": mock_select,
            "api.routers.invitations.get_db": mock_get_db,
            "api.routers.invitations.audit_log": mock_audit_log,
            "api.routers.invitations.utc_now_naive": mock_utc_now,
            "api.routers.invitations.is_canonical_invitation_short_code": mock_is_canonical_short_code,
            "api.routers.invitations.build_invitation_contract_v2": mock_build_contract_v2,
            "api.routers.invitations.build_public_invitation_contract_v2": mock_build_public_contract_v2,
            "api.routers.invitations.derive_invitation_state": mock_derive_state,
            "api.routers.invitations.get_new_invitation_expiry": mock_get_new_expiry,
            "api.routers.invitations.soft_revoke_invitation": mock_soft_revoke,
            "api.routers.invitations.lock_invitation_for_transition": mock_lock_transition,
            "api.routers.invitations.is_accountant_invitation_token": mock_is_accountant,
            "api.routers.invitations.is_customer_invitation_token": mock_is_customer,
            "api.routers.invitations.get_pending_accountant_relation_by_invitation_token": mock_get_pending_accountant,
            "api.routers.invitations.get_pending_customer_relation_by_invitation_token": mock_get_pending_customer,
            "api.routers.invitations.create_or_reuse_canonical_invitation": mock_create_or_reuse,
            "api.routers.invitations.generate_invitation_short_code": mock_generate_code,
            "api.routers.invitations.generate_invitation_token": mock_generate_token,
            "api.routers.invitations.resolve_current_invitation_requester": mock_resolve_requester,
            "api.routers.invitations.release_invitation_identity": mock_release_identity,
            "api.routers.invitations.enforce_public_invitation_access": mock_enforce_access,
            "api.routers.invitations.send_invitation_sms_result": mock_send_sms,
            "api.routers.invitations.deliver_invitation_sms_once": mock_deliver_sms,
            "api.routers.invitations.load_invitation_sms_status_map": mock_load_sms,
            "api.routers.invitations.prepare_invitation_sms_delivery": mock_prepare_sms,
            "api.routers.invitations.verify_admin_user": mock_verify_admin,
            "api.routers.invitations.verify_internal_signature": mock_verify_internal_sig,
            "core.services.invitation_public_access_service.get_redis_client": mock_get_redis,
            "core.services.invitation_public_access_service.client_ip_from_request": mock_client_ip,
        }

        with contextlib.ExitStack() as stack:
            for target, mock_obj in patch_targets.items():
                stack.enter_context(mock.patch(target, new=mock_obj))
            with self.assertRaises(HTTPException) as ctx:
                await validate_invitation(token=token)

        self.assertEqual(ctx.exception.status_code, 410)
        self.assertEqual(ctx.exception.status_code, status.HTTP_410_GONE)
        self.assertEqual(
            ctx.exception.detail,
            "Raw invitation validation is retired",
        )
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

        mock_public_exception.assert_called_once_with(
            status_code=status.HTTP_410_GONE,
            detail="Raw invitation validation is retired",
        )
        self.assertEqual(mock_public_exception.call_count, 1)

        mock_select.assert_not_called()
        self.assertEqual(mock_select.call_count, 0)
        mock_get_db.assert_not_called()
        self.assertEqual(mock_get_db.call_count, 0)

        mock_audit_log.assert_not_called()
        self.assertEqual(mock_audit_log.call_count, 0)
        mock_utc_now.assert_not_called()
        self.assertEqual(mock_utc_now.call_count, 0)

        mock_is_canonical_short_code.assert_not_called()
        self.assertEqual(mock_is_canonical_short_code.call_count, 0)
        mock_build_contract_v2.assert_not_called()
        self.assertEqual(mock_build_contract_v2.call_count, 0)
        mock_build_public_contract_v2.assert_not_called()
        self.assertEqual(mock_build_public_contract_v2.call_count, 0)

        mock_derive_state.assert_not_called()
        self.assertEqual(mock_derive_state.call_count, 0)
        mock_get_new_expiry.assert_not_called()
        self.assertEqual(mock_get_new_expiry.call_count, 0)
        mock_soft_revoke.assert_not_called()
        self.assertEqual(mock_soft_revoke.call_count, 0)
        mock_lock_transition.assert_not_called()
        self.assertEqual(mock_lock_transition.call_count, 0)

        mock_is_accountant.assert_not_called()
        self.assertEqual(mock_is_accountant.call_count, 0)
        mock_is_customer.assert_not_called()
        self.assertEqual(mock_is_customer.call_count, 0)
        mock_get_pending_accountant.assert_not_called()
        self.assertEqual(mock_get_pending_accountant.call_count, 0)
        mock_get_pending_customer.assert_not_called()
        self.assertEqual(mock_get_pending_customer.call_count, 0)

        mock_create_or_reuse.assert_not_called()
        self.assertEqual(mock_create_or_reuse.call_count, 0)
        mock_generate_code.assert_not_called()
        self.assertEqual(mock_generate_code.call_count, 0)
        mock_generate_token.assert_not_called()
        self.assertEqual(mock_generate_token.call_count, 0)
        mock_resolve_requester.assert_not_called()
        self.assertEqual(mock_resolve_requester.call_count, 0)
        mock_release_identity.assert_not_called()
        self.assertEqual(mock_release_identity.call_count, 0)

        mock_enforce_access.assert_not_called()
        self.assertEqual(mock_enforce_access.call_count, 0)

        mock_send_sms.assert_not_called()
        self.assertEqual(mock_send_sms.call_count, 0)
        mock_deliver_sms.assert_not_called()
        self.assertEqual(mock_deliver_sms.call_count, 0)
        mock_load_sms.assert_not_called()
        self.assertEqual(mock_load_sms.call_count, 0)
        mock_prepare_sms.assert_not_called()
        self.assertEqual(mock_prepare_sms.call_count, 0)

        mock_verify_admin.assert_not_called()
        self.assertEqual(mock_verify_admin.call_count, 0)
        mock_verify_internal_sig.assert_not_called()
        self.assertEqual(mock_verify_internal_sig.call_count, 0)

        mock_get_redis.assert_not_called()
        self.assertEqual(mock_get_redis.call_count, 0)
        mock_client_ip.assert_not_called()
        self.assertEqual(mock_client_ip.call_count, 0)

    async def test_validate_invitation_retires_canonical_looking_inv_raw_bearer(
        self,
    ) -> None:
        raw_bearer_token = "INV-78a9c20f5b1d4e3f890123456789abcd"
        self.assertTrue(raw_bearer_token.startswith("INV-"))
        with self.subTest(
            scenario="canonical_looking_inv_raw_bearer",
            token=raw_bearer_token,
        ):
            await self._assert_token_unconditionally_retired_without_downstream(
                raw_bearer_token
            )

    async def test_validate_invitation_retires_eight_character_short_code_shaped_token(
        self,
    ) -> None:
        short_code_shaped_token = "K9X2M4Q7"
        self.assertEqual(len(short_code_shaped_token), 8)
        self.assertTrue(short_code_shaped_token.isalnum())
        with self.subTest(
            scenario="eight_character_short_code_shaped",
            token=short_code_shaped_token,
        ):
            await self._assert_token_unconditionally_retired_without_downstream(
                short_code_shaped_token
            )

    async def test_validate_invitation_retires_malformed_or_empty_token(
        self,
    ) -> None:
        malformed_or_empty_token = ""
        self.assertEqual(len(malformed_or_empty_token), 0)
        with self.subTest(
            scenario="malformed_or_empty",
            token=malformed_or_empty_token,
        ):
            await self._assert_token_unconditionally_retired_without_downstream(
                malformed_or_empty_token
            )


if __name__ == "__main__":
    unittest.main()
