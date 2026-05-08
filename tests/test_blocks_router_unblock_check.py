import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.blocks import check_block_status, unblock_a_user


class BlocksRouterUnblockCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_unblock_a_user_maps_service_failure_and_success(self):
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.blocks.unblock_user", new=AsyncMock(return_value=(False, "not blocked"))):
            with self.assertRaises(HTTPException) as exc_info:
                await unblock_a_user(8, db=SimpleNamespace(), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "not blocked")

        with patch("api.routers.blocks.unblock_user", new=AsyncMock(return_value=(True, "unblocked"))) as service_mock:
            result = await unblock_a_user(8, db=SimpleNamespace(), current_user=current_user)

        service_mock.assert_awaited_once_with(unittest.mock.ANY, 5, 8)
        self.assertEqual(result, {"success": True, "message": "unblocked"})

    async def test_check_block_status_returns_is_blocked_by_me_flag(self):
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.blocks.is_blocked_by", new=AsyncMock(return_value=True)) as service_mock:
            result = await check_block_status(8, db=SimpleNamespace(), current_user=current_user)

        service_mock.assert_awaited_once_with(unittest.mock.ANY, 5, 8)
        self.assertEqual(result, {"user_id": 8, "is_blocked_by_me": True})


if __name__ == "__main__":
    unittest.main()