import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import handle_cancel_fsm, handle_delete_confirm, handle_delete_yes
from bot.states import CommodityManagement
from core.enums import UserRole


class FakeResponse:
    def __init__(self, error=None):
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error


class FakeClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def delete(self, url, headers=None):
        return self.response


class BotAdminCommoditiesDeleteCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_delete_confirm_delete_yes_and_cancel_fsm(self):
        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), data="comm_delete_7")
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        with patch("bot.handlers.admin_commodities.get_commodity_delete_confirm_keyboard", return_value="KB"):
            await handle_delete_confirm(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_delete_confirmation)
        state.update_data.assert_awaited_once_with(commodity_to_delete_id=7)
        self.assertEqual(query.message.edit_text.await_args.kwargs["reply_markup"], "KB")

        status_msg = SimpleNamespace(message_id=60, edit_text=AsyncMock())
        query = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock(return_value=status_msg), chat=SimpleNamespace(id=1)),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"commodity_to_delete_id": 7}))
        with patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()) as anchor_mock, patch(
            "bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()
        ) as clear_mock, patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse())), patch(
            "bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()) as show_list_mock:
            await handle_delete_yes(query, user=SimpleNamespace(id=1), state=state)
        anchor_mock.assert_awaited_once_with(state, 60, query.bot, 1)
        clear_mock.assert_awaited_once_with(state)
        status_msg.edit_text.assert_awaited_once_with("✅ کالا حذف شد.")
        show_list_mock.assert_awaited_once_with(query.bot, 1, unittest.mock.ANY, state)

        query = SimpleNamespace(bot=SimpleNamespace(), message=SimpleNamespace(chat=SimpleNamespace(id=1)))
        state = SimpleNamespace(get_data=AsyncMock(return_value={"commodity_id": 7}))
        with patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ) as show_aliases_mock:
            await handle_cancel_fsm(query, state=state, user=SimpleNamespace(id=1))
        clear_mock.assert_awaited_once_with(state)
        show_aliases_mock.assert_awaited_once_with(query.bot, 1, unittest.mock.ANY, state, 7)

        query = SimpleNamespace(bot=SimpleNamespace(), message=SimpleNamespace(chat=SimpleNamespace(id=1)))
        state = SimpleNamespace(get_data=AsyncMock(return_value={}))
        with patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await handle_cancel_fsm(query, state=state, user=SimpleNamespace(id=1))
        show_list_mock.assert_awaited_once_with(query.bot, 1, unittest.mock.ANY, state)


if __name__ == "__main__":
    unittest.main()