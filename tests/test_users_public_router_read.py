import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.deps import get_current_user
from api.routers.users_public import (
    _build_project_user_directory_stmt,
    _can_view_customer_profile,
    _resolve_public_search_rows,
    read_public_user,
    router,
)
from core.db import get_db
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
    _NON_SELF_PRIVATE_FIELDS = {
        "address",
        "last_seen_at",
        "created_at",
        "created_at_jalali",
        "trades_count",
        "resolved_from_accountant_id",
        "chat_role_kind",
        "chat_role_label",
        "chat_accountant_owner_name",
        "chat_accountant_owner_label",
        "highlight_accountant_user_id",
        "highlight_accountant_relation_display_name",
        "accountant_relations",
        "customer_owner_user_id",
        "customer_owner_account_name",
        "customer_management_name",
        "customer_tier",
        "customer_relations",
    }

    def assert_minimal_non_self_projection(self, result, *, raw_mobile: str, raw_address: str):
        payload = result.model_dump(exclude_none=True)
        self.assertEqual(payload["mobile_number"], f"{raw_mobile[:4]}****{raw_mobile[-3:]}")
        self.assertNotIn(raw_mobile, str(payload))
        self.assertNotIn(raw_address, str(payload))
        self.assertTrue(self._NON_SELF_PRIVATE_FIELDS.isdisjoint(payload))
        return payload

    def test_public_profile_route_omits_none_fields_and_private_data_from_wire_response(self):
        route = next(route for route in router.routes if route.path == "/{user_id}")
        self.assertTrue(route.response_model_exclude_none)

        target = SimpleNamespace(
            id=7,
            is_deleted=False,
            account_name="owner7",
            mobile_number="09120000007",
            address="نشانی خصوصی",
            avatar_file_id=None,
        )
        viewer = SimpleNamespace(id=99, role=UserRole.STANDARD)
        app = FastAPI()
        app.include_router(router)

        async def override_db():
            yield FakeDB(target)

        async def override_current_user():
            return viewer

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_current_user
        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), TestClient(app) as client:
            response = client.get("/7")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "id": 7,
            "account_name": "owner7",
            "mobile_number": "0912****007",
        })
        self.assertNotIn(target.mobile_number, response.text)
        self.assertNotIn(target.address, response.text)

    async def test_customer_authorization_helpers_and_empty_search_rows(self):
        relation = SimpleNamespace(owner_user_id=21, customer_user_id=91)
        self.assertTrue(
            _can_view_customer_profile(
                SimpleNamespace(id=91, role=UserRole.STANDARD),
                relation,
                viewer_accountant_relation=None,
            )
        )
        self.assertFalse(
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

    def test_project_users_directory_excludes_any_customer_or_accountant_relation_history(self):
        stmt = _build_project_user_directory_stmt(current_user_id=5, q=None, limit=25, offset=0)
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))

        self.assertIn("customer_relations", compiled)
        self.assertIn("accountant_relations", compiled)
        self.assertNotIn("customer_relations.status", compiled)
        self.assertNotIn("accountant_relations.status", compiled)
        self.assertNotIn("customer_relations.deleted_at", compiled)
        self.assertNotIn("accountant_relations.deleted_at", compiled)

    async def test_read_public_user_returns_masked_minimal_projection_for_normal_peer(self):
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
        ):
            result = await read_public_user(7, db=db, current_user=current_user)

        self.assertEqual(result.id, user.id)
        self.assertEqual(result.account_name, user.account_name)
        payload = self.assert_minimal_non_self_projection(
            result,
            raw_mobile=user.mobile_number,
            raw_address=user.address,
        )
        self.assertEqual(set(payload), {"id", "account_name", "mobile_number"})
        self.assertEqual(db.calls[0][1], 7)

    async def test_read_public_user_returns_full_mobile_and_address_only_for_exact_self_request(self):
        current_user = SimpleNamespace(
            id=44,
            is_deleted=False,
            account_name="accountant44",
            role=UserRole.STANDARD,
            mobile_number="09124444444",
            address="آدرس شخصی حسابدار",
            avatar_file_id=None,
            created_at=__import__("datetime").datetime(2026, 1, 1),
            trades_count=9,
            last_seen_at=__import__("datetime").datetime(2026, 1, 4, 8, 30, 0),
        )
        accountant_lookup = AsyncMock(side_effect=AssertionError("self must not resolve to owner"))

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_lookup,
        ):
            result = await read_public_user(44, db=FakeDB(None), current_user=current_user)

        accountant_lookup.assert_not_awaited()
        self.assertEqual(result.model_dump(exclude_none=True), {
            "id": 44,
            "account_name": "accountant44",
            "mobile_number": "09124444444",
            "address": "آدرس شخصی حسابدار",
        })

    async def test_read_public_user_does_not_upgrade_owner_when_accountant_target_resolves_to_owner(self):
        owner = SimpleNamespace(
            id=21,
            is_deleted=False,
            account_name="owner21",
            role=UserRole.STANDARD,
            mobile_number="09120000021",
            address="آدرس مالک",
            avatar_file_id=None,
        )
        relation = SimpleNamespace(owner_user=owner, relation_display_name="حسابدار فروش")

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await read_public_user(44, db=FakeDB(None), current_user=owner)

        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=owner.mobile_number,
            raw_address=owner.address,
        )

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
        ):
            result = await read_public_user(
                20,
                db=FakeDB(owner),
                current_user=SimpleNamespace(id=91, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, 20)
        self.assertEqual(result.account_name, "owner20")
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=owner.mobile_number,
            raw_address=owner.address,
        )

    async def test_read_public_user_raises_404_for_missing_or_deleted_user(self):
        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
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
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    9,
                    db=FakeDB(SimpleNamespace(id=9, is_deleted=True)),
                    current_user=SimpleNamespace(id=77, role=UserRole.STANDARD),
                )
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_read_public_user_resolves_accountant_to_minimal_owner_profile(self):
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
        relation = SimpleNamespace(
            owner_user=owner_user,
            accountant_user=SimpleNamespace(id=44),
            relation_display_name="حسابدار فروش",
        )
        db = FakeDB(None)

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await read_public_user(
                44,
                db=db,
                current_user=SimpleNamespace(id=99, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, owner_user.id)
        self.assertEqual(result.account_name, owner_user.account_name)
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=owner_user.mobile_number,
            raw_address=owner_user.address,
        )
        self.assertEqual(db.calls, [])

    async def test_read_public_user_does_not_grant_super_admin_pii_through_public_profile(self):
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
        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=relation),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await read_public_user(
                44,
                db=FakeDB(None),
                current_user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )

        self.assertEqual(result.id, 21)
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=owner_user.mobile_number,
            raw_address=owner_user.address,
        )

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

    async def test_read_public_user_keeps_owner_customer_authorization_but_hides_customer_context(self):
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
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=customer_user.mobile_number,
            raw_address=customer_user.address,
        )

    async def test_read_public_user_keeps_accountant_customer_authorization_but_hides_customer_context(self):
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
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=customer_user.mobile_number,
            raw_address=customer_user.address,
        )

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

    async def test_read_public_user_hides_owner_customer_list_from_super_admin_public_profile(self):
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
        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ):
            result = await read_public_user(
                21,
                db=FakeDB(owner_user),
                current_user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )

        self.assertEqual(result.id, 21)
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=owner_user.mobile_number,
            raw_address=owner_user.address,
        )

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
        ):
            result = await read_public_user(
                44,
                db=FakeDB(None),
                current_user=SimpleNamespace(id=91, role=UserRole.STANDARD),
            )

        self.assertEqual(result.id, 21)
        self.assertEqual(result.account_name, "owner_principal")
        self.assert_minimal_non_self_projection(
            result,
            raw_mobile=owner_user.mobile_number,
            raw_address=owner_user.address,
        )


if __name__ == "__main__":
    unittest.main()
