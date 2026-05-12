import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

import schemas
from api.routers.users import update_user
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

    async def test_update_user_updates_role_bot_access_and_owner_limits(self):
        user = make_user()
        db = FakeDB(user)
        update = schemas.UserUpdate(role=UserRole.STANDARD, has_bot_access=False, max_sessions=99, max_accountants=6)

        with patch("api.routers.users.track_limitation_changes", return_value=([], False, False)), patch(
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


if __name__ == "__main__":
    unittest.main()