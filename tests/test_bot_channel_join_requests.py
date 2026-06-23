import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.start import handle_channel_join_request
from bot.utils.channel_invites import create_channel_join_request_link
from core.enums import UserAccountStatus, UserRole


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, user):
        self.user = user

    async def execute(self, stmt):
        return FakeExecuteResult(self.user)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_join_request(user_id=7):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-1003367566585),
        from_user=SimpleNamespace(id=user_id),
        user_chat_id=7000 + user_id,
        bot=SimpleNamespace(
            approve_chat_join_request=AsyncMock(),
            decline_chat_join_request=AsyncMock(),
            send_message=AsyncMock(),
        ),
    )


class ChannelJoinRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_channel_join_request_link_uses_join_request_mode(self):
        bot = SimpleNamespace(
            create_chat_invite_link=AsyncMock(return_value=SimpleNamespace(invite_link="https://t.me/joinreq"))
        )

        with patch(
            "bot.utils.channel_invites.settings",
            SimpleNamespace(channel_id=-1003367566585, channel_invite_link=None),
        ):
            link = await create_channel_join_request_link(bot, user_id=55)

        self.assertEqual(link, "https://t.me/joinreq")
        kwargs = bot.create_chat_invite_link.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], -1003367566585)
        self.assertTrue(kwargs["creates_join_request"])
        self.assertEqual(kwargs["name"], "channel-join-55")

    async def test_handle_channel_join_request_approves_registered_user(self):
        join_request = make_join_request()
        user = SimpleNamespace(
            telegram_id=7,
            role=UserRole.STANDARD,
            has_bot_access=False,
            is_deleted=False,
            account_status=UserAccountStatus.ACTIVE,
        )

        with patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user)),
        ), patch("bot.handlers.start.settings", SimpleNamespace(channel_id=-1003367566585)):
            await handle_channel_join_request(join_request)

        join_request.bot.approve_chat_join_request.assert_awaited_once_with(
            chat_id=-1003367566585,
            user_id=7,
        )
        join_request.bot.decline_chat_join_request.assert_not_awaited()
        self.assertIn("تایید شد", join_request.bot.send_message.await_args.kwargs["text"])

    async def test_handle_channel_join_request_declines_unknown_user(self):
        join_request = make_join_request(user_id=9)

        with patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(None)),
        ), patch("bot.handlers.start.settings", SimpleNamespace(channel_id=-1003367566585)):
            await handle_channel_join_request(join_request)

        join_request.bot.decline_chat_join_request.assert_awaited_once_with(
            chat_id=-1003367566585,
            user_id=9,
        )
        join_request.bot.approve_chat_join_request.assert_not_awaited()
        self.assertIn("همگام\u200cسازی", join_request.bot.send_message.await_args.kwargs["text"])
        self.assertIn("دوباره تلاش کنید", join_request.bot.send_message.await_args.kwargs["text"])

    async def test_handle_channel_join_request_declines_inactive_user(self):
        join_request = make_join_request(user_id=10)
        user = SimpleNamespace(
            telegram_id=10,
            role=UserRole.STANDARD,
            has_bot_access=True,
            is_deleted=False,
            account_status=UserAccountStatus.INACTIVE,
        )

        with patch(
            "bot.handlers.start.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(user)),
        ), patch("bot.handlers.start.settings", SimpleNamespace(channel_id=-1003367566585)):
            await handle_channel_join_request(join_request)

        join_request.bot.decline_chat_join_request.assert_awaited_once_with(
            chat_id=-1003367566585,
            user_id=10,
        )
        join_request.bot.approve_chat_join_request.assert_not_awaited()
        self.assertIn("غیرفعال", join_request.bot.send_message.await_args.kwargs["text"])


if __name__ == "__main__":
    unittest.main()
