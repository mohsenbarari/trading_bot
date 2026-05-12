import unittest
from types import SimpleNamespace

from models.user import UserRole

from api.routers.users_public import search_public_users


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, results):
        self.results = list(results)
        self.last_stmt = None
        self.stmts = []

    async def execute(self, stmt):
        self.last_stmt = stmt
        self.stmts.append(stmt)
        if not self.results:
            raise AssertionError("Unexpected execute() call")
        return self.results.pop(0)


def make_user(**overrides):
    data = {
        "id": 7,
        "is_deleted": False,
        "account_name": "user7",
        "role": UserRole.STANDARD,
        "mobile_number": "09120000000",
        "address": "تهران",
        "avatar_file_id": None,
        "created_at": __import__("datetime").datetime(2026, 1, 1),
        "trades_count": 0,
        "last_seen_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_relation(**overrides):
    owner_user = overrides.pop("owner_user", make_user(id=20, account_name="owner20"))
    data = {
        "accountant_user_id": 7,
        "owner_user": owner_user,
        "relation_display_name": "حسابدار مالک",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class UsersPublicRouterSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_public_users_without_query_returns_db_rows(self):
        current_user = SimpleNamespace(id=5)
        rows = [make_user(id=7), make_user(id=6)]
        db = FakeDB([FakeExecuteResult(rows), FakeExecuteResult([])])

        result = await search_public_users(q=None, limit=25, db=db, current_user=current_user)

        self.assertEqual([item.id for item in result], [7, 6])
        stmt_text = str(db.stmts[0])
        self.assertIn("users.is_deleted = false", stmt_text.lower())
        self.assertIn("users.id !=", stmt_text.lower())

    async def test_search_public_users_with_query_adds_search_filters_and_limit(self):
        current_user = SimpleNamespace(id=5)
        rows = [make_user(id=9)]
        db = FakeDB([FakeExecuteResult(rows), FakeExecuteResult([])])

        result = await search_public_users(q="ali", limit=10, db=db, current_user=current_user)

        self.assertEqual([item.id for item in result], [9])
        stmt_text = str(db.stmts[0]).lower()
        self.assertIn("lower(users.full_name) like lower", stmt_text)
        self.assertIn("lower(users.account_name) like lower", stmt_text)
        self.assertIn("lower(users.username) like lower", stmt_text)
        self.assertIn("lower(users.mobile_number) like lower", stmt_text)

    async def test_search_public_users_resolves_accountants_to_owner_profiles_and_deduplicates(self):
        current_user = SimpleNamespace(id=5)
        owner_user = make_user(id=20, account_name="owner20")
        rows = [
            make_user(id=7, account_name="acct7"),
            owner_user,
            make_user(id=8, account_name="acct8"),
            make_user(id=30, account_name="plain30"),
        ]
        db = FakeDB([
            FakeExecuteResult(rows),
            FakeExecuteResult([
                make_relation(accountant_user_id=7, owner_user=owner_user, relation_display_name="حسابدار فروش"),
                make_relation(accountant_user_id=8, owner_user=owner_user, relation_display_name="حسابدار دوم"),
            ]),
        ])

        result = await search_public_users(q="owner", limit=10, db=db, current_user=current_user)

        self.assertEqual([item.id for item in result], [20, 30])
        self.assertEqual(result[0].resolved_from_accountant_id, 7)
        self.assertEqual(result[0].highlight_accountant_user_id, 7)
        self.assertEqual(result[0].highlight_accountant_relation_display_name, "حسابدار فروش")

    async def test_search_public_users_skips_owner_resolved_to_current_user(self):
        current_user = SimpleNamespace(id=5)
        current_owner = make_user(id=5, account_name="owner5")
        rows = [make_user(id=44, account_name="acct44")]
        db = FakeDB([
            FakeExecuteResult(rows),
            FakeExecuteResult([
                make_relation(accountant_user_id=44, owner_user=current_owner, relation_display_name="حسابدار خودم"),
            ]),
        ])

        result = await search_public_users(q="acct", limit=10, db=db, current_user=current_user)

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()