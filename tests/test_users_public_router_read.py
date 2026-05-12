import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.users_public import read_public_user
from models.user import UserRole


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.calls = []

    async def get(self, model, user_id):
        self.calls.append((model, user_id))
        return self.user


class UsersPublicRouterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_public_user_returns_user_when_present(self):
        user = SimpleNamespace(
            id=7,
            is_deleted=False,
            account_name="owner",
            role=UserRole.STANDARD,
            mobile_number="09120000000",
            address="تهران",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 1),
            trades_count=0,
            last_seen_at=None,
        )
        db = FakeDB(user)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            result = await read_public_user(7, db=db)

        self.assertEqual(result.id, user.id)
        self.assertEqual(result.account_name, user.account_name)
        self.assertIsNone(result.resolved_from_accountant_id)
        self.assertEqual(db.calls[0][1], 7)

    async def test_read_public_user_raises_404_for_missing_or_deleted_user(self):
        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(8, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(9, db=FakeDB(SimpleNamespace(id=9, is_deleted=True)))
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_read_public_user_resolves_active_accountant_to_owner_profile(self):
        owner_user = SimpleNamespace(
            id=21,
            is_deleted=False,
            account_name="owner_principal",
            role=UserRole.STANDARD,
            mobile_number="09124444444",
            address="مشهد",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 2),
            trades_count=7,
            last_seen_at=None,
        )
        relation = SimpleNamespace(
            owner_user=owner_user,
            relation_display_name="حسابدار فروش",
        )
        db = FakeDB(None)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ):
            result = await read_public_user(44, db=db)

        self.assertEqual(result.id, owner_user.id)
        self.assertEqual(result.account_name, owner_user.account_name)
        self.assertEqual(result.resolved_from_accountant_id, 44)
        self.assertEqual(result.highlight_accountant_user_id, 44)
        self.assertEqual(result.highlight_accountant_relation_display_name, "حسابدار فروش")
        self.assertEqual(db.calls, [])


if __name__ == "__main__":
    unittest.main()