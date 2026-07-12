import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.utils import channel_invites


class BotChannelInvitesTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_channel_join_request_link_uses_static_fallback_without_channel_id(self):
        with patch.object(channel_invites.settings, 'channel_id', None), patch.object(
            channel_invites.settings, 'channel_invite_link', 'https://static.example/invite'
        ):
            bot = AsyncMock()
            link = await channel_invites.create_channel_join_request_link(bot, user_id=12)

        self.assertEqual(link, 'https://static.example/invite')
        bot.create_chat_invite_link.assert_not_awaited()

    async def test_create_channel_join_request_link_falls_back_and_logs_on_bot_errors(self):
        bot = AsyncMock()
        bot.create_chat_invite_link = AsyncMock(side_effect=RuntimeError('bot failed'))

        with patch.object(channel_invites.settings, 'channel_id', -100123), patch.object(
            channel_invites.settings, 'channel_invite_link', 'https://fallback.example/invite'
        ), patch.object(channel_invites.logger, 'exception') as exception_mock:
            link = await channel_invites.create_channel_join_request_link(bot, user_id=777)

        self.assertEqual(link, 'https://fallback.example/invite')
        exception_mock.assert_called_once_with('Failed to create channel join-request link')

    async def test_build_channel_join_request_line_handles_none_and_successful_links(self):
        with patch.object(channel_invites.settings, 'channel_invite_link', None):
            self.assertIsNone(await channel_invites.build_channel_join_request_line(None))

        bot = MagicMock()
        with patch.object(
            channel_invites, 'create_channel_join_request_link', AsyncMock(return_value='https://dynamic.example/invite')
        ):
            line = await channel_invites.build_channel_join_request_line(bot, user_id=22)

        self.assertEqual(line, '🔗 [درخواست عضویت در کانال معاملات](https://dynamic.example/invite)')

    async def test_build_channel_join_request_text_returns_plain_clickable_url(self):
        bot = MagicMock()
        with patch.object(
            channel_invites, 'create_channel_join_request_link', AsyncMock(return_value='https://dynamic.example/invite_token')
        ):
            text = await channel_invites.build_channel_join_request_text(bot, user_id=22)

        self.assertEqual(text, '🔗 درخواست عضویت در کانال معاملات:\nhttps://dynamic.example/invite_token')

    async def test_build_channel_access_text_reuses_resolved_link_with_returning_copy(self):
        bot = MagicMock()
        with patch.object(
            channel_invites,
            'create_channel_join_request_link',
            AsyncMock(return_value='https://dynamic.example/returning'),
        ):
            text = await channel_invites.build_channel_access_text(bot, user_id=22)

        self.assertEqual(text, '🔗 کانال معاملات:\nhttps://dynamic.example/returning')

    async def test_create_channel_join_request_link_uses_trimmed_name_for_user_specific_links(self):
        bot = AsyncMock()
        bot.create_chat_invite_link = AsyncMock(return_value=SimpleNamespace(invite_link='https://dynamic.example/invite'))

        with patch.object(channel_invites.settings, 'channel_id', -100123), patch.object(
            channel_invites.settings, 'channel_invite_link', None
        ):
            link = await channel_invites.create_channel_join_request_link(bot, user_id=12345678901234567890)

        self.assertEqual(link, 'https://dynamic.example/invite')
        self.assertLessEqual(len(bot.create_chat_invite_link.await_args.kwargs['name']), 32)
        self.assertTrue(bot.create_chat_invite_link.await_args.kwargs['creates_join_request'])


if __name__ == '__main__':
    unittest.main()
