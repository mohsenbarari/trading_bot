import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import handle_add_name, handle_add_start
from bot.states import CommodityManagement
from core.enums import UserRole


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def raise_for_status(self):
        return None

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


class BotAdminCommoditiesAddFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_add_start_and_add_name_duplicate_and_success(self):
        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()))
        state = SimpleNamespace(set_state=AsyncMock())
        with patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"):
            await handle_add_start(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_add_name)
        self.assertEqual(query.message.edit_text.await_args.kwargs["reply_markup"], "KB")

        error_msg = SimpleNamespace(message_id=44)
        message = SimpleNamespace(text="سکه", answer=AsyncMock(return_value=error_msg), bot=SimpleNamespace(), chat=SimpleNamespace(id=1))
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse([{"name": "سکه"}]))
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        anchor_mock.assert_awaited_once_with(state, 44, message.bot, 1)
        self.assertIn("قبلاً ثبت شده", message.answer.await_args.args[0])

        message = SimpleNamespace(text="بهار", answer=AsyncMock(return_value=SimpleNamespace(message_id=45)), bot=SimpleNamespace(), chat=SimpleNamespace(id=1))
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse([]))
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(name="بهار")
        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_add_aliases)
        anchor_mock.assert_awaited_once_with(state, 45, message.bot, 1)
        self.assertIn("نام\u200cهای مستعار", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()