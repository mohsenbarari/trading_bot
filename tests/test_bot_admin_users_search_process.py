import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import process_search_query
from core.enums import UserRole


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


class BotAdminUsersSearchProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_search_query_clears_state_for_non_admin_and_handles_empty_query(self):
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(text="ali", delete=AsyncMock())
        await process_search_query(message, state=state, user=None)
        state.clear.assert_awaited_once()

        msg = SimpleNamespace(message_id=10)
        message = SimpleNamespace(
            text="   ",
            answer=AsyncMock(return_value=msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace()
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ) as clear_mock, patch("bot.handlers.admin_users.get_users_management_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_users.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await process_search_query(message, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        delete_mock.assert_awaited_once_with(message)
        clear_mock.assert_awaited_once_with(state)
        anchor_mock.assert_awaited_once_with(state, 10, message.bot, 1)
        self.assertIn("نمی\u200cتواند خالی باشد", message.answer.await_args.args[0])

    async def test_process_search_query_handles_not_found_and_found(self):
        searching = SimpleNamespace(message_id=11)
        missing = SimpleNamespace(message_id=12)
        message = SimpleNamespace(
            text="ali",
            answer=AsyncMock(side_effect=[searching, missing]),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=2),
        )
        state = SimpleNamespace()
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ), patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)), patch(
            "bot.handlers.admin_users.get_users_management_keyboard", return_value="KB"
        ), patch("bot.handlers.admin_users.update_anchor", new=AsyncMock()) as anchor_mock:
            await process_search_query(message, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(anchor_mock.await_count, 2)
        self.assertIn("یافت نشد", message.answer.await_args_list[-1].args[0])

        found = SimpleNamespace(
            id=9,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        searching = SimpleNamespace(message_id=13)
        profile_msg = SimpleNamespace(message_id=14)
        message = SimpleNamespace(
            text="0912",
            answer=AsyncMock(side_effect=[searching, profile_msg]),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=3),
        )
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ), patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(found)), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_profile_return_keyboard", return_value="KB") as keyboard_mock, patch(
            "bot.handlers.admin_users.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await process_search_query(message, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(user_id=9, back_to_page=1, is_restricted=True, has_limitations=True)
        self.assertEqual(anchor_mock.await_count, 2)
        self.assertEqual(message.answer.await_args_list[-1].args[0], "PROFILE")


if __name__ == "__main__":
    unittest.main()