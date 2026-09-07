"""Direct-characterization tests for api.routers.blocks.unblock_a_user.

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

from api.routers.blocks import unblock_a_user  # noqa: E402


class CharacterizeUnblockUserTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing unblock_a_user."""

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.unblock_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_unblock_a_user_success(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_message = "sentinel unblock success"
        mock_ensure_block_management_allowed.return_value = None
        mock_unblock_user.return_value = (True, sentinel_message)

        result = await unblock_a_user(
            user_id=target_user_id,
            db=fake_db,
            current_user=current_user,
        )

        self.assertEqual(result, {"success": True, "message": sentinel_message})
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_unblock_user.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )
        mock_audit_log.assert_called_once_with(
            "user.unblock",
            target_type="user",
            target_id=target_user_id,
            actor_id=current_user.id,
            actor_role="admin",
            after_summary={"blocked": False},
        )

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.unblock_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_unblock_a_user_guard_failure(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_error = RuntimeError("sentinel guard failure")
        mock_ensure_block_management_allowed.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await unblock_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_unblock_user.assert_not_awaited()
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.unblock_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_unblock_a_user_false_result(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_message = "sentinel unblock failure message"
        mock_ensure_block_management_allowed.return_value = None
        mock_unblock_user.return_value = (False, sentinel_message)

        with self.assertRaises(HTTPException) as context:
            await unblock_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, sentinel_message)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_unblock_user.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.unblock_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_unblock_a_user_dependency_exception_propagation(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_unblock_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_error = RuntimeError("sentinel unblock_user failure")
        mock_ensure_block_management_allowed.return_value = None
        mock_unblock_user.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await unblock_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_unblock_user.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )
        mock_audit_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
