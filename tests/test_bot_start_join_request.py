import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_channel_join_request
from core.enums import UserAccountStatus, UserRole


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


def make_join_request(chat_id=100, user_id=7, user_chat_id=77):
    bot = SimpleNamespace(
        decline_chat_join_request=AsyncMock(),
        approve_chat_join_request=AsyncMock(),
        send_message=AsyncMock(),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id),
        user_chat_id=user_chat_id,
        bot=bot,
    )


class BotStartJoinRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_join_request_ignores_unrelated_chat_and_declines_missing_or_blocked_users(self):
        join_request = make_join_request(chat_id=999)
        with patch("bot.handlers.start.settings.channel_id", 100):
            await handle_channel_join_request(join_request)
        join_request.bot.decline_chat_join_request.assert_not_awaited()

        join_request = make_join_request(chat_id=100)
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))
        ):
            await handle_channel_join_request(join_request)
        join_request.bot.decline_chat_join_request.assert_awaited_once_with(chat_id=100, user_id=7)
        join_request.bot.send_message.assert_awaited_once()

        blocked_user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.INACTIVE,
            is_deleted=False,
        )
        join_request = make_join_request(chat_id=100)
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(blocked_user))
        ):
            await handle_channel_join_request(join_request)
        self.assertIn("غیرفعال است", join_request.bot.send_message.await_args.kwargs["text"])

    async def test_join_request_approve_and_notification_failures_are_tolerated(self):
        active_user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        join_request = make_join_request(chat_id=100)
        join_request.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(active_user))
        ), patch(
            "bot.handlers.start.logger.exception"
        ) as logger_mock:
            await handle_channel_join_request(join_request)
        join_request.bot.approve_chat_join_request.assert_awaited_once_with(chat_id=100, user_id=7)
        logger_mock.assert_called_once()

        join_request = make_join_request(chat_id=100)
        join_request.bot.send_message = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("bot.handlers.start.settings.channel_id", 100), patch(
            "bot.handlers.start.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))
        ), patch("bot.handlers.start.logger.exception") as logger_mock:
            await handle_channel_join_request(join_request)
        join_request.bot.decline_chat_join_request.assert_awaited_once_with(chat_id=100, user_id=7)
        logger_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
