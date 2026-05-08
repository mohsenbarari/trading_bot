import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import show_aliases_list


class FakeResponse:
    def __init__(self, payload=None, error=False):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise RuntimeError("bad")

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        return self.response


class BotAdminCommoditiesShowAliasesTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_aliases_list_renders_aliases_and_falls_back_to_commodity_list(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=10)))
        state = SimpleNamespace()
        commodity = {"name": "سکه", "aliases": []}

        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse(commodity))), patch(
            "bot.handlers.admin_commodities.get_aliases_list_keyboard", return_value="KB"
        ), patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()) as anchor_mock:
            await show_aliases_list(bot, 1, SimpleNamespace(id=1), state, commodity_id=7)
        self.assertIn("هیچ نام مستعاری ثبت نشده", bot.send_message.await_args.args[1])
        self.assertEqual(bot.send_message.await_args.kwargs["reply_markup"], "KB")
        anchor_mock.assert_awaited_once_with(state, 10, bot, 1)

        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse(error=True))), patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await show_aliases_list(bot, 1, SimpleNamespace(id=1), state, commodity_id=7)
        show_list_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()