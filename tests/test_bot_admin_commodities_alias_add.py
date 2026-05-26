import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from bot.handlers.admin_commodities import handle_alias_add_name, handle_alias_add_start
from bot.states import CommodityManagement
from core.enums import UserRole


class FakeResponse:
    def __init__(self, error=None):
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error
        return None


class FakeClient:
    def __init__(self, error=None):
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        return FakeResponse(self.error)


class BotAdminCommoditiesAliasAddTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_alias_add_start_and_name_success(self):
        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), data="alias_add_7")
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

        with patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"):
            await handle_alias_add_start(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_alias_add_name)
        state.update_data.assert_awaited_once_with(commodity_id=7)
        self.assertEqual(query.message.edit_text.await_args.kwargs["reply_markup"], "KB")

        status_msg = SimpleNamespace(message_id=12, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="بهار",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"commodity_id": 7}))
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock, patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient()
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ) as show_aliases_mock:
            await handle_alias_add_name(message, state, user=SimpleNamespace(id=1))
        delete_mock.assert_awaited_once_with(message)
        anchor_mock.assert_awaited_once_with(state, 12, message.bot, 1)
        clear_mock.assert_awaited_once_with(state)
        status_msg.edit_text.assert_awaited_once()
        show_aliases_mock.assert_awaited_once_with(message.bot, 1, unittest.mock.ANY, state, 7)

        status_msg = SimpleNamespace(message_id=13, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="بهار",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"commodity_id": 7}))
        http_error = httpx.HTTPStatusError(
            "bad",
            request=SimpleNamespace(),
            response=SimpleNamespace(text="plain", json=lambda: {"detail": "exists"}),
        )
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(http_error)
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ) as show_aliases_mock:
            await handle_alias_add_name(message, state, user=SimpleNamespace(id=1))
        self.assertIn("exists", status_msg.edit_text.await_args.args[0])
        clear_mock.assert_not_awaited()
        show_aliases_mock.assert_not_awaited()

        status_msg = SimpleNamespace(message_id=14, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="بهار",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"commodity_id": 7}))
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", side_effect=RuntimeError("boom")
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ) as show_aliases_mock:
            await handle_alias_add_name(message, state, user=SimpleNamespace(id=1))
        self.assertIn("boom", status_msg.edit_text.await_args.args[0])
        clear_mock.assert_not_awaited()
        show_aliases_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()