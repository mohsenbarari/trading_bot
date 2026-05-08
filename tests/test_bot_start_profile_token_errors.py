import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_start_with_token


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, value):
        self.value = value

    async def execute(self, stmt):
        return FakeExecuteResult(self.value)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotStartProfileTokenErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_profile_token_handles_missing_and_invalid_users(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=31),
            delete=AsyncMock(),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))):
            await handle_start_with_token(message, SimpleNamespace(args="profile_11"), state=SimpleNamespace(), user=None)
        self.assertIn("کاربر یافت نشد", message.answer.await_args.args[0])

        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=31),
            delete=AsyncMock(),
            answer=AsyncMock(),
        )
        await handle_start_with_token(message, SimpleNamespace(args="profile_bad"), state=SimpleNamespace(), user=None)
        self.assertIn("لینک نامعتبر", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()