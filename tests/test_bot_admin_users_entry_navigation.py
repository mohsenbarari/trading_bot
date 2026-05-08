import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import (
    handle_back_to_admin,
    handle_users_list_command,
    handle_users_menu,
    handle_users_pagination,
    handle_view_user_profile,
)
from core.enums import UserRole


def consume_task(coro):
    coro.close()
    return None


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


class BotAdminUsersEntryNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_list_and_pagination_handlers_delegate_correctly(self):
        message = SimpleNamespace(
            answer=AsyncMock(return_value=SimpleNamespace(message_id=41)),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(update_data=AsyncMock())
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_users.get_users_management_keyboard", return_value="KB"
        ):
            await handle_users_menu(message, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        delete_mock.assert_awaited_once_with(message)
        state.update_data.assert_awaited_once_with(users_menu_id=41)

        state = SimpleNamespace()
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ) as clear_mock, patch("bot.handlers.admin_users.show_users_list", new=AsyncMock()) as show_mock:
            await handle_users_list_command(message, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        delete_mock.assert_awaited_once_with(message)
        clear_mock.assert_awaited_once_with(state)
        show_mock.assert_awaited_once_with(message.bot, 1, state, page=1)

        callback = SimpleNamespace(
            data="users_page_3",
            bot=SimpleNamespace(),
            message=SimpleNamespace(chat=SimpleNamespace(id=2), message_id=88),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.show_users_list", new=AsyncMock()) as show_mock:
            await handle_users_pagination(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        show_mock.assert_awaited_once_with(callback.bot, 2, state, 3, message_id_to_edit=88)
        callback.answer.assert_awaited_once()

    async def test_handle_view_user_profile_handles_missing_user_and_success(self):
        callback = SimpleNamespace(data="user_profile_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_view_user_profile(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=SimpleNamespace())
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        target_user = SimpleNamespace(
            id=9,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        reply_markup = SimpleNamespace(
            inline_keyboard=[[SimpleNamespace(callback_data="users_page_4")]]
        )
        message = SimpleNamespace(reply_markup=reply_markup, edit_text=AsyncMock(), chat=SimpleNamespace(id=5))
        callback = SimpleNamespace(
            data="user_profile_9",
            message=message,
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_profile_return_keyboard", return_value="KB") as keyboard_mock:
            await handle_view_user_profile(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=SimpleNamespace())
        keyboard_mock.assert_called_once_with(user_id=9, back_to_page=4, is_restricted=True, has_limitations=True)
        message.edit_text.assert_awaited_once_with("PROFILE", reply_markup="KB", parse_mode="Markdown")
        callback.answer.assert_awaited_once()

    async def test_handle_back_to_admin_clears_state_and_schedules_anchor_cleanup(self):
        message = SimpleNamespace(
            answer=AsyncMock(return_value=SimpleNamespace(message_id=70)),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=7),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"anchor_id": 11, "users_menu_id": 12}), clear=AsyncMock())
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_users.get_admin_panel_keyboard", return_value="KB"
        ), patch("bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task) as create_task_mock:
            await handle_back_to_admin(message, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        delete_mock.assert_awaited_once_with(message)
        state.clear.assert_awaited_once()
        self.assertEqual(create_task_mock.call_count, 2)
        message.answer.assert_awaited_once_with("به پنل مدیریت بازگشتید.", reply_markup="KB")


if __name__ == "__main__":
    unittest.main()