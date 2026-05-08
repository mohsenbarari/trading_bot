import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import handle_alias_delete_start, handle_alias_delete_yes
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


class BotAdminCommoditiesAliasDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_alias_delete_start_extracts_name_and_sets_state(self):
        reply_markup = SimpleNamespace(
            inline_keyboard=[
                [
                    SimpleNamespace(text="ربع", callback_data="noop"),
                    SimpleNamespace(callback_data="alias_edit_7_9"),
                    SimpleNamespace(callback_data="alias_delete_7_9"),
                ]
            ]
        )
        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock(), reply_markup=reply_markup), data="alias_delete_7_9")
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

        with patch("bot.handlers.admin_commodities.get_alias_delete_confirm_keyboard", return_value="KB"):
            await handle_alias_delete_start(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)

        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_alias_delete_confirm)
        state.update_data.assert_awaited_once_with(alias_to_delete_id=9, commodity_id=7)
        self.assertEqual(query.message.edit_text.await_args.kwargs["reply_markup"], "KB")
        self.assertIn("ربع", query.message.edit_text.await_args.args[0])

    async def test_handle_alias_delete_yes_handles_success_and_error(self):
        status_msg = SimpleNamespace(message_id=22, edit_text=AsyncMock())
        query = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock(return_value=status_msg), chat=SimpleNamespace(id=1)),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"alias_to_delete_id": 9, "commodity_id": 7}))

        with patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()) as anchor_mock, patch(
            "bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()
        ) as clear_mock, patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse())), patch(
            "bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()) as show_aliases_mock:
            await handle_alias_delete_yes(query, user=SimpleNamespace(id=1), state=state)

        anchor_mock.assert_awaited_once_with(state, 22, query.bot, 1)
        clear_mock.assert_awaited_once_with(state)
        status_msg.edit_text.assert_awaited_once_with("✅ حذف شد.")
        show_aliases_mock.assert_awaited_once_with(query.bot, 1, unittest.mock.ANY, state, 7)

        status_msg = SimpleNamespace(message_id=22, edit_text=AsyncMock())
        query = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock(return_value=status_msg), chat=SimpleNamespace(id=1)),
            bot=SimpleNamespace(),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"alias_to_delete_id": 9, "commodity_id": 7}))
        with patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse(error=RuntimeError("bad")))), patch(
            "bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()):
            await handle_alias_delete_yes(query, user=SimpleNamespace(id=1), state=state)
        self.assertIn("bad", status_msg.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()