import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.users_public import list_project_users_directory
from models.user import UserRole


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, results):
        self.results = list(results)
        self.stmts = []

    async def execute(self, stmt):
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
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class UsersPublicProjectUsersTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_project_users_directory_returns_filtered_rows_for_self_profile(self):
        current_user = SimpleNamespace(id=7, role=UserRole.STANDARD)
        db = FakeDB([
            FakeExecuteResult([
                make_user(id=8, account_name="acct8", role=UserRole.WATCH, mobile_number="09120000008"),
                make_user(id=9, account_name="manager9", role=UserRole.MIDDLE_MANAGER, mobile_number="09120000009"),
            ])
        ])

        accountant_lookup = AsyncMock(side_effect=AssertionError("self profile should not load accountant relation"))
        with patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_lookup,
        ):
            result = await list_project_users_directory(7, q="0912", limit=25, db=db, current_user=current_user)

        accountant_lookup.assert_not_awaited()
        self.assertEqual([row.id for row in result], [8, 9])
        self.assertEqual(result[0].account_name, "acct8")
        self.assertEqual(result[0].mobile_number, "09120000008")
        self.assertEqual(result[0].model_dump(), {
            "id": 8,
            "account_name": "acct8",
            "mobile_number": "09120000008",
            "created_at": None,
        })

        stmt_text = str(db.stmts[0]).lower()
        self.assertIn("users.role in", stmt_text)
        self.assertIn("accountant_relations", stmt_text)
        self.assertIn("customer_relations", stmt_text)
        self.assertIn("not (exists", stmt_text)
        self.assertIn("users.id !=", stmt_text)
        self.assertIn("users.created_at desc", stmt_text)
        self.assertIn("nulls last", stmt_text)
        self.assertIn("users.id desc", stmt_text)
        self.assertIn("lower(users.account_name) like lower", stmt_text)
        self.assertIn("lower(users.mobile_number) like lower", stmt_text)

    async def test_list_project_users_directory_allows_active_accountant_for_owner_profile(self):
        db = FakeDB([
            FakeExecuteResult([
                make_user(
                    id=20,
                    account_name="owner20",
                    mobile_number="09120000020",
                    created_at=datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc),
                ),
                make_user(id=9, account_name="manager9", role=UserRole.MIDDLE_MANAGER, mobile_number="09120000009"),
            ])
        ])

        with patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=20)),
        ):
            result = await list_project_users_directory(
                20,
                q=None,
                limit=25,
                db=db,
                current_user=SimpleNamespace(id=44, role=UserRole.STANDARD),
            )

        self.assertEqual([row.id for row in result], [20, 9])
        self.assertEqual(result[0].created_at, datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc))

    async def test_list_project_users_directory_denies_unrelated_requests(self):
        db = FakeDB([])

        with patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await list_project_users_directory(
                    7,
                    q=None,
                    limit=25,
                    db=db,
                    current_user=SimpleNamespace(id=99, role=UserRole.STANDARD),
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(db.stmts, [])

    async def test_list_project_users_directory_denies_customers(self):
        db = FakeDB([])

        with patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=21)),
        ), patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await list_project_users_directory(
                    91,
                    q=None,
                    limit=25,
                    db=db,
                    current_user=SimpleNamespace(id=91, role=UserRole.STANDARD),
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(db.stmts, [])


if __name__ == "__main__":
    unittest.main()
