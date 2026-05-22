import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.users_public import read_public_user
from models.customer_relation import CustomerTier
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
        current_user = SimpleNamespace(id=99, role=UserRole.STANDARD)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[]),
        ):
            result = await read_public_user(7, db=db, current_user=current_user)

        self.assertEqual(result.id, user.id)
        self.assertEqual(result.account_name, user.account_name)
        self.assertIsNone(result.resolved_from_accountant_id)
        self.assertEqual(result.accountant_relations, [])
        self.assertEqual(result.customer_relations, [])
        self.assertIsNone(result.customer_management_name)
        self.assertNotIn("role", result.model_dump())
        self.assertEqual(db.calls[0][1], 7)

    async def test_read_public_user_raises_404_for_missing_or_deleted_user(self):
        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(8, db=FakeDB(None), current_user=SimpleNamespace(id=77, role=UserRole.STANDARD))
        self.assertEqual(exc_info.exception.status_code, 404)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    9,
                    db=FakeDB(SimpleNamespace(id=9, is_deleted=True)),
                    current_user=SimpleNamespace(id=77, role=UserRole.STANDARD),
                )
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
        active_relation = SimpleNamespace(
            accountant_user=SimpleNamespace(id=44, account_name="acct44", is_deleted=False),
            relation_display_name="حسابدار فروش",
            duty_description="پیگیری معاملات",
        )
        db = FakeDB(None)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, relation]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[active_relation]),
        ), patch(
            "api.routers.users_public.list_active_customers_for_owner",
            new=AsyncMock(return_value=[]),
        ):
            result = await read_public_user(
                44,
                db=db,
                current_user=SimpleNamespace(id=99, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, owner_user.id)
        self.assertEqual(result.account_name, owner_user.account_name)
        self.assertEqual(result.resolved_from_accountant_id, 44)
        self.assertEqual(result.highlight_accountant_user_id, 44)
        self.assertEqual(result.highlight_accountant_relation_display_name, "حسابدار فروش")
        self.assertEqual(len(result.accountant_relations), 1)
        self.assertEqual(result.accountant_relations[0].accountant_user_id, 44)
        self.assertEqual(result.accountant_relations[0].relation_display_name, "حسابدار فروش")
        self.assertNotIn("role", result.model_dump())
        self.assertEqual(db.calls, [])

    async def test_read_public_user_returns_customer_context_for_owner_tree_viewer(self):
        customer_user = SimpleNamespace(
            id=91,
            is_deleted=False,
            account_name="customer91",
            role=UserRole.STANDARD,
            mobile_number="09127777777",
            address="شیراز",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 3),
            trades_count=3,
            last_seen_at=None,
        )
        owner_user = SimpleNamespace(id=21, account_name="owner21", is_deleted=False)
        relation = SimpleNamespace(
            owner_user_id=21,
            customer_user_id=91,
            owner_user=owner_user,
            customer_user=customer_user,
            management_name="مشتری ویژه",
            customer_tier=CustomerTier.TIER_2,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ):
            result = await read_public_user(
                91,
                db=FakeDB(None),
                current_user=SimpleNamespace(id=21, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, 91)
        self.assertEqual(result.customer_owner_user_id, 21)
        self.assertEqual(result.customer_owner_account_name, "owner21")
        self.assertEqual(result.customer_management_name, "مشتری ویژه")
        self.assertEqual(result.customer_tier, CustomerTier.TIER_2)

    async def test_read_public_user_hides_customer_profile_from_middle_manager(self):
        customer_user = SimpleNamespace(id=91, is_deleted=False)
        owner_user = SimpleNamespace(id=21, account_name="owner21", is_deleted=False)
        relation = SimpleNamespace(
            owner_user_id=21,
            customer_user_id=91,
            owner_user=owner_user,
            customer_user=customer_user,
            management_name="مشتری ویژه",
            customer_tier=CustomerTier.TIER_1,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=relation),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    91,
                    db=FakeDB(None),
                    current_user=SimpleNamespace(id=501, role=UserRole.MIDDLE_MANAGER),
                )

        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_read_public_user_includes_customer_list_for_super_admin_on_owner_profile(self):
        owner_user = SimpleNamespace(
            id=21,
            is_deleted=False,
            account_name="owner21",
            role=UserRole.STANDARD,
            mobile_number="09124444444",
            address="مشهد",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 2),
            trades_count=7,
            last_seen_at=None,
        )
        customer_relation = SimpleNamespace(
            customer_user=SimpleNamespace(id=91, account_name="customer91", is_deleted=False),
            management_name="مشتری ویژه",
            customer_tier=CustomerTier.TIER_1,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[]),
        ), patch(
            "api.routers.users_public.list_active_customers_for_owner",
            new=AsyncMock(return_value=[customer_relation]),
        ):
            result = await read_public_user(
                21,
                db=FakeDB(owner_user),
                current_user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )

        self.assertEqual(result.id, 21)
        self.assertEqual(len(result.customer_relations), 1)
        self.assertEqual(result.customer_relations[0].customer_user_id, 91)
        self.assertEqual(result.customer_relations[0].management_name, "مشتری ویژه")


if __name__ == "__main__":
    unittest.main()