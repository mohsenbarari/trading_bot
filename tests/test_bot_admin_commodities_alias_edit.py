import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from bot.handlers.admin_commodities import handle_alias_edit_name, handle_alias_edit_start
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

    async def put(self, url, json=None, headers=None):
        return self.response


class BotAdminCommoditiesAliasEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_alias_edit_start_extracts_name_and_sets_state(self):
        reply_markup = SimpleNamespace(
            inline_keyboard=[
                [
                    SimpleNamespace(text="ربع", callback_data="noop"),
                    SimpleNamespace(callback_data="alias_edit_7_9"),
                    SimpleNamespace(callback_data="alias_delete_7_9"),
                ]
            ]
        )
        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock(), reply_markup=reply_markup), data="alias_edit_7_9")
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

        with patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"):
            await handle_alias_edit_start(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)

        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_alias_edit_name)
        state.update_data.assert_awaited_once_with(alias_id=9, alias_name="ربع", commodity_id=7)
        self.assertEqual(query.message.edit_text.await_args.kwargs["reply_markup"], "KB")

        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock(), reply_markup=SimpleNamespace(inline_keyboard=None)), data="alias_edit_7_9")
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        with patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"):
            await handle_alias_edit_start(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        state.update_data.assert_awaited_once_with(alias_id=9, alias_name="---", commodity_id=7)

    async def test_handle_alias_edit_name_handles_success_and_error(self):
        status_msg = SimpleNamespace(message_id=15, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="بهار",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"alias_id": 9, "commodity_id": 7}))

        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()) as delete_mock, patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock, patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse())
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ) as show_aliases_mock:
            await handle_alias_edit_name(message, state, user=SimpleNamespace(id=1))

        delete_mock.assert_awaited_once_with(message)
        anchor_mock.assert_awaited_once_with(state, 15, message.bot, 1)
        clear_mock.assert_awaited_once_with(state)
        status_msg.edit_text.assert_awaited_once_with("✅ ویرایش شد.", parse_mode="Markdown")
        show_aliases_mock.assert_awaited_once_with(message.bot, 1, unittest.mock.ANY, state, 7)

        status_msg = SimpleNamespace(message_id=15, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="بهار",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"alias_id": 9, "commodity_id": 7}))
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse(error=RuntimeError("bad")))
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ):
            await handle_alias_edit_name(message, state, user=SimpleNamespace(id=1))
        self.assertIn("bad", status_msg.edit_text.await_args.args[0])

        status_msg = SimpleNamespace(message_id=16, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="بهار403",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"alias_id": 9, "commodity_id": 7}))
        http_error = httpx.HTTPStatusError(
            "bad",
            request=SimpleNamespace(),
            response=SimpleNamespace(text="plain", json=lambda: {"detail": "شما نمیتوانید در نام کالا از اعداد استفاده کنید"}),
        )
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(FakeResponse(error=http_error))
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()
        ):
            await handle_alias_edit_name(message, state, user=SimpleNamespace(id=1))
        self.assertIn("شما نمیتوانید در نام کالا از اعداد استفاده کنید", status_msg.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()