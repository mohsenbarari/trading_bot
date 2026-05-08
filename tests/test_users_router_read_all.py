import unittest
from types import SimpleNamespace

from api.routers.users import read_all_users


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, result):
        self.result = result
        self.last_stmt = None

    async def execute(self, stmt):
        self.last_stmt = stmt
        return self.result


class UsersRouterReadAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_all_users_filters_deleted_by_default(self):
        rows = [SimpleNamespace(id=1)]
        db = FakeDB(FakeExecuteResult(rows))

        result = await read_all_users(skip=0, limit=50, search=None, include_deleted=False, db=db)

        self.assertEqual(result, rows)
        self.assertIn("users.is_deleted = false", str(db.last_stmt).lower())

    async def test_read_all_users_applies_search_and_can_include_deleted(self):
        rows = [SimpleNamespace(id=2)]
        db = FakeDB(FakeExecuteResult(rows))

        result = await read_all_users(skip=5, limit=10, search="ali", include_deleted=True, db=db)

        self.assertEqual(result, rows)
        stmt_text = str(db.last_stmt).lower()
        self.assertNotIn("users.is_deleted = false", stmt_text)
        self.assertIn("lower(users.full_name) like lower", stmt_text)
        self.assertIn("lower(users.account_name) like lower", stmt_text)
        self.assertIn("lower(users.mobile_number) like lower", stmt_text)


if __name__ == "__main__":
    unittest.main()