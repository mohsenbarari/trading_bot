import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.blocks import block_a_user


class FakeDB:
    def __init__(self, target_user):
        self.target_user = target_user

    async def get(self, model, user_id):
        return self.target_user


class BlocksRouterBlockTests(unittest.IsolatedAsyncioTestCase):
    async def test_block_a_user_rejects_missing_or_deleted_target(self):
        current_user = SimpleNamespace(id=5)

        with self.assertRaises(HTTPException) as exc_info:
            await block_a_user(8, db=FakeDB(None), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

        with self.assertRaises(HTTPException) as exc_info:
            await block_a_user(8, db=FakeDB(SimpleNamespace(id=8, is_deleted=True)), current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_block_a_user_maps_service_failure_and_success(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB(SimpleNamespace(id=8, is_deleted=False))

        with patch("api.routers.blocks.block_user", new=AsyncMock(return_value=(False, "cannot block"))):
            with self.assertRaises(HTTPException) as exc_info:
                await block_a_user(8, db=db, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "cannot block")

        with patch("api.routers.blocks.block_user", new=AsyncMock(return_value=(True, "blocked"))) as service_mock:
            result = await block_a_user(8, db=db, current_user=current_user)

        service_mock.assert_awaited_once_with(unittest.mock.ANY, 5, 8)
        self.assertEqual(result, {"success": True, "message": "blocked"})


if __name__ == "__main__":
    unittest.main()