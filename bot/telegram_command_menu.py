"""Telegram command-menu configuration for the interactive bot identity."""

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
)

logger = logging.getLogger(__name__)

START_COMMAND_DESCRIPTION = "شروع بات و نمایش منوی اصلی"
INTERACTIVE_BOT_COMMANDS = (
    BotCommand(command="start", description=START_COMMAND_DESCRIPTION),
)


async def configure_interactive_bot_command_menu(bot: Bot) -> bool:
    """Expose /start through Telegram's default private-chat Menu button.

    Menu configuration is intentionally best-effort: a transient Bot API failure
    must not prevent polling or the delivery workers from starting.
    """

    try:
        commands_configured = await bot.set_my_commands(
            commands=list(INTERACTIVE_BOT_COMMANDS),
            scope=BotCommandScopeAllPrivateChats(),
        )
        menu_configured = await bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(),
        )
    except Exception as exc:
        logger.warning(
            "Telegram interactive command menu configuration failed",
            extra={
                "event": "bot.command_menu_configuration_failed",
                "error_type": type(exc).__name__,
            },
        )
        return False

    configured = bool(commands_configured and menu_configured)
    logger.log(
        logging.INFO if configured else logging.WARNING,
        (
            "Telegram interactive command menu configured"
            if configured
            else "Telegram interactive command menu configuration rejected"
        ),
        extra={
            "event": (
                "bot.command_menu_configured"
                if configured
                else "bot.command_menu_configuration_rejected"
            ),
            "commands_configured": bool(commands_configured),
            "menu_configured": bool(menu_configured),
        },
    )
    return configured
