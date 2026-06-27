import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from bot.handlers.admin_commodities import handle_add_aliases_and_create


class FakeResponse:
    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return FakeResponse()


class FailingClient:
    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        raise self.exc


class BotAdminCommoditiesAddAliasesCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_add_aliases_and_create_builds_payload_and_handles_http_error(self):
        status_msg = SimpleNamespace(message_id=50, edit_text=AsyncMock())
        message = SimpleNamespace(text="بهار، طرح جدید-بهار", answer=AsyncMock(return_value=status_msg), bot=SimpleNamespace(), chat=SimpleNamespace(id=1))
        state = SimpleNamespace(get_data=AsyncMock(return_value={"name": "سکه"}))
        client = FakeClient()
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock, patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=client
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await handle_add_aliases_and_create(message, state, user=SimpleNamespace(id=1))

        anchor_mock.assert_awaited_once_with(state, 50, message.bot, 1)
        clear_mock.assert_awaited_once_with(state)
        payload = client.calls[0][1]
        self.assertEqual(payload["commodity_data"], {"name": "سکه"})
        self.assertEqual(payload["aliases"], ["بهار", "طرح جدید"])
        status_msg.edit_text.assert_awaited_once_with("✅ کالا **'سکه'** ثبت شد.", parse_mode="Markdown")
        show_list_mock.assert_awaited_once_with(message.bot, 1, unittest.mock.ANY, state)

        request = httpx.Request("POST", "http://app:8000/api/commodities/")
        response = httpx.Response(400, request=request)
        exc = httpx.HTTPStatusError("bad", request=request, response=response)
        status_msg = SimpleNamespace(message_id=50, edit_text=AsyncMock())
        message = SimpleNamespace(text="ندارد", answer=AsyncMock(return_value=status_msg), bot=SimpleNamespace(), chat=SimpleNamespace(id=1))
        state = SimpleNamespace(get_data=AsyncMock(return_value={"name": "سکه"}))
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FailingClient(exc)
        ), patch("bot.handlers.admin_commodities.get_error_detail", return_value="detail"), patch(
            "bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()):
            await handle_add_aliases_and_create(message, state, user=SimpleNamespace(id=1))
        self.assertIn("detail", status_msg.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
