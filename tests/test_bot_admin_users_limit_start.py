import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import (
    get_limit_panel_text,
    handle_set_commodities,
    handle_set_requests,
    handle_set_trades,
    handle_user_limit_start,
)
from bot.states import UserLimitations
from core.enums import UserRole


class BotAdminUsersLimitStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_limit_panel_text_and_limit_start_paths(self):
        text = get_limit_panel_text(1, None, 3)
        self.assertIn("1", text)
        self.assertIn("---", text)
        self.assertIn("3", text)

        callback = SimpleNamespace(
            data="user_limit_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        target_user = SimpleNamespace(id=9, role=UserRole.STANDARD)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.get_limit_duration_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_user_limit_start(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=SimpleNamespace())
        keyboard_mock.assert_called_once_with(9)
        self.assertIn("مدت زمان محدودیت", callback.message.edit_text.await_args.args[0])
        callback.answer.assert_awaited_once()

        state = SimpleNamespace(update_data=AsyncMock())
        callback = SimpleNamespace(
            data="user_limit_dur_9_15",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.datetime"
        ) as datetime_mock, patch(
            "bot.keyboards.get_limit_settings_keyboard", return_value="KB"
        ) as keyboard_mock:
            datetime_mock.utcnow.return_value = datetime(2026, 1, 1, 12, 0, 0)
            await handle_user_limit_start(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        state.update_data.assert_awaited_once()
        self.assertEqual(state.update_data.await_args.kwargs["limit_target_user_id"], 9)
        keyboard_mock.assert_called_once_with(9)
        callback.message.edit_text.assert_awaited_once_with(get_limit_panel_text(None, None, None), reply_markup="KB", parse_mode="Markdown")

    async def test_limit_set_prompts_mark_editing_state(self):
        for handler, callback_data, expected_editing in [
            (handle_set_trades, "limit_set_trades_9", "trades"),
            (handle_set_commodities, "limit_set_commodities_9", "commodities"),
            (handle_set_requests, "limit_set_requests_9", "requests"),
        ]:
            state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
            callback = SimpleNamespace(data=callback_data, message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.STANDARD))):
                await handler(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
            state.update_data.assert_awaited_once_with(limit_editing=expected_editing)
            state.set_state.assert_awaited_once_with(UserLimitations.awaiting_limit_value)
            callback.answer.assert_awaited_once()


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


if __name__ == "__main__":
    unittest.main()