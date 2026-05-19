import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from bot.handlers.admin_users import (
    _apply_user_management_scope,
    _can_edit_target_role,
    _can_manage_target_user,
    _can_open_user_management,
    _is_admin_role_value,
    clear_state_retain_anchors,
    delete_user_message,
    safe_delete_message,
    send_delayed_removal_notification,
    update_anchor,
)
from core.enums import UserRole
from models.user import User


def consume_task(coro):
    coro.close()
    return None


class BotAdminUsersHelpersTests(unittest.IsolatedAsyncioTestCase):
    def test_user_management_guard_helpers_cover_admin_role_paths(self):
        super_admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
        middle_manager = SimpleNamespace(role=UserRole.MIDDLE_MANAGER)
        standard_user = SimpleNamespace(role=UserRole.STANDARD)

        self.assertFalse(_can_open_user_management(None))
        self.assertTrue(_can_open_user_management(middle_manager))

        self.assertTrue(_can_edit_target_role(super_admin))
        self.assertFalse(_can_edit_target_role(middle_manager))

        self.assertTrue(_is_admin_role_value(UserRole.SUPER_ADMIN))
        self.assertTrue(_is_admin_role_value(UserRole.MIDDLE_MANAGER.value))
        self.assertFalse(_is_admin_role_value(UserRole.STANDARD))

        self.assertFalse(_can_manage_target_user(None, standard_user))
        self.assertFalse(_can_manage_target_user(middle_manager, super_admin))
        self.assertTrue(_can_manage_target_user(super_admin, middle_manager))

        base_stmt = select(User)
        self.assertIs(_apply_user_management_scope(base_stmt, None), base_stmt)
        self.assertIn('NOT IN', str(_apply_user_management_scope(base_stmt, middle_manager)))

    async def test_safe_delete_message_honors_delay_and_swallows_errors(self):
        bot = SimpleNamespace(delete_message=AsyncMock())
        with patch("bot.handlers.admin_users.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await safe_delete_message(bot, 10, 20, delay=5)
        sleep_mock.assert_awaited_once_with(5)
        bot.delete_message.assert_awaited_once_with(10, 20)

        bot = SimpleNamespace(delete_message=AsyncMock(side_effect=RuntimeError("boom")))
        await safe_delete_message(bot, 10, 20)

    async def test_update_anchor_replaces_old_anchor_and_preserves_same_anchor(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 11}), update_data=AsyncMock())
        with patch("bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task) as create_task_mock, patch(
            "bot.handlers.admin_users.safe_delete_message", new=AsyncMock()
        ):
            await update_anchor(state, 22, SimpleNamespace(), 33)
        state.update_data.assert_awaited_once_with(anchor_id=22)
        create_task_mock.assert_called_once()

        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 22}), update_data=AsyncMock())
        with patch("bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task) as create_task_mock, patch(
            "bot.handlers.admin_users.safe_delete_message", new=AsyncMock()
        ):
            await update_anchor(state, 22, SimpleNamespace(), 33)
        create_task_mock.assert_not_called()

    async def test_clear_state_retain_anchors_and_delete_user_message(self):
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"anchor_id": 7, "users_menu_id": 8, "other": 9}),
            clear=AsyncMock(),
            update_data=AsyncMock(),
        )
        await clear_state_retain_anchors(state)
        state.clear.assert_awaited_once()
        state.update_data.assert_awaited_once_with(anchor_id=7, users_menu_id=8)

        message = SimpleNamespace(delete=AsyncMock())
        await delete_user_message(message)
        message.delete.assert_awaited_once()

        message = SimpleNamespace(delete=AsyncMock(side_effect=RuntimeError("boom")))
        await delete_user_message(message)

    async def test_send_delayed_removal_notification_skips_missing_or_restored_limits(self):
        class FakeSession:
            def __init__(self, user):
                self.user = user

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, model, user_id):
                self.last_get = (model, user_id)
                return self.user

        sleep_mock = AsyncMock()
        create_mock = AsyncMock()
        telegram_mock = AsyncMock()

        blocked_user = SimpleNamespace(
            trading_restricted_until=datetime.utcnow() + timedelta(minutes=5),
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        limited_user = SimpleNamespace(
            trading_restricted_until=None,
            max_daily_trades=2,
            max_active_commodities=None,
            max_daily_requests=None,
        )

        with patch('bot.handlers.admin_users.asyncio.sleep', new=sleep_mock), patch(
            'bot.handlers.admin_users.AsyncSessionLocal', return_value=FakeSession(None)
        ), patch('bot.handlers.admin_users.create_user_notification', new=create_mock), patch(
            'bot.handlers.admin_users.send_telegram_notification', new=telegram_mock
        ):
            await send_delayed_removal_notification(11, 22, True, delay_seconds=1)

        create_mock.assert_not_awaited()
        telegram_mock.assert_not_awaited()

        with patch('bot.handlers.admin_users.asyncio.sleep', new=sleep_mock), patch(
            'bot.handlers.admin_users.AsyncSessionLocal', return_value=FakeSession(blocked_user)
        ), patch('bot.handlers.admin_users.create_user_notification', new=create_mock), patch(
            'bot.handlers.admin_users.send_telegram_notification', new=telegram_mock
        ):
            await send_delayed_removal_notification(12, 23, True, delay_seconds=1)

        create_mock.assert_not_awaited()
        telegram_mock.assert_not_awaited()

        with patch('bot.handlers.admin_users.asyncio.sleep', new=sleep_mock), patch(
            'bot.handlers.admin_users.AsyncSessionLocal', return_value=FakeSession(limited_user)
        ), patch('bot.handlers.admin_users.create_user_notification', new=create_mock), patch(
            'bot.handlers.admin_users.send_telegram_notification', new=telegram_mock
        ):
            await send_delayed_removal_notification(13, 24, False, delay_seconds=1)

        create_mock.assert_not_awaited()
        telegram_mock.assert_not_awaited()

    async def test_send_delayed_removal_notification_sends_block_and_limit_removal_messages(self):
        class FakeSession:
            def __init__(self, user):
                self.user = user

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, model, user_id):
                self.last_get = (model, user_id)
                return self.user

        sleep_mock = AsyncMock()
        create_mock = AsyncMock()
        telegram_mock = AsyncMock()
        unrestricted_user = SimpleNamespace(
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )

        with patch('bot.handlers.admin_users.asyncio.sleep', new=sleep_mock), patch(
            'bot.handlers.admin_users.AsyncSessionLocal', return_value=FakeSession(unrestricted_user)
        ), patch('bot.handlers.admin_users.create_user_notification', new=create_mock), patch(
            'bot.handlers.admin_users.send_telegram_notification', new=telegram_mock
        ):
            await send_delayed_removal_notification(21, 31, True, delay_seconds=1)

        self.assertIn('رفع مسدودیت', create_mock.await_args_list[0].args[2])
        telegram_mock.assert_awaited_with(31, create_mock.await_args_list[0].args[2])

        create_mock.reset_mock()
        telegram_mock.reset_mock()

        with patch('bot.handlers.admin_users.asyncio.sleep', new=sleep_mock), patch(
            'bot.handlers.admin_users.AsyncSessionLocal', return_value=FakeSession(unrestricted_user)
        ), patch('bot.handlers.admin_users.create_user_notification', new=create_mock), patch(
            'bot.handlers.admin_users.send_telegram_notification', new=telegram_mock
        ):
            await send_delayed_removal_notification(22, 32, False, delay_seconds=1)

        self.assertIn('رفع محدودیت', create_mock.await_args_list[0].args[2])
        telegram_mock.assert_awaited_with(32, create_mock.await_args_list[0].args[2])


if __name__ == "__main__":
    unittest.main()