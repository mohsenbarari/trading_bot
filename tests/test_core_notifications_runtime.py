import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core import notifications


class _BotContext:
    def __init__(self, bot):
        self.bot = bot

    async def __aenter__(self):
        return self.bot

    async def __aexit__(self, exc_type, exc, tb):
        return False


class CoreNotificationsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_telegram_message_relays_from_iran(self):
        with patch.object(notifications.settings, 'server_mode', 'iran'), patch(
            'core.notifications.push_sync_direct'
        ) as push_sync_direct:
            await notifications.send_telegram_message(1, 'hello', parse_mode='HTML')

        push_sync_direct.assert_called_once()
        payload = push_sync_direct.call_args.args[0]
        self.assertEqual(payload['chat_id'], 1)
        self.assertEqual(payload['parse_mode'], 'HTML')

    async def test_send_telegram_message_logs_relay_failures(self):
        with patch.object(notifications.settings, 'server_mode', 'iran'), patch(
            'core.notifications.push_sync_direct', side_effect=RuntimeError('down')
        ), patch.object(notifications, 'logger') as logger:
            await notifications.send_telegram_message(1, 'hello')

        logger.warning.assert_called_once()

    async def test_send_telegram_message_sends_directly_and_reraises_errors(self):
        bot = AsyncMock()
        with patch.object(notifications.settings, 'server_mode', 'foreign'), patch.object(
            notifications.settings, 'bot_token', 'token'
        ), patch('core.notifications.Bot', return_value=_BotContext(bot)):
            await notifications.send_telegram_message(2, 'hello')
        bot.send_message.assert_awaited_once_with(chat_id=2, text='hello', parse_mode='Markdown')

        failing_bot = AsyncMock()
        failing_bot.send_message = AsyncMock(side_effect=RuntimeError('boom'))
        with patch.object(notifications.settings, 'server_mode', 'foreign'), patch.object(
            notifications.settings, 'bot_token', 'token'
        ), patch('core.notifications.Bot', return_value=_BotContext(failing_bot)), patch.object(
            notifications, 'logger'
        ) as logger:
            with self.assertRaises(RuntimeError):
                await notifications.send_telegram_message(2, 'hello')

        logger.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()