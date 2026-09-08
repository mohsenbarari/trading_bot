"""Direct-characterization tests for accountant-denial boundary in api.routers.blocks.ensure_block_management_allowed.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.blocks import (  # noqa: E402
    ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL,
    ensure_block_management_allowed,
)
from models.user import UserRole  # noqa: E402


class CharacterizeBlockManagementAccountantDeniedTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing ensure_block_management_allowed accountant denial."""

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.is_user_accountant", new_callable=AsyncMock)
    @patch("api.routers.blocks.is_user_customer", new_callable=AsyncMock)
    async def test_ensure_block_management_allowed_accountant_denied(
        self,
        mock_is_user_customer: AsyncMock,
        mock_is_user_accountant: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(execute=AsyncMock())
        current_user = SimpleNamespace(id=42, role=UserRole.STANDARD)
        mock_is_user_customer.return_value = False
        mock_is_user_accountant.return_value = True

        call_tracker = Mock()
        call_tracker.attach_mock(mock_is_user_customer, "is_user_customer")
        call_tracker.attach_mock(mock_is_user_accountant, "is_user_accountant")

        expected_detail = "قابلیت بلاک کاربران فقط در اختیار سرگروه است."

        with self.assertRaises(HTTPException) as context:
            await ensure_block_management_allowed(fake_db, current_user)

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, expected_detail)
        self.assertEqual(context.exception.detail, ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL)
        self.assertEqual(ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL, expected_detail)

        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_is_user_accountant.assert_awaited_once_with(fake_db, current_user.id)
        self.assertEqual(
            call_tracker.mock_calls,
            [
                call.is_user_customer(fake_db, current_user.id),
                call.is_user_accountant(fake_db, current_user.id),
            ],
        )
        fake_db.execute.assert_not_called()
        fake_db.execute.assert_not_awaited()

        mock_audit_log.assert_called_once_with(
            "user.block_management",
            target_type="block_management",
            target_id=42,
            result="denied",
            actor_id=42,
            actor_role=UserRole.STANDARD.value,
            reason="accountant_cannot_manage_blocks",
        )


if __name__ == "__main__":
    unittest.main()
