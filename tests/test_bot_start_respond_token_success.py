import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import LEGACY_RESPOND_PATH_DISABLED_MESSAGE, handle_start_with_token


class BotStartRespondTokenSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_respond_token_is_fail_closed_for_registered_user(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=33),
            delete=AsyncMock(),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.start.AsyncSessionLocal") as session_factory:
            await handle_start_with_token(
                message,
                SimpleNamespace(args="respond_5"),
                state=SimpleNamespace(),
                user=SimpleNamespace(id=2),
            )

        session_factory.assert_not_called()
        message.delete.assert_awaited_once()
        message.answer.assert_awaited_once_with(LEGACY_RESPOND_PATH_DISABLED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
