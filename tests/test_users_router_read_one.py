import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from api.routers.users import read_user


class FakeDB:
    def __init__(self, user):
        self.user = user

    async def get(self, model, user_id):
        return self.user


class UsersRouterReadOneTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_user_returns_user_when_found(self):
        user = SimpleNamespace(id=7)
        result = await read_user(7, db=FakeDB(user))
        self.assertIs(result, user)

    async def test_read_user_raises_404_when_missing(self):
        with self.assertRaises(HTTPException) as exc_info:
            await read_user(7, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()