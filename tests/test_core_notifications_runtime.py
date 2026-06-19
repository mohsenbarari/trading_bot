import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import notifications


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
        gateway_send = AsyncMock(return_value=SimpleNamespace(ok=True))
        with patch.object(notifications.settings, 'server_mode', 'foreign'), patch.object(
            notifications.settings, 'bot_token', 'token'
        ), patch.object(notifications.telegram_gateway, "send_message", gateway_send):
            await notifications.send_telegram_message(2, 'hello')
        gateway_send.assert_awaited_once_with(
            2,
            'hello',
            parse_mode='Markdown',
            idempotency_key='notification:2',
        )

        failing_send = AsyncMock(return_value=SimpleNamespace(ok=False, error="boom", status_code=None))
        with patch.object(notifications.settings, 'server_mode', 'foreign'), patch.object(
            notifications.settings, 'bot_token', 'token'
        ), patch.object(notifications.telegram_gateway, "send_message", failing_send), patch.object(
            notifications, 'logger'
        ) as logger:
            with self.assertRaises(RuntimeError):
                await notifications.send_telegram_message(2, 'hello')

        logger.error.assert_called_once()


if __name__ == '__main__':
    unittest.main()
