import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import LEGACY_RESPOND_PATH_DISABLED_MESSAGE, handle_start_with_token


def make_message():
    return SimpleNamespace(
        bot=SimpleNamespace(),
        chat=SimpleNamespace(id=32),
        delete=AsyncMock(),
        answer=AsyncMock(),
    )


class BotStartRespondTokenGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_respond_token_requires_user(self):
        message = make_message()

        await handle_start_with_token(message, SimpleNamespace(args="respond_5"), state=SimpleNamespace(), user=None)

        self.assertIn("ابتدا باید ثبت", message.answer.await_args.args[0])

    async def test_handle_start_with_any_respond_token_is_disabled_without_offer_lookup(self):
        message = make_message()

        with patch("bot.handlers.start.AsyncSessionLocal") as session_factory:
            await handle_start_with_token(
                message,
                SimpleNamespace(args="respond_bad"),
                state=SimpleNamespace(),
                user=SimpleNamespace(id=7),
            )

        session_factory.assert_not_called()
        message.answer.assert_awaited_once_with(LEGACY_RESPOND_PATH_DISABLED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
