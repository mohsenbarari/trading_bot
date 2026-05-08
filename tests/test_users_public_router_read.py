import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from api.routers.users_public import read_public_user


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get(self, model, user_id):
        self.calls.append((model, user_id))
        return self.user


class UsersPublicRouterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_public_user_returns_user_when_present(self):
        user = SimpleNamespace(id=7, is_deleted=False)
        db = FakeDB(user)

        result = await read_public_user(7, db=db)

        self.assertIs(result, user)
        self.assertEqual(db.calls[0][1], 7)

    async def test_read_public_user_raises_404_for_missing_or_deleted_user(self):
        with self.assertRaises(HTTPException) as exc_info:
            await read_public_user(8, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)

        with self.assertRaises(HTTPException) as exc_info:
            await read_public_user(9, db=FakeDB(SimpleNamespace(id=9, is_deleted=True)))
        self.assertEqual(exc_info.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()