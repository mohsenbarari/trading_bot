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


class BotStartProfileTokenSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_profile_token_shows_public_profile(self):
        target_user = SimpleNamespace(
            id=9,
            is_deleted=False,
            account_name="target",
            mobile_number="09120000000",
            address="تهران",
        )
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=30),
            delete=AsyncMock(),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=90)),
        )

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(target_user))), patch(
            "bot.handlers.start.delete_previous_anchor", new=AsyncMock()
        ) as delete_anchor, patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args="profile_9"), state=SimpleNamespace(), user=None)

        message.delete.assert_awaited_once()
        delete_anchor.assert_awaited_once()
        self.assertIn("پروفایل عمومی", message.answer.await_args.args[0])
        set_anchor.assert_not_called()


if __name__ == "__main__":
    unittest.main()