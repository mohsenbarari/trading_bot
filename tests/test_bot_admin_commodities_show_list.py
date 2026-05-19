import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_commodities import show_commodity_list
from core.enums import UserRole


class FakeResponse:
    def __init__(self, payload, raise_error: Exception | None = None):
        self._payload = payload
        self._raise_error = raise_error

    def raise_for_status(self):
        if self._raise_error:
            raise self._raise_error
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload, raise_error: Exception | None = None):
        self.payload = payload
        self.raise_error = raise_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, timeout=None, headers=None):
        return FakeResponse(self.payload, self.raise_error)


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

        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=101)))
        payload = {"x": "bad-row", "y": {"id": 3, "name": "ربع"}}
        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient(payload)), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ):
            await show_commodity_list(bot, 1, SimpleNamespace(role=UserRole.SUPER_ADMIN), state)
        keyboard = bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "📦 ربع")

        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=102)))
        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient("unexpected")), patch(
            "bot.handlers.admin_commodities.update_anchor", new=AsyncMock()
        ):
            await show_commodity_list(bot, 1, SimpleNamespace(role=UserRole.SUPER_ADMIN), state)
        self.assertIn("هیچ کالایی ثبت نشده", bot.send_message.await_args.args[1])

        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=103)))

        def fake_create_task(coro):
            coro.close()
            return SimpleNamespace()

        with patch("bot.handlers.admin_commodities.httpx.AsyncClient", return_value=FakeClient([], raise_error=RuntimeError("boom"))), patch(
            "bot.handlers.admin_commodities.asyncio.create_task", side_effect=fake_create_task
        ) as create_task_mock, patch("bot.handlers.admin_commodities.logger.exception") as logger_mock:
            await show_commodity_list(bot, 1, SimpleNamespace(role=UserRole.SUPER_ADMIN), state)
        self.assertIn("خطای سیستمی", bot.send_message.await_args_list[-1].args[1])
        logger_mock.assert_called_once()
        create_task_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()