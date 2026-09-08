"""Direct-characterization tests for customer-denial boundary in api.routers.blocks.ensure_block_management_allowed.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.blocks import (  # noqa: E402
    CUSTOMER_BLOCK_MANAGEMENT_DETAIL,
    ensure_block_management_allowed,
)
from models.user import UserRole  # noqa: E402


class CharacterizeBlockManagementCustomerDeniedTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing ensure_block_management_allowed customer denial."""

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.is_user_accountant", new_callable=AsyncMock)
    @patch("api.routers.blocks.is_user_customer", new_callable=AsyncMock)
    async def test_ensure_block_management_allowed_customer_denied(
        self,
        mock_is_user_customer: AsyncMock,
        mock_is_user_accountant: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(execute=AsyncMock())
        current_user = SimpleNamespace(id=42, role=UserRole.STANDARD)
        mock_is_user_customer.return_value = True

        # Authoritative literal matches api/routers/blocks.py CUSTOMER_BLOCK_MANAGEMENT_DETAIL (len=43, U+200C at index 38).
        expected_detail = "سیستم بلاک مشتریان توسط مالک مدیریت می\u200cشود."

        with self.assertRaises(HTTPException) as context:
            await ensure_block_management_allowed(fake_db, current_user)

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, expected_detail)
        self.assertEqual(context.exception.detail, CUSTOMER_BLOCK_MANAGEMENT_DETAIL)
        self.assertEqual(CUSTOMER_BLOCK_MANAGEMENT_DETAIL, expected_detail)

        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_is_user_accountant.assert_not_called()
        mock_is_user_accountant.assert_not_awaited()
        fake_db.execute.assert_not_called()
        fake_db.execute.assert_not_awaited()

        mock_audit_log.assert_called_once_with(
            "user.block_management",
            target_type="block_management",
            target_id=current_user.id,
            result="denied",
            actor_id=current_user.id,
            actor_role=UserRole.STANDARD.value,
            reason="customer_cannot_manage_blocks",
        )


if __name__ == "__main__":
    unittest.main()
