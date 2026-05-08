import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_start_with_token
from models.offer import OfferStatus


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

    async def test_handle_start_with_respond_token_handles_missing_inactive_and_self_offer(self):
        message = make_message()
        user = SimpleNamespace(id=7)
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))):
            await handle_start_with_token(message, SimpleNamespace(args="respond_5"), state=SimpleNamespace(), user=user)
        self.assertIn("یافت نشد", message.answer.await_args.args[0])

        inactive_offer = SimpleNamespace(status=OfferStatus.COMPLETED)
        message = make_message()
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(inactive_offer))):
            await handle_start_with_token(message, SimpleNamespace(args="respond_5"), state=SimpleNamespace(), user=user)
        self.assertIn("دیگر فعال نیست", message.answer.await_args.args[0])

        self_offer = SimpleNamespace(status=OfferStatus.ACTIVE, user_id=7)
        message = make_message()
        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(self_offer))):
            await handle_start_with_token(message, SimpleNamespace(args="respond_5"), state=SimpleNamespace(), user=user)
        self.assertIn("لفظ خودتان", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()