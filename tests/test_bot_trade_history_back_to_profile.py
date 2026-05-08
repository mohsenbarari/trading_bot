import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_history import back_to_profile


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


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))


class BotTradeHistoryBackToProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_back_to_profile_returns_early_without_user(self):
        callback = make_callback()

        await back_to_profile(callback, SimpleNamespace(target_user_id=5), state=SimpleNamespace(), user=None)

        callback.answer.assert_not_awaited()

    async def test_back_to_profile_rebuilds_profile_text_and_answers(self):
        target_user = SimpleNamespace(account_name="target", mobile_number="0912", address="تهران")
        callback = make_callback()

        with patch("bot.handlers.trade_history.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(target_user))):
            await back_to_profile(callback, SimpleNamespace(target_user_id=5), state=SimpleNamespace(), user=SimpleNamespace(id=2))

        self.assertIn("پروفایل عمومی", callback.message.edit_text.await_args.args[0])
        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()