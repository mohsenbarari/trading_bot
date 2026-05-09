from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from bot import message_manager


class BotMessageManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        message_manager._anchor_messages.clear()

    async def test_anchor_helpers(self):
        message_manager.set_anchor(1, 10)
        self.assertEqual(message_manager.get_anchor(1), 10)
        self.assertTrue(message_manager.is_anchor(1, 10))
        message_manager.clear_anchor(1)
        self.assertIsNone(message_manager.get_anchor(1))

    async def test_delete_message_task_skips_anchor_and_handles_errors(self):
        bot = AsyncMock()
        message_manager.set_anchor(1, 10)

        with patch('bot.message_manager.asyncio.sleep', AsyncMock()):
            await message_manager._delete_message_task(bot, 1, 10, 5)
        bot.delete_message.assert_not_awaited()

        bot.delete_message = AsyncMock(side_effect=TelegramBadRequest(method='deleteMessage', message='gone'))
        with patch('bot.message_manager.asyncio.sleep', AsyncMock()):
            await message_manager._delete_message_task(bot, 1, 11, 5)
        bot.delete_message.assert_awaited_once_with(1, 11)

    async def test_schedule_delete_and_schedule_message_delete(self):
        bot = AsyncMock()
        with patch('bot.message_manager.asyncio.create_task') as create_task:
            create_task.side_effect = lambda coro: coro.close()
            message_manager.schedule_delete(bot, 1, 2, message_manager.DeleteDelay.NEVER)
            create_task.assert_not_called()

            message_manager.schedule_delete(bot, 1, 2, message_manager.DeleteDelay.DEFAULT)
            create_task.assert_called_once()

        message = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=99), message_id=77)
        with patch('bot.message_manager.schedule_delete') as schedule_delete:
            message_manager.schedule_message_delete(message)
        schedule_delete.assert_called_once_with(bot, 99, 77, message_manager.DeleteDelay.DEFAULT)

    async def test_delete_previous_anchor_and_handle_user_message(self):
        bot = AsyncMock()
        message_manager.set_anchor(5, 55)
        await message_manager.delete_previous_anchor(bot, 5)
        bot.delete_message.assert_awaited_once_with(5, 55)
        self.assertIsNone(message_manager.get_anchor(5))

        message_manager.set_anchor(6, 66)
        with patch('bot.message_manager.schedule_delete') as schedule_delete:
            await message_manager.delete_previous_anchor(bot, 6, delay=9)
        schedule_delete.assert_called_once_with(bot, 6, 66, 9)

        message = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=7), message_id=70)
        with patch('bot.message_manager.schedule_message_delete') as schedule_message_delete, patch(
            'bot.message_manager.delete_previous_anchor', AsyncMock()
        ) as delete_previous_anchor:
            await message_manager.handle_user_message(message, is_new_anchor=True)

        schedule_message_delete.assert_called_once_with(message, message_manager.DeleteDelay.DEFAULT)
        delete_previous_anchor.assert_awaited_once_with(bot, 7, delay=message_manager.DeleteDelay.DEFAULT.value)

    async def test_delete_previous_anchor_swallows_delete_errors_and_handle_without_new_anchor(self):
        bot = AsyncMock()
        message_manager.set_anchor(8, 88)
        bot.delete_message = AsyncMock(side_effect=RuntimeError('boom'))
        await message_manager.delete_previous_anchor(bot, 8)
        self.assertIsNone(message_manager.get_anchor(8))

        with patch('bot.message_manager.schedule_message_delete') as schedule_message_delete, patch(
            'bot.message_manager.delete_previous_anchor', AsyncMock()
        ) as delete_previous_anchor:
            message = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=9), message_id=90)
            await message_manager.handle_user_message(message, is_new_anchor=False)

        schedule_message_delete.assert_called_once_with(message, message_manager.DeleteDelay.DEFAULT)
        delete_previous_anchor.assert_not_awaited()

    async def test_delete_message_task_swallows_unexpected_delete_errors(self):
        bot = AsyncMock()
        bot.delete_message = AsyncMock(side_effect=RuntimeError('boom'))
        with patch('bot.message_manager.asyncio.sleep', AsyncMock()):
            await message_manager._delete_message_task(bot, 1, 22, 5)
        bot.delete_message.assert_awaited_once_with(1, 22)


if __name__ == '__main__':
    unittest.main()