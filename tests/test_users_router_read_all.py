import unittest
from types import SimpleNamespace

from api.routers.users import read_all_users


def make_user(**overrides):
    values = {
        "id": 1,
        "telegram_id": None,
        "username": None,
        "full_name": "Ali Reza",
        "account_name": "ali",
        "mobile_number": "09120000000",
        "role": "عادی",
        "has_bot_access": False,
        "created_at": __import__("datetime").datetime(2026, 1, 1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, result):
        self.result = result
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self.result


class UsersRouterReadAllTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_all_users_filters_deleted_by_default(self):
        rows = [make_user(id=1)]
        db = FakeDB(FakeExecuteResult(rows))

        result = await read_all_users(skip=0, limit=50, search=None, include_deleted=False, db=db)

        self.assertEqual([item.id for item in result], [1])
        stmt_text = str(db.statements[0]).lower()
        self.assertIn("users.is_deleted = false", stmt_text)
        self.assertIn("case", stmt_text)
        self.assertIn("customer_relations", stmt_text)
        self.assertIn("accountant_relations", stmt_text)

    async def test_read_all_users_applies_search_and_can_include_deleted(self):
        rows = [make_user(id=2)]
        db = FakeDB(FakeExecuteResult(rows))

        result = await read_all_users(skip=5, limit=10, search="ali", include_deleted=True, db=db)

        self.assertEqual([item.id for item in result], [2])
        stmt_text = str(db.statements[0]).lower()
        self.assertNotIn("users.is_deleted = false", stmt_text)
        self.assertIn("lower(users.full_name) like lower", stmt_text)
        self.assertIn("lower(users.account_name) like lower", stmt_text)
        self.assertIn("lower(users.mobile_number) like lower", stmt_text)
        self.assertIn("customer_relations", stmt_text)
        self.assertIn("accountant_relations", stmt_text)


if __name__ == "__main__":
    unittest.main()
