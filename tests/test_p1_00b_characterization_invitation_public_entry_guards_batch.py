"""Hermetic shared characterization batch for invitation public entry guards.

Gap: docs/refactor/two-server/section-1/06-current-drift-register.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seeds:
  - API-IDENTITY-GET-INVITATIONS-LOOKUP-INVITATION-L382
  - API-IDENTITY-GET-INVITATIONS-VALIDATE-INVITATION-L437
Targets:
  - api.routers.invitations.lookup_invitation
  - api.routers.invitations.validate_invitation
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import HTTPException, status

from api.routers.invitations import (
    lookup_invitation,
    validate_invitation,
)
from core.services.invitation_public_access_service import (
    PUBLIC_INVITATION_SECURITY_HEADERS,
    public_invitation_http_exception as real_public_invitation_http_exception,
)


class InvitationPublicEntryGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic handler characterization tests for public invitation entry guards."""

    async def _assert_public_entry_guard_lifecycle(
        self,
        handler_coroutine,
        *,
        expected_status: int,
        expected_detail: str,
    ) -> HTTPException:
        fence = install_p1_00_network_fence()
        before_denied = len(fence["denied_connects"])
        before_redis = len(fence["live_redis_constructs"])

        with self.assertRaises(HTTPException) as ctx:
            await handler_coroutine

        exc = ctx.exception
        self.assertEqual(exc.status_code, expected_status)
        self.assertEqual(exc.detail, expected_detail)
        self.assertEqual(exc.headers, PUBLIC_INVITATION_SECURITY_HEADERS)
        self.assertIsNot(exc.headers, PUBLIC_INVITATION_SECURITY_HEADERS)
        self.assertEqual(
            PUBLIC_INVITATION_SECURITY_HEADERS,
            {
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

        self.assertEqual(len(fence["denied_connects"]), before_denied)
        self.assertEqual(len(fence["live_redis_constructs"]), before_redis)
        return exc

    async def test_lookup_invitation_rejects_noncanonical_short_code_before_reads(
        self,
    ) -> None:
        mock_public_exception = mock.Mock(
            name="public_invitation_http_exception",
            wraps=real_public_invitation_http_exception,
        )
        mock_select = mock.Mock(
            name="select",
            side_effect=AssertionError("select called unexpectedly"),
        )
        mock_audit_log = mock.Mock(
            name="audit_log",
            side_effect=AssertionError("audit_log called unexpectedly"),
        )
        mock_pending_accountant = mock.AsyncMock(
            name="get_pending_accountant_relation_by_invitation_token",
            side_effect=AssertionError(
                "get_pending_accountant_relation_by_invitation_token awaited unexpectedly"
            ),
        )
        mock_pending_customer = mock.AsyncMock(
            name="get_pending_customer_relation_by_invitation_token",
            side_effect=AssertionError(
                "get_pending_customer_relation_by_invitation_token awaited unexpectedly"
            ),
        )
        mock_db_execute = mock.AsyncMock(
            name="db.execute",
            side_effect=AssertionError("db.execute awaited unexpectedly"),
        )
        strict_db = SimpleNamespace(execute=mock_db_execute)

        short_code = "ABC!1234"
        self.assertEqual(len(short_code), 8)
        self.assertFalse(short_code.isalnum())

        with (
            mock.patch(
                "api.routers.invitations.public_invitation_http_exception",
                new=mock_public_exception,
            ),
            mock.patch("api.routers.invitations.select", new=mock_select),
            mock.patch("api.routers.invitations.audit_log", new=mock_audit_log),
            mock.patch(
                "api.routers.invitations.get_pending_accountant_relation_by_invitation_token",
                new=mock_pending_accountant,
            ),
            mock.patch(
                "api.routers.invitations.get_pending_customer_relation_by_invitation_token",
                new=mock_pending_customer,
            ),
        ):
            await self._assert_public_entry_guard_lifecycle(
                lookup_invitation(short_code=short_code, db=strict_db),
                expected_status=404,
                expected_detail="Invalid short code",
            )

        mock_public_exception.assert_called_once_with(
            status_code=404,
            detail="Invalid short code",
        )
        self.assertEqual(mock_public_exception.call_count, 1)

        mock_select.assert_not_called()
        self.assertEqual(mock_select.call_count, 0)

        mock_pending_accountant.assert_not_called()
        self.assertEqual(mock_pending_accountant.call_count, 0)
        mock_pending_accountant.assert_not_awaited()
        self.assertEqual(mock_pending_accountant.await_count, 0)

        mock_pending_customer.assert_not_called()
        self.assertEqual(mock_pending_customer.call_count, 0)
        mock_pending_customer.assert_not_awaited()
        self.assertEqual(mock_pending_customer.await_count, 0)

        mock_audit_log.assert_not_called()
        self.assertEqual(mock_audit_log.call_count, 0)

        mock_db_execute.assert_not_called()
        self.assertEqual(mock_db_execute.call_count, 0)
        mock_db_execute.assert_not_awaited()
        self.assertEqual(mock_db_execute.await_count, 0)

    async def test_validate_invitation_retires_raw_bearer_without_reads(
        self,
    ) -> None:
        mock_public_exception = mock.Mock(
            name="public_invitation_http_exception",
            wraps=real_public_invitation_http_exception,
        )
        mock_select = mock.Mock(
            name="select",
            side_effect=AssertionError("select called unexpectedly"),
        )
        mock_audit_log = mock.Mock(
            name="audit_log",
            side_effect=AssertionError("audit_log called unexpectedly"),
        )
        mock_pending_accountant = mock.Mock(
            name="get_pending_accountant_relation_by_invitation_token",
            side_effect=AssertionError(
                "get_pending_accountant_relation_by_invitation_token called unexpectedly"
            ),
        )
        mock_pending_customer = mock.Mock(
            name="get_pending_customer_relation_by_invitation_token",
            side_effect=AssertionError(
                "get_pending_customer_relation_by_invitation_token called unexpectedly"
            ),
        )

        raw_bearer_token = "INV-characterization-retired"

        with (
            mock.patch(
                "api.routers.invitations.public_invitation_http_exception",
                new=mock_public_exception,
            ),
            mock.patch("api.routers.invitations.select", new=mock_select),
            mock.patch("api.routers.invitations.audit_log", new=mock_audit_log),
            mock.patch(
                "api.routers.invitations.get_pending_accountant_relation_by_invitation_token",
                new=mock_pending_accountant,
            ),
            mock.patch(
                "api.routers.invitations.get_pending_customer_relation_by_invitation_token",
                new=mock_pending_customer,
            ),
        ):
            await self._assert_public_entry_guard_lifecycle(
                validate_invitation(token=raw_bearer_token),
                expected_status=410,
                expected_detail="Raw invitation validation is retired",
            )

        mock_public_exception.assert_called_once_with(
            status_code=status.HTTP_410_GONE,
            detail="Raw invitation validation is retired",
        )
        self.assertEqual(mock_public_exception.call_count, 1)

        mock_select.assert_not_called()
        self.assertEqual(mock_select.call_count, 0)

        mock_audit_log.assert_not_called()
        self.assertEqual(mock_audit_log.call_count, 0)

        mock_pending_accountant.assert_not_called()
        self.assertEqual(mock_pending_accountant.call_count, 0)

        mock_pending_customer.assert_not_called()
        self.assertEqual(mock_pending_customer.call_count, 0)


if __name__ == "__main__":
    unittest.main()
