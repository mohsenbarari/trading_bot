import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin import _return_to_admin_panel


class FakeState:
    def __init__(self, data=None):
        self.data = data or {}
        self.updated = []

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)


class BotAdminReturnPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_return_to_admin_panel_handles_message_and_callback_inputs(self):
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat = SimpleNamespace(id=chat_id)

        class FakeCallbackQuery:
            def __init__(self, chat_id):
                self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

        bot = SimpleNamespace(
            delete_message=AsyncMock(),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        )
        state = FakeState({"anchor_message_id": 55})

        with patch("bot.handlers.admin.get_admin_panel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin.types.Message", FakeMessage
        ), patch("bot.handlers.admin.types.CallbackQuery", FakeCallbackQuery):
            await _return_to_admin_panel(FakeMessage(10), state, bot)

        bot.delete_message.assert_awaited_once_with(10, 55)
        bot.send_message.assert_awaited_once_with(chat_id=10, text="...بازگشت به پنل مدیریت", reply_markup="KB")
        self.assertEqual(state.updated, [{"anchor_message_id": 77}])

        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock(return_value=SimpleNamespace(message_id=88)))
        state = FakeState()
        with patch("bot.handlers.admin.get_admin_panel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin.types.Message", FakeMessage
        ), patch("bot.handlers.admin.types.CallbackQuery", FakeCallbackQuery):
            await _return_to_admin_panel(FakeCallbackQuery(11), state, bot)

        bot.send_message.assert_awaited_once_with(chat_id=11, text="...بازگشت به پنل مدیریت", reply_markup="KB")


if __name__ == "__main__":
    unittest.main()