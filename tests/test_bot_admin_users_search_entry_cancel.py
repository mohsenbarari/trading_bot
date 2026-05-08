import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_search_cancel, start_search_user
from bot.states import UserManagement
from core.enums import UserRole


class BotAdminUsersSearchEntryCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_search_user_sets_state_and_updates_anchor(self):
        message = SimpleNamespace(
            answer=AsyncMock(return_value=SimpleNamespace(message_id=81)),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=6),
        )
        state = SimpleNamespace(set_state=AsyncMock())
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_users.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await start_search_user(message, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        delete_mock.assert_awaited_once_with(message)
        state.set_state.assert_awaited_once_with(UserManagement.awaiting_search_query)
        anchor_mock.assert_awaited_once_with(state, 81, message.bot, 6)
        self.assertIn("نام کاربری", message.answer.await_args.args[0])

    async def test_handle_user_search_cancel_returns_to_menu(self):
        msg = SimpleNamespace(message_id=82)
        query = SimpleNamespace(
            message=SimpleNamespace(answer=AsyncMock(return_value=msg), chat=SimpleNamespace(id=9)),
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        state = SimpleNamespace()
        with patch("bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_users.get_users_management_keyboard", return_value="KB"
        ), patch("bot.handlers.admin_users.update_anchor", new=AsyncMock()) as anchor_mock:
            await handle_user_search_cancel(query, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        clear_mock.assert_awaited_once_with(state)
        anchor_mock.assert_awaited_once_with(state, 82, query.bot, 9)
        query.answer.assert_awaited_once_with("عملیات لغو شد")


if __name__ == "__main__":
    unittest.main()