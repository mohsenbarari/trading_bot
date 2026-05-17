import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_admin_max_block_custom, process_custom_max_block
from bot.states import UserManagement
from core.enums import UserRole


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


class BotAdminUsersBlockCustomTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_admin_max_block_custom_and_process_custom_value(self):
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_max_block_custom_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.STANDARD))):
            await handle_admin_max_block_custom(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        state.update_data.assert_awaited_once_with(custom_max_block_user_id=9)
        state.set_state.assert_awaited_once_with(UserManagement.awaiting_custom_max_block)
        callback.answer.assert_awaited_once()

        invalid_msg = SimpleNamespace(message_id=51)
        message = SimpleNamespace(
            text="101",
            answer=AsyncMock(return_value=invalid_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"custom_max_block_user_id": 9, "anchor_id": 1}))
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await process_custom_max_block(message, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        self.assertIn("بین 1 تا 100", message.answer.await_args.args[0])
        anchor_mock.assert_awaited_once_with(state, 51, message.bot, 1)

        target_user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=True, max_blocked_users=5)
        session = FakeSession(target_user)
        success_msg = SimpleNamespace(message_id=52)
        message = SimpleNamespace(
            text="12",
            answer=AsyncMock(return_value=success_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"custom_max_block_user_id": 9, "anchor_id": 1}))
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ) as clear_mock, patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ) as keyboard_mock, patch("bot.handlers.admin_users.update_anchor", new=AsyncMock()) as anchor_mock:
            await process_custom_max_block(message, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        self.assertEqual(target_user.max_blocked_users, 12)
        session.commit.assert_awaited_once()
        clear_mock.assert_awaited_once_with(state)
        keyboard_mock.assert_called_once_with(9, True, 12)
        anchor_mock.assert_awaited_once_with(state, 52, message.bot, 1)

        missing_msg = SimpleNamespace(message_id=53)
        message = SimpleNamespace(
            text="12",
            answer=AsyncMock(return_value=missing_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"custom_max_block_user_id": 9, "anchor_id": 1}))
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ), patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)), patch(
            "bot.handlers.admin_users.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await process_custom_max_block(message, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        self.assertEqual(message.answer.await_args.args[0], "❌ کاربر یافت نشد.")
        anchor_mock.assert_awaited_once_with(state, 53, message.bot, 1)


if __name__ == "__main__":
    unittest.main()