import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_start_without_token


class BotStartWithoutTokenTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_handle_start_without_token_leaves_unauthorized_users_to_default_handler(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()) as delete_anchor:
            await handle_start_without_token(message, state=SimpleNamespace(), user=None)

        delete_anchor.assert_awaited_once()
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()