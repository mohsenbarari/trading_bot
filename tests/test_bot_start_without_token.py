import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_start_without_token
from bot.handlers.start import handle_start_with_token


class BotStartWithoutTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_valid_link_token_prompts_for_contact(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.load_pending_telegram_link_token_user_for_update",
            new=AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())),
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args="link_raw-token"), state=state, user=None)

        self.assertIn("شماره موبایل همین حساب", message.answer.await_args.args[0])
        state.update_data.assert_awaited_once()
        state.set_state.assert_awaited_once()
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_start_without_token_shows_panel_for_registered_user(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        user = SimpleNamespace(full_name="Ali", role="standard")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.start.get_persistent_menu_keyboard", return_value="menu"
        ) as menu_mock, patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_without_token(message, state=SimpleNamespace(), user=user)

        delete_anchor.assert_awaited_once()
        menu_mock.assert_called_once()
        self.assertIn("سلام Ali", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_start_without_token_returns_neutral_fallback_for_unknown_user(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        )

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_start_without_token(message, state=SimpleNamespace(), user=None)

        delete_anchor.assert_awaited_once()
        self.assertIn("ثبت‌نام را در سامانه کامل کنید", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 77)


if __name__ == "__main__":
    unittest.main()
