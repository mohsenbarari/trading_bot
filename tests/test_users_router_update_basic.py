import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

import schemas
from api.routers.users import update_user
from api.routers.users import terminate_user_sessions
from core.enums import UserRole


def make_user(**overrides):
    data = {
        "id": 5,
        "telegram_id": 999,
        "role": UserRole.WATCH,
        "is_deleted": False,
        "deleted_at": None,
        "has_bot_access": True,
        "trading_restricted_until": None,
        "max_daily_trades": None,
        "max_active_commodities": None,
        "max_daily_requests": None,
        "limitations_expire_at": None,
        "trades_count": 1,
        "commodities_traded_count": 2,
        "channel_messages_count": 3,
        "max_sessions": 1,
        "max_accountants": 3,
        "can_block_users": True,
        "max_blocked_users": 10,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.commits = 0
        self.refreshes = 0

    async def get(self, model, user_id):
        return self.user

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshes += 1


class UsersRouterUpdateBasicTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_user_raises_404_when_missing(self):
        with self.assertRaises(HTTPException) as exc_info:
            await update_user(5, schemas.UserUpdate(), db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_middle_manager_cannot_change_roles(self):
        user = make_user()

        with self.assertRaises(HTTPException) as exc_info:
            await update_user(
                5,
                schemas.UserUpdate(role=UserRole.STANDARD),
                db=FakeDB(user),
                actor=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, 'فقط مدیر ارشد می‌تواند نقش کاربر را تغییر دهد')

    async def test_middle_manager_cannot_manage_admin_targets(self):
        user = make_user(role=UserRole.MIDDLE_MANAGER)

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_user_sessions(
                5,
                db=FakeDB(user),
                actor=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, 'مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند')

    async def test_update_user_updates_role_bot_access_and_owner_limits(self):
        user = make_user()
        db = FakeDB(user)
        update = schemas.UserUpdate(role=UserRole.STANDARD, has_bot_access=False, max_sessions=99, max_accountants=6)

        with patch("api.routers.users.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "api.routers.users.track_limitation_changes", return_value=([], False, False)
        ), patch(
            "api.routers.users.sync_mandatory_channel_for_user_state_change", new=AsyncMock()
        ) as mandatory_sync_mock, patch(
            "api.routers.users.invalidate_user_cache", new=AsyncMock(), create=True
        ) as invalidate_mock, patch("core.cache.invalidate_user_cache", new=AsyncMock()) as cache_mock, patch(
            "api.routers.users.send_bot_access_notification", new=AsyncMock()
        ) as bot_notify_mock, patch("api.routers.users.send_block_notification", new=AsyncMock()) as block_mock, patch(
            "api.routers.users.send_limitation_notification", new=AsyncMock()
        ) as limit_mock, patch("api.routers.users.asyncio.create_task") as create_task_mock:
            result = await update_user(5, update, db=db)

        self.assertIs(result, user)
        self.assertEqual(user.role, UserRole.STANDARD)
        self.assertFalse(user.has_bot_access)
        self.assertEqual(user.max_sessions, 3)
        self.assertEqual(user.max_accountants, 6)
        self.assertEqual(db.commits, 1)
        self.assertEqual(db.refreshes, 1)
        mandatory_sync_mock.assert_awaited_once_with(
            db,
            user=user,
            previous_role=UserRole.WATCH,
            previous_is_deleted=False,
            previous_deleted_at=None,
        )
        cache_mock.assert_awaited_once_with(999)
        bot_notify_mock.assert_awaited_once_with(db, user, False)
        block_mock.assert_not_awaited()
        limit_mock.assert_not_awaited()
        create_task_mock.assert_not_called()

    async def test_update_user_persists_block_capability_settings(self):
        user = make_user(can_block_users=True, max_blocked_users=10)
        db = FakeDB(user)
        update = schemas.UserUpdate(can_block_users=False, max_blocked_users=999)

        with patch("api.routers.users.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "api.routers.users.track_limitation_changes", return_value=([], False, False)
        ), patch(
            "api.routers.users.sync_mandatory_channel_for_user_state_change", new=AsyncMock()
        ), patch(
            "core.cache.invalidate_user_cache", new=AsyncMock()
        ), patch(
            "api.routers.users.send_bot_access_notification", new=AsyncMock()
        ), patch(
            "api.routers.users.send_block_notification", new=AsyncMock()
        ), patch(
            "api.routers.users.send_limitation_notification", new=AsyncMock()
        ), patch("api.routers.users.asyncio.create_task"):
            result = await update_user(5, update, db=db)

        self.assertIs(result, user)
        self.assertFalse(user.can_block_users)
        self.assertEqual(user.max_blocked_users, 100)

    async def test_update_user_clamps_accountant_bot_access_and_session_cap(self):
        user = make_user(has_bot_access=False, max_sessions=1)
        db = FakeDB(user)
        db.execute = AsyncMock()
        update = schemas.UserUpdate(has_bot_access=True, max_sessions=3)

        with patch("api.routers.users.is_user_accountant", new=AsyncMock(return_value=True)), patch(
            "api.routers.users.track_limitation_changes", return_value=([], False, False)
        ), patch(
            "api.routers.users.sync_mandatory_channel_for_user_state_change", new=AsyncMock()
        ), patch(
            "core.cache.invalidate_user_cache", new=AsyncMock()
        ), patch(
            "api.routers.users.send_bot_access_notification", new=AsyncMock()
        ) as bot_notify_mock, patch(
            "api.routers.users.send_block_notification", new=AsyncMock()
        ), patch(
            "api.routers.users.send_limitation_notification", new=AsyncMock()
        ), patch("api.routers.users.asyncio.create_task"):
            result = await update_user(5, update, db=db)

        self.assertIs(result, user)
        self.assertFalse(user.has_bot_access)
        self.assertEqual(user.max_sessions, 1)
        bot_notify_mock.assert_not_awaited()

    async def test_terminate_user_sessions_raises_404_for_missing_or_deleted_user(self):
        with self.assertRaises(HTTPException) as exc_info:
            await terminate_user_sessions(5, db=FakeDB(None))
        self.assertEqual(exc_info.exception.status_code, 404)

        with self.assertRaises(HTTPException) as exc_info:
            await terminate_user_sessions(5, db=FakeDB(make_user(is_deleted=True)))
        self.assertEqual(exc_info.exception.status_code, 404)

    async def test_terminate_user_sessions_clears_all_active_sessions(self):
        user = make_user(id=12)
        db = FakeDB(user)

        with patch("api.routers.users.force_clear_sessions", new=AsyncMock(return_value=4)) as clear_mock:
            result = await terminate_user_sessions(12, db=db)

        clear_mock.assert_awaited_once_with(db, 12)
        self.assertEqual(result, {"detail": "4 نشست پایان یافت", "terminated_sessions": 4})


if __name__ == "__main__":
    unittest.main()