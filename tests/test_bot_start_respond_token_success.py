import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_start_with_token
from models.offer import OfferStatus, OfferType


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


class BotStartRespondTokenSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_respond_token_shows_confirmation_prompt(self):
        offer = SimpleNamespace(
            id=5,
            status=OfferStatus.ACTIVE,
            user_id=1,
            offer_type=OfferType.BUY,
            quantity=3,
            price=120000,
            user=SimpleNamespace(account_name="seller"),
            commodity=SimpleNamespace(name="سکه"),
        )
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=33),
            delete=AsyncMock(),
            answer=AsyncMock(),
        )

        with patch("bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(offer))):
            await handle_start_with_token(message, SimpleNamespace(args="respond_5"), state=SimpleNamespace(), user=SimpleNamespace(id=2))

        self.assertIn("تایید معامله", message.answer.await_args.args[0])
        self.assertEqual(message.answer.await_args.kwargs["parse_mode"], "Markdown")


if __name__ == "__main__":
    unittest.main()