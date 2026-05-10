import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import schemas
from api.routers.users import update_user


def make_user(**overrides):
    data = {
        "id": 5,
        "telegram_id": 999,
        "role": None,
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
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeDB:
    def __init__(self, user):
        self.user = user
        self.commits = 0

    async def get(self, model, user_id):
        return self.user

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return None


class UsersRouterUpdateLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_user_sends_block_and_limitation_notifications(self):
        user = make_user()
        db = FakeDB(user)
        until = datetime(2026, 1, 1, 12, 0, 0)

        with patch("api.routers.users.convert_to_utc", return_value=until), patch(
            "api.routers.users.track_limitation_changes", return_value=(["A: 1"], True, False)
        ), patch("api.routers.users.sync_mandatory_channel_for_user_state_change", new=AsyncMock()) as mandatory_sync_mock, patch("core.cache.invalidate_user_cache", new=AsyncMock()), patch(
            "api.routers.users.send_block_notification", new=AsyncMock()
        ) as block_mock, patch("api.routers.users.send_limitation_notification", new=AsyncMock()) as limit_mock, patch(
            "api.routers.users.send_bot_access_notification", new=AsyncMock()
        ) as bot_mock, patch("api.routers.users.asyncio.create_task") as create_task_mock:
            await update_user(5, schemas.UserUpdate(trading_restricted_until=until), db=db)

        mandatory_sync_mock.assert_awaited_once_with(
            db,
            user=user,
            previous_role=None,
            previous_is_deleted=False,
            previous_deleted_at=None,
        )
        block_mock.assert_awaited_once_with(db, user, until)
        limit_mock.assert_awaited_once_with(db, user, ["A: 1"])
        bot_mock.assert_not_awaited()
        create_task_mock.assert_not_called()

    async def test_update_user_schedules_unblock_and_unlimit_tasks(self):
        old_restricted = datetime(2026, 1, 1, 12, 0, 0)
        user = make_user(trading_restricted_until=old_restricted)
        db = FakeDB(user)

        def fake_create_task(coro):
            if hasattr(coro, "close"):
                coro.close()
            return SimpleNamespace()

        with patch("api.routers.users.convert_to_utc", return_value=None), patch(
            "api.routers.users.track_limitation_changes", return_value=([], False, True)
        ), patch("api.routers.users.sync_mandatory_channel_for_user_state_change", new=AsyncMock()) as mandatory_sync_mock, patch("core.cache.invalidate_user_cache", new=AsyncMock()), patch(
            "api.routers.users.send_block_notification", new=AsyncMock()
        ), patch("api.routers.users.send_limitation_notification", new=AsyncMock()), patch(
            "api.routers.users.send_bot_access_notification", new=AsyncMock()
        ), patch("api.routers.users.asyncio.create_task", side_effect=fake_create_task) as create_task_mock:
            await update_user(5, schemas.UserUpdate(trading_restricted_until=None), db=db)

        mandatory_sync_mock.assert_awaited_once_with(
            db,
            user=user,
            previous_role=None,
            previous_is_deleted=False,
            previous_deleted_at=None,
        )
        self.assertEqual(create_task_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()