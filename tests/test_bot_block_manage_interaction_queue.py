import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import (
    build_block_menu_text,
    get_block_menu_keyboard,
    send_block_menu_message,
)


class _SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotBlockManageInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_block_menu_entry_uses_durable_interaction_adapter(self):
        status = {
            "can_block": True,
            "current_blocked": 1,
            "max_blocked": 4,
            "remaining": 3,
        }
        message = SimpleNamespace(answer=AsyncMock())
        user = SimpleNamespace(id=9)
        queued = SimpleNamespace(notification=SimpleNamespace(created=True))

        with patch(
            "bot.handlers.block_manage.AsyncSessionLocal",
            return_value=_SessionContext(),
        ), patch(
            "bot.handlers.block_manage.get_block_status",
            new=AsyncMock(return_value=status),
        ), patch(
            "bot.handlers.block_manage.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=queued),
        ) as adapter:
            result = await send_block_menu_message(message, user)

        self.assertIs(result, queued)
        message.answer.assert_not_awaited()
        adapter.assert_awaited_once()
        args = adapter.await_args.args
        kwargs = adapter.await_args.kwargs
        self.assertEqual(args, (message, user, build_block_menu_text(status)))
        self.assertEqual(kwargs["source_key"], "block-menu-entry")
        self.assertEqual(kwargs["parse_mode"], "Markdown")
        self.assertEqual(
            kwargs["reply_markup"].model_dump(mode="json", exclude_none=True),
            get_block_menu_keyboard(status).model_dump(
                mode="json",
                exclude_none=True,
            ),
        )


if __name__ == "__main__":
    unittest.main()
