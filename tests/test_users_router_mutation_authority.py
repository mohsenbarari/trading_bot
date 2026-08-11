import inspect
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.params import Depends

import schemas
from api.routers.users import (
    _ensure_actor_can_mutate_target,
    delete_user,
    terminate_user_sessions,
    update_user,
)
from core.enums import UserAccountStatus, UserRole


SELF_MUTATION_DETAIL = "مدیر نمی‌تواند عملیات حساس مدیریتی را روی حساب خودش انجام دهد"
MIDDLE_ADMIN_TARGET_DETAIL = "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند"
SUPER_ADMIN_PEER_DETAIL = "مدیر ارشد نمی‌تواند عملیات حساس مدیریتی را روی حساب مدیر ارشد انجام دهد"


def make_actor(user_id: int, role: UserRole):
    return SimpleNamespace(id=user_id, role=role)


def make_target(**overrides):
    data = {
        "id": 20,
        "role": UserRole.STANDARD,
        "is_deleted": False,
        "deleted_at": None,
        "telegram_id": 999,
        "account_status": UserAccountStatus.ACTIVE,
        "trading_restricted_until": None,
        "max_daily_trades": None,
        "max_active_commodities": None,
        "max_daily_requests": None,
        "limitations_expire_at": None,
        "max_sessions": 1,
        "can_block_users": True,
        "max_blocked_users": 10,
        "max_accountants": 3,
        "max_customers": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.commits = 0
        self.refreshes = 0

    async def get(self, _model, _user_id):
        return self.user

    async def commit(self):
        self.commits += 1

    async def refresh(self, _user):
        self.refreshes += 1


class UsersRouterMutationAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def assert_all_sensitive_routes_rejected(self, *, actor, target, detail):
        routes = (
            (
                "update",
                lambda: update_user(
                    target.id,
                    schemas.UserUpdate(max_daily_trades=1),
                    db=FakeDB(target),
                    actor=actor,
                ),
            ),
            (
                "delete",
                lambda: delete_user(target.id, db=FakeDB(target), actor=actor),
            ),
            (
                "terminate_sessions",
                lambda: terminate_user_sessions(target.id, db=FakeDB(target), actor=actor),
            ),
        )

        for route_name, invoke in routes:
            with self.subTest(route=route_name, actor_role=actor.role, target_role=target.role):
                with self.assertRaises(HTTPException) as exc_info:
                    await invoke()
                self.assertEqual(exc_info.exception.status_code, 403)
                self.assertEqual(exc_info.exception.detail, detail)

    async def test_middle_manager_self_mutations_are_read_only_on_every_route(self):
        actor = make_actor(10, UserRole.MIDDLE_MANAGER)
        await self.assert_all_sensitive_routes_rejected(
            actor=actor,
            target=make_target(id=10, role=UserRole.MIDDLE_MANAGER),
            detail=SELF_MUTATION_DETAIL,
        )

    async def test_super_admin_self_mutations_are_read_only_on_every_route(self):
        actor = make_actor(10, UserRole.SUPER_ADMIN)
        await self.assert_all_sensitive_routes_rejected(
            actor=actor,
            target=make_target(id=10, role=UserRole.SUPER_ADMIN),
            detail=SELF_MUTATION_DETAIL,
        )

    async def test_super_admin_peer_mutations_are_read_only_on_every_route(self):
        await self.assert_all_sensitive_routes_rejected(
            actor=make_actor(10, UserRole.SUPER_ADMIN),
            target=make_target(id=20, role=UserRole.SUPER_ADMIN),
            detail=SUPER_ADMIN_PEER_DETAIL,
        )

    async def test_middle_manager_cannot_mutate_another_admin_on_every_route(self):
        await self.assert_all_sensitive_routes_rejected(
            actor=make_actor(10, UserRole.MIDDLE_MANAGER),
            target=make_target(id=20, role=UserRole.MIDDLE_MANAGER),
            detail=MIDDLE_ADMIN_TARGET_DETAIL,
        )

    async def test_update_rejects_each_sensitive_payload_before_side_effects_for_self(self):
        actor = make_actor(10, UserRole.SUPER_ADMIN)
        target = make_target(id=10, role=UserRole.SUPER_ADMIN)
        updates = (
            schemas.UserUpdate(account_status=UserAccountStatus.INACTIVE),
            schemas.UserUpdate(role=UserRole.STANDARD),
            schemas.UserUpdate(max_daily_trades=1, max_sessions=2),
            schemas.UserUpdate(trading_restricted_until=datetime(2026, 8, 11, 12, 0, 0)),
        )

        for user_update in updates:
            db = FakeDB(target)
            with self.subTest(update_fields=sorted(user_update.model_dump(exclude_unset=True))):
                with self.assertRaises(HTTPException) as exc_info:
                    await update_user(target.id, user_update, db=db, actor=actor)
                self.assertEqual(exc_info.exception.status_code, 403)
                self.assertEqual(exc_info.exception.detail, SELF_MUTATION_DETAIL)
                self.assertEqual(db.commits, 0)

    async def test_middle_manager_can_mutate_non_admin_through_all_sensitive_routes(self):
        actor = make_actor(10, UserRole.MIDDLE_MANAGER)

        update_target = make_target(id=20, role=UserRole.STANDARD)
        update_db = FakeDB(update_target)
        with patch("api.routers.users.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "api.routers.users.track_limitation_changes", return_value=([], False, False)
        ), patch("api.routers.users.sync_mandatory_channel_for_user_state_change", new=AsyncMock()), patch(
            "core.cache.invalidate_user_cache", new=AsyncMock()
        ), patch("api.routers.users.attach_customer_user_context", new=AsyncMock(return_value=update_target)), patch(
            "api.routers.users.serialize_user_read", side_effect=lambda user: user
        ), patch("api.routers.users.audit_log"):
            result = await update_user(
                update_target.id,
                schemas.UserUpdate(max_daily_trades=1, max_sessions=2),
                db=update_db,
                actor=actor,
            )

        self.assertIs(result, update_target)
        self.assertEqual(update_target.max_sessions, 2)
        self.assertEqual(update_db.commits, 1)

        delete_target = make_target(id=21, role=UserRole.STANDARD)
        with patch("api.routers.users.delete_user_account", new=AsyncMock()) as delete_mock, patch(
            "api.routers.users.audit_log"
        ):
            deleted = await delete_user(delete_target.id, db=FakeDB(delete_target), actor=actor)

        delete_mock.assert_awaited_once()
        self.assertEqual(deleted, {"message": "User deleted successfully"})

        session_target = make_target(id=22, role=UserRole.STANDARD)
        with patch("api.routers.users.force_clear_sessions", new=AsyncMock(return_value=3)) as terminate_mock, patch(
            "api.routers.users.audit_log"
        ):
            terminated = await terminate_user_sessions(
                session_target.id,
                db=FakeDB(session_target),
                actor=actor,
            )

        terminate_mock.assert_awaited_once_with(unittest.mock.ANY, session_target.id)
        self.assertEqual(terminated["terminated_sessions"], 3)

    def test_super_admin_can_mutate_lower_role_targets(self):
        actor = make_actor(10, UserRole.SUPER_ADMIN)

        for target_role in (UserRole.MIDDLE_MANAGER, UserRole.STANDARD):
            with self.subTest(target_role=target_role):
                target = make_target(id=20, role=target_role)
                self.assertIs(_ensure_actor_can_mutate_target(actor, target), actor)

    def test_every_sensitive_route_declares_the_shared_write_authority_guard(self):
        routes = (
            (update_user, "update"),
            (delete_user, "delete"),
            (terminate_user_sessions, "terminate_sessions"),
        )

        for route, operation in routes:
            with self.subTest(route=route.__name__):
                dependency = inspect.signature(route).parameters["_admin_authority"].default
                self.assertIsInstance(dependency, Depends)
                with patch("core.admin_authority.current_server", return_value="foreign"):
                    with self.assertRaises(HTTPException) as exc_info:
                        dependency.dependency()
                self.assertEqual(exc_info.exception.status_code, 409)
                self.assertEqual(exc_info.exception.detail["table"], "users")
                self.assertEqual(exc_info.exception.detail["operation"], operation)


if __name__ == "__main__":
    unittest.main()
