import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.blocks import get_my_block_status, get_my_blocked_users


class BlocksRouterStatusListTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_my_block_status_delegates_to_service(self):
        current_user = SimpleNamespace(id=5)
        payload = {
            "can_block": True,
            "can_block_now": True,
            "max_blocked": 3,
            "current_blocked": 1,
            "remaining": 2,
            "reason_code": None,
            "reason_message": None,
        }

        with patch("api.routers.blocks.get_block_status", new=AsyncMock(return_value=payload)) as service_mock:
            result = await get_my_block_status(db=SimpleNamespace(), current_user=current_user)

        service_mock.assert_awaited_once_with(unittest.mock.ANY, 5)
        self.assertEqual(result, payload)

    async def test_get_my_blocked_users_returns_service_rows(self):
        current_user = SimpleNamespace(id=7)
        rows = [SimpleNamespace(id=9, account_name="ali")]

        with patch("api.routers.blocks.get_blocked_users", new=AsyncMock(return_value=rows)) as service_mock:
            result = await get_my_blocked_users(db=SimpleNamespace(), current_user=current_user)

        service_mock.assert_awaited_once_with(unittest.mock.ANY, 7)
        self.assertEqual(result, rows)

    async def test_get_my_blocked_users_rejects_customer_viewer(self):
        current_user = SimpleNamespace(id=7)
        db = SimpleNamespace(execute=AsyncMock())

        with patch("api.routers.blocks.is_user_customer", new=AsyncMock(return_value=True)):
            with self.assertRaises(HTTPException) as exc_info:
                await get_my_blocked_users(db=db, current_user=current_user)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertIn("مالک", exc_info.exception.detail)


if __name__ == "__main__":
    unittest.main()