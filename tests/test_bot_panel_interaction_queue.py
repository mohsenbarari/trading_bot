import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import panel


class _SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotPanelInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_reply_panel_actions_use_distinct_interaction_sources(self):
        message = SimpleNamespace(answer=AsyncMock())
        user = SimpleNamespace(id=7, role=panel.UserRole.STANDARD)
        block_status = {
            "can_block": True,
            "current_blocked": 1,
            "max_blocked": 4,
        }

        with (
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue,
            patch("core.db.AsyncSessionLocal", return_value=_SessionContext()),
            patch(
                "core.services.block_service.get_block_status",
                new=AsyncMock(return_value=block_status),
            ),
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch(
                "bot.handlers.panel._can_view_support",
                new=AsyncMock(return_value=True),
            ),
        ):
            await panel.handle_user_settings_button(message, SimpleNamespace(), user)
            await panel.handle_simple_settings_button(message, user)
            await panel.show_support_contact(message, user)

        self.assertEqual(
            [call.kwargs["source_key"] for call in enqueue.await_args_list],
            [
                "panel-user-settings",
                "panel-simple-settings",
                "panel-support-contact",
            ],
        )
        self.assertEqual(enqueue.await_args_list[0].kwargs["parse_mode"], "Markdown")
        self.assertIsNotNone(enqueue.await_args_list[0].kwargs["reply_markup"])
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
