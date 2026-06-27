import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from bot.handlers.admin_commodities import handle_add_aliases_and_create, handle_add_name, handle_add_start
from bot.states import CommodityManagement
from core.enums import UserRole


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error
        return None

    def json(self):
        return self._payload


class FakeGetClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        return FakeResponse(self.payload)


class FakePostClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return FakeResponse()


class ErrorPostClient(FakePostClient):
    async def post(self, url, json=None, headers=None):
        response = SimpleNamespace(text="bad", json=lambda: {"detail": "oops"})
        raise httpx.HTTPStatusError("bad", request=SimpleNamespace(), response=response)


class BotAdminCommoditiesAddCreateTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_add_start_sets_state_and_prompt(self):
        query = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()))
        state = SimpleNamespace(set_state=AsyncMock())

        with patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"):
            await handle_add_start(query, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)

        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_add_name)
        self.assertEqual(query.message.edit_text.await_args.kwargs["reply_markup"], "KB")

    async def test_handle_add_name_handles_duplicate_and_success(self):
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        message = SimpleNamespace(
            text="سکه",
            answer=AsyncMock(return_value=SimpleNamespace(message_id=41)),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )

        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeGetClient([{"name": "سکه"}])
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        self.assertIn("قبلاً ثبت شده", message.answer.await_args.args[0])
        anchor_mock.assert_awaited_once()
        state.update_data.assert_not_awaited()

        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        prompt_msg = SimpleNamespace(message_id=42)
        message = SimpleNamespace(
            text="سکه",
            answer=AsyncMock(return_value=prompt_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeGetClient([{"name": "نیم"}])
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ) as anchor_mock:
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(name="سکه")
        state.set_state.assert_awaited_once_with(CommodityManagement.awaiting_add_aliases)
        anchor_mock.assert_awaited_once_with(state, 42, message.bot, 1)

        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        prompt_msg = SimpleNamespace(message_id=43)
        message = SimpleNamespace(
            text="سکه",
            answer=AsyncMock(return_value=prompt_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeGetClient({"a": {"name": "نیم"}})
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ):
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(name="سکه")

        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        prompt_msg = SimpleNamespace(message_id=44)
        message = SimpleNamespace(
            text="سکه",
            answer=AsyncMock(return_value=prompt_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeGetClient("unexpected")
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ):
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(name="سکه")

        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        prompt_msg = SimpleNamespace(message_id=45)
        message = SimpleNamespace(
            text="سکه",
            answer=AsyncMock(return_value=prompt_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", side_effect=RuntimeError("lookup failed")
        ), patch("bot.handlers.admin_commodities.get_commodity_fsm_cancel_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ):
            await handle_add_name(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(name="سکه")

    async def test_handle_add_aliases_and_create_handles_success_and_http_error(self):
        status_msg = SimpleNamespace(message_id=51, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="نیم، ربع-نیم",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"name": "سکه"}))
        client = FakePostClient()

        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=client
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await handle_add_aliases_and_create(message, state, user=SimpleNamespace(id=1))
        payload = client.calls[0][1]
        self.assertEqual(payload["commodity_data"], {"name": "سکه"})
        self.assertEqual(payload["aliases"], ["نیم", "ربع"])
        status_msg.edit_text.assert_awaited_once_with("✅ کالا **'سکه'** ثبت شد.", parse_mode="Markdown")
        clear_mock.assert_awaited_once_with(state)
        show_list_mock.assert_awaited_once_with(message.bot, 1, unittest.mock.ANY, state)

        status_msg = SimpleNamespace(message_id=52, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="ندارد",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"name": "سکه"}))
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", return_value=ErrorPostClient()
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock, patch("bot.handlers.admin_commodities.get_error_detail", return_value="oops"):
            await handle_add_aliases_and_create(message, state, user=SimpleNamespace(id=1))
        self.assertIn("oops", status_msg.edit_text.await_args.args[0])
        clear_mock.assert_not_awaited()
        show_list_mock.assert_not_awaited()

        status_msg = SimpleNamespace(message_id=53, edit_text=AsyncMock())
        message = SimpleNamespace(
            text="ندارد",
            answer=AsyncMock(return_value=status_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"name": "سکه"}))
        with patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ), patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient", side_effect=RuntimeError("broken transport")
        ), patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()), patch(
            "bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()
        ) as show_list_mock:
            await handle_add_aliases_and_create(message, state, user=SimpleNamespace(id=1))
        self.assertIn("broken transport", status_msg.edit_text.await_args.args[0])
        clear_mock.assert_not_awaited()
        show_list_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
