import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.users_public import (
    _can_view_customer_profile,
    _resolve_public_search_rows,
    _serialize_public_accountant_relation,
    _serialize_public_customer_relation,
    read_public_user,
)
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
    async def test_users_public_helper_shortcuts_and_empty_search_rows(self):
        self.assertIsNone(_serialize_public_accountant_relation(SimpleNamespace(accountant_user=None)))
        self.assertIsNone(
            _serialize_public_customer_relation(
                SimpleNamespace(customer_user=SimpleNamespace(is_deleted=True))
            )
        )

        relation = SimpleNamespace(owner_user_id=21, customer_user_id=91)
        self.assertTrue(
            _can_view_customer_profile(
                SimpleNamespace(id=91, role=UserRole.STANDARD),
                relation,
                viewer_accountant_relation=None,
            )
        )
        self.assertTrue(
            _can_view_customer_profile(
                SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                relation,
                viewer_accountant_relation=None,
            )
        )

        rows = await _resolve_public_search_rows(
            FakeDB(None),
            [],
            current_user=SimpleNamespace(id=99, role=UserRole.STANDARD),
        )
        self.assertEqual(rows, [])

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

    async def test_read_public_user_denies_customer_viewer_for_outside_public_profile(self):
        user = SimpleNamespace(
            id=30,
            is_deleted=False,
            account_name="outside30",
            role=UserRole.STANDARD,
            mobile_number="09120000030",
            address="تهران",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 1),
            trades_count=0,
            last_seen_at=None,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
                new=AsyncMock(side_effect=lambda _db, user_id: SimpleNamespace(owner_user_id=20) if user_id == 91 else None),
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[20, 44]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    30,
                    db=FakeDB(user),
                    current_user=SimpleNamespace(id=91, role=UserRole.STANDARD),
                )

        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_read_public_user_allows_customer_viewer_for_owner_public_profile(self):
        owner = SimpleNamespace(
            id=20,
            is_deleted=False,
            account_name="owner20",
            role=UserRole.STANDARD,
            mobile_number="09120000020",
            address="تهران",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 1),
            trades_count=0,
            last_seen_at=None,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
                new=AsyncMock(side_effect=lambda _db, user_id: SimpleNamespace(owner_user_id=20) if user_id == 91 else None),
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[20, 44, 1]),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[]),
        ):
            result = await read_public_user(
                20,
                db=FakeDB(owner),
                current_user=SimpleNamespace(id=91, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, 20)
        self.assertEqual(result.account_name, "owner20")

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
            last_seen_at=__import__("datetime").datetime(2026, 1, 4, 12, 0, 0),
        )
        accountant_last_seen_at = __import__("datetime").datetime(2026, 1, 4, 8, 30, 0)
        relation = SimpleNamespace(
            owner_user=owner_user,
            accountant_user=SimpleNamespace(id=44, last_seen_at=accountant_last_seen_at),
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
            new=AsyncMock(return_value=relation),
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
        self.assertEqual(result.last_seen_at, accountant_last_seen_at)
        self.assertEqual(len(result.accountant_relations), 1)
        self.assertEqual(result.accountant_relations[0].accountant_user_id, 44)
        self.assertEqual(result.accountant_relations[0].relation_display_name, "حسابدار فروش")
        self.assertNotIn("role", result.model_dump())
        self.assertEqual(db.calls, [])

    async def test_read_public_user_resolved_accountant_profile_includes_customer_list_for_super_admin(self):
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
        relation = SimpleNamespace(owner_user=owner_user, relation_display_name="حسابدار فروش")
        active_relation = SimpleNamespace(
            accountant_user=SimpleNamespace(id=44, account_name="acct44", is_deleted=False),
            relation_display_name="حسابدار فروش",
            duty_description="پیگیری معاملات",
        )
        customer_relation = SimpleNamespace(
            customer_user=SimpleNamespace(id=91, account_name="customer91", is_deleted=False),
            management_name="مشتری ویژه",
            customer_tier=CustomerTier.TIER_1,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[active_relation]),
        ), patch(
            "api.routers.users_public.list_active_customers_for_owner",
            new=AsyncMock(return_value=[customer_relation]),
        ):
            result = await read_public_user(
                44,
                db=FakeDB(None),
                current_user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )

        self.assertEqual(result.id, 21)
        self.assertEqual(len(result.customer_relations), 1)
        self.assertEqual(result.customer_relations[0].customer_user_id, 91)

    async def test_read_public_user_raises_404_for_customer_relation_without_live_customer_user(self):
        relation = SimpleNamespace(
            owner_user_id=21,
            customer_user_id=91,
            owner_user=SimpleNamespace(id=21, account_name="owner21", is_deleted=False),
            customer_user=None,
            management_name="مشتری ویژه",
            customer_tier=CustomerTier.TIER_1,
        )

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=lambda _db, user_id: relation if user_id == 91 else None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    91,
                    db=FakeDB(None),
                    current_user=SimpleNamespace(id=21, role=UserRole.STANDARD),
                )

        self.assertEqual(exc_info.exception.status_code, 404)

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
            new=AsyncMock(side_effect=lambda _db, user_id: relation if user_id == 91 else None),
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

    async def test_read_public_user_returns_customer_context_for_same_owner_accountant_viewer(self):
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
            last_seen_at=__import__("datetime").datetime(2026, 1, 4, 8, 30, 0),
        )
        owner_user = SimpleNamespace(id=21, account_name="owner21", is_deleted=False)
        relation = SimpleNamespace(
            owner_user_id=21,
            customer_user_id=91,
            owner_user=owner_user,
            customer_user=customer_user,
            management_name="مشتری ویژه",
            customer_tier=CustomerTier.TIER_1,
        )
        viewer_accountant_relation = SimpleNamespace(owner_user_id=21)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, viewer_accountant_relation]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=lambda _db, user_id: relation if user_id == 91 else None),
        ):
            result = await read_public_user(
                91,
                db=FakeDB(None),
                current_user=SimpleNamespace(id=44, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, 91)
        self.assertEqual(result.account_name, "customer91")
        self.assertEqual(result.mobile_number, "09127777777")
        self.assertEqual(result.address, "شیراز")
        self.assertEqual(result.last_seen_at, __import__("datetime").datetime(2026, 1, 4, 8, 30, 0))
        self.assertEqual(result.customer_owner_user_id, 21)
        self.assertEqual(result.customer_owner_account_name, "owner21")
        self.assertEqual(result.customer_management_name, "مشتری ویژه")
        self.assertEqual(result.customer_tier, CustomerTier.TIER_1)
        self.assertNotIn("role", result.model_dump())

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
            new=AsyncMock(side_effect=lambda _db, user_id: relation if user_id == 91 else None),
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

    async def test_read_public_user_owner_resolves_shared_group_accountant_for_customer_viewer(self):
        owner_user = SimpleNamespace(
            id=21,
            is_deleted=False,
            account_name="owner_principal",
            role=UserRole.STANDARD,
            mobile_number="09120000021",
            address="تهران",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 1),
            trades_count=0,
            last_seen_at=None,
        )
        relation = SimpleNamespace(owner_user=owner_user, relation_display_name="حسابدار گروه")

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=lambda _db, user_id: SimpleNamespace(owner_user_id=21) if user_id == 91 else None),
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[21, 44, 1]),
        ), patch(
            "api.routers.users_public.list_active_accountants_for_owner",
            new=AsyncMock(return_value=[]),
        ), patch(
            "api.routers.users_public.list_active_customers_for_owner",
            new=AsyncMock(return_value=[]),
        ):
            result = await read_public_user(
                44,
                db=FakeDB(None),
                current_user=SimpleNamespace(id=91, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, 21)
        self.assertEqual(result.account_name, "owner_principal")
        self.assertEqual(result.resolved_from_accountant_id, 44)
        self.assertEqual(result.highlight_accountant_user_id, 44)


if __name__ == "__main__":
    unittest.main()
