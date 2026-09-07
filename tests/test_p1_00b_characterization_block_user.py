"""Direct-characterization tests for api.routers.blocks.block_a_user.

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

from api.routers.blocks import User, block_a_user  # noqa: E402


class CharacterizeBlockUserTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing block_a_user."""

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_success(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(is_deleted=False))
        )
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_message = "sentinel block success"
        mock_ensure_block_management_allowed.return_value = None
        mock_block_user.return_value = (True, sentinel_message)

        result = await block_a_user(
            user_id=target_user_id,
            db=fake_db,
            current_user=current_user,
        )

        self.assertEqual(result, {"success": True, "message": sentinel_message})
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_awaited_once_with(User, target_user_id)
        mock_block_user.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )
        mock_audit_log.assert_called_once_with(
            "user.block",
            target_type="user",
            target_id=target_user_id,
            actor_id=current_user.id,
            actor_role="admin",
            after_summary={"blocked": True},
        )

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_guard_failure(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(get=AsyncMock())
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_error = RuntimeError("sentinel guard failure")
        mock_ensure_block_management_allowed.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await block_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_not_awaited()
        fake_db.get.assert_not_called()
        mock_block_user.assert_not_awaited()
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_missing_target_none(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(get=AsyncMock(return_value=None))
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        mock_ensure_block_management_allowed.return_value = None

        with self.assertRaises(HTTPException) as context:
            await block_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "کاربر یافت نشد.")
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_awaited_once_with(User, target_user_id)
        mock_block_user.assert_not_awaited()
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_missing_target_deleted(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(is_deleted=True))
        )
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        mock_ensure_block_management_allowed.return_value = None

        with self.assertRaises(HTTPException) as context:
            await block_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "کاربر یافت نشد.")
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_awaited_once_with(User, target_user_id)
        mock_block_user.assert_not_awaited()
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_false_result(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(is_deleted=False))
        )
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_message = "sentinel block failure message"
        mock_ensure_block_management_allowed.return_value = None
        mock_block_user.return_value = (False, sentinel_message)

        with self.assertRaises(HTTPException) as context:
            await block_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(context.exception.detail, sentinel_message)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_awaited_once_with(User, target_user_id)
        mock_block_user.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_db_get_exception_propagation(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        sentinel_error = RuntimeError("sentinel db.get failure")
        fake_db = SimpleNamespace(get=AsyncMock(side_effect=sentinel_error))
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        mock_ensure_block_management_allowed.return_value = None

        with self.assertRaises(RuntimeError) as context:
            await block_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_awaited_once_with(User, target_user_id)
        mock_block_user.assert_not_awaited()
        mock_audit_log.assert_not_called()

    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_block_a_user_block_user_exception_propagation(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_block_user: AsyncMock,
        mock_audit_log: Any,
    ) -> None:
        fake_db = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(is_deleted=False))
        )
        current_user = SimpleNamespace(id=42, role="admin")
        target_user_id = 101
        sentinel_error = RuntimeError("sentinel block_user failure")
        mock_ensure_block_management_allowed.return_value = None
        mock_block_user.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await block_a_user(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        fake_db.get.assert_awaited_once_with(User, target_user_id)
        mock_block_user.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )
        mock_audit_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
