import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.users import delete_user


class FakeDB:
    def __init__(self, user):
        self.user = user

    async def get(self, model, user_id):
        return self.user


class UsersRouterDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_user_rejects_missing_and_already_deleted_users(self):
        with self.assertRaises(HTTPException) as exc_info:
            await delete_user(5, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)

        with self.assertRaises(HTTPException) as exc_info:
            await delete_user(5, db=FakeDB(SimpleNamespace(id=5, is_deleted=True)))
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_delete_user_maps_service_errors_and_success(self):
        user = SimpleNamespace(id=5, is_deleted=False)
        with patch("api.routers.users.delete_user_account", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertRaises(HTTPException) as exc_info:
                await delete_user(5, db=FakeDB(user))
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertIn("boom", exc_info.exception.detail)

        with patch("api.routers.users.delete_user_account", new=AsyncMock()) as delete_mock:
            result = await delete_user(5, db=FakeDB(user))

        delete_mock.assert_awaited_once_with(unittest.mock.ANY, user)
        self.assertEqual(result, {"message": "User deleted successfully"})


if __name__ == "__main__":
    unittest.main()