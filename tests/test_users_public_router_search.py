import unittest
from types import SimpleNamespace

from api.routers.users_public import search_public_users


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


class UsersPublicRouterSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_public_users_without_query_returns_db_rows(self):
        current_user = SimpleNamespace(id=5)
        rows = [SimpleNamespace(id=7), SimpleNamespace(id=6)]
        db = FakeDB(FakeExecuteResult(rows))

        result = await search_public_users(q=None, limit=25, db=db, current_user=current_user)

        self.assertEqual(result, rows)
        stmt_text = str(db.last_stmt)
        self.assertIn("users.is_deleted = false", stmt_text.lower())
        self.assertIn("users.id !=", stmt_text.lower())

    async def test_search_public_users_with_query_adds_search_filters_and_limit(self):
        current_user = SimpleNamespace(id=5)
        rows = [SimpleNamespace(id=9)]
        db = FakeDB(FakeExecuteResult(rows))

        result = await search_public_users(q="ali", limit=10, db=db, current_user=current_user)

        self.assertEqual(result, rows)
        stmt_text = str(db.last_stmt).lower()
        self.assertIn("lower(users.full_name) like lower", stmt_text)
        self.assertIn("lower(users.account_name) like lower", stmt_text)
        self.assertIn("lower(users.username) like lower", stmt_text)
        self.assertIn("lower(users.mobile_number) like lower", stmt_text)


if __name__ == "__main__":
    unittest.main()