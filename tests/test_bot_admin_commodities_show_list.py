import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import show_commodity_list
from core.enums import UserRole


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, timeout=None, headers=None):
        return FakeResponse(self.payload)


class BotAdminCommoditiesShowListTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_commodity_list_handles_non_admin_empty_and_filled_payloads(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)))
        state = SimpleNamespace()

        await show_commodity_list(bot, 1, SimpleNamespace(role=UserRole.STANDARD), state)
        bot.send_message.assert_not_awaited()

        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient({})), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await show_commodity_list(bot, 1, SimpleNamespace(role=UserRole.SUPER_ADMIN), state)
        self.assertIn("هیچ کالایی ثبت نشده", bot.send_message.await_args_list[-1].args[1])
        anchor_mock.assert_awaited_once_with(state, 99, bot, 1)

        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)))
        payload = [{"id": 1, "name": "سکه"}, {"id": 2, "name": "نیم"}]
        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(payload)), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ):
            await show_commodity_list(bot, 1, SimpleNamespace(role=UserRole.SUPER_ADMIN), state)
        keyboard = bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "📦 سکه")
        self.assertEqual(keyboard.inline_keyboard[-1][0].callback_data, "comm_add_new")


if __name__ == "__main__":
    unittest.main()