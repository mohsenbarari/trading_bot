import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import BotCommandScopeAllPrivateChats, MenuButtonCommands

from bot.telegram_command_menu import (
    START_COMMAND_DESCRIPTION,
    configure_interactive_bot_command_menu,
)


class TelegramCommandMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_configures_start_for_private_chats_and_commands_menu(self):
        bot = MagicMock()
        bot.set_my_commands = AsyncMock(return_value=True)
        bot.set_chat_menu_button = AsyncMock(return_value=True)

        configured = await configure_interactive_bot_command_menu(bot)

        self.assertTrue(configured)
        commands_call = bot.set_my_commands.await_args
        commands = commands_call.kwargs["commands"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].command, "start")
        self.assertEqual(commands[0].description, START_COMMAND_DESCRIPTION)
        self.assertIsInstance(
            commands_call.kwargs["scope"],
            BotCommandScopeAllPrivateChats,
        )
        self.assertIsInstance(
            bot.set_chat_menu_button.await_args.kwargs["menu_button"],
            MenuButtonCommands,
        )

    async def test_keeps_startup_available_when_telegram_rejects_configuration(self):
        bot = MagicMock()
        bot.set_my_commands = AsyncMock(side_effect=RuntimeError("telegram unavailable"))
        bot.set_chat_menu_button = AsyncMock()

        with patch("bot.telegram_command_menu.logger.warning") as warning:
            configured = await configure_interactive_bot_command_menu(bot)

        self.assertFalse(configured)
        bot.set_chat_menu_button.assert_not_awaited()
        warning.assert_called_once()
        self.assertEqual(
            warning.call_args.kwargs["extra"],
            {
                "event": "bot.command_menu_configuration_failed",
                "error_type": "RuntimeError",
            },
        )


if __name__ == "__main__":
    unittest.main()
