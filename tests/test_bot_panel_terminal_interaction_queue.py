import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import panel


class _SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _State:
    async def get_data(self):
        return {"editing_setting": "offer_expiry_minutes"}

    async def clear(self):
        return None


class BotPanelTerminalInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_feedback_uses_distinct_interaction_sources(self):
        message = SimpleNamespace(text="x", answer=AsyncMock(), answer_document=AsyncMock())
        user = SimpleNamespace(id=7, role=panel.UserRole.SUPER_ADMIN)

        with patch(
            "bot.handlers.panel.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as enqueue:
            with patch(
                "bot.handlers.panel.is_user_global_web_locked",
                return_value=True,
            ):
                await panel.show_my_profile_and_change_keyboard(message, _State(), user)
                await panel.show_colleagues_list(message, _State(), user)

            with (
                patch(
                    "bot.handlers.panel.is_user_global_web_locked",
                    return_value=False,
                ),
                patch(
                    "bot.handlers.panel.AsyncSessionLocal",
                    return_value=_SessionContext(),
                ),
                patch(
                    "bot.handlers.panel._can_view_colleagues_list",
                    new=AsyncMock(return_value=False),
                ),
            ):
                await panel.show_colleagues_list(message, _State(), user)

            with patch(
                "bot.handlers.panel._load_recent_user_trades",
                new=AsyncMock(return_value=[]),
            ):
                await panel.show_recent_trades_pdf(message, _State(), user)

            with (
                patch(
                    "bot.handlers.panel._load_recent_user_trades",
                    new=AsyncMock(return_value=[SimpleNamespace(id=1)]),
                ),
                patch(
                    "bot.handlers.panel.generate_trade_history_pdf_file",
                    side_effect=RuntimeError("boom"),
                ),
            ):
                await panel.show_recent_trades_pdf(message, _State(), user)

            with patch(
                "bot.handlers.panel.handoff_navigation_button",
                new=AsyncMock(return_value=False),
            ):
                await panel.handle_settings_new_value(message, _State(), user)

            message.text = "8"
            with (
                patch(
                    "bot.handlers.panel.handoff_navigation_button",
                    new=AsyncMock(return_value=False),
                ),
                patch(
                    "bot.handlers.panel._settings_admin_write_decision",
                    return_value=SimpleNamespace(ok=False),
                ),
                patch(
                    "bot.handlers.panel.admin_write_rejection_message",
                    return_value="سرور فعلی مرجع نیست",
                ),
            ):
                await panel.handle_settings_new_value(message, _State(), user)

        self.assertEqual(
            [call.kwargs["source_key"] for call in enqueue.await_args_list],
            [
                "panel-profile-account-locked",
                "panel-colleagues-account-locked",
                "panel-colleagues-not-allowed",
                "panel-recent-trades-empty",
                "panel-recent-trades-export-error",
                "panel-settings-value-invalid",
                "panel-settings-not-authoritative",
            ],
        )
        message.answer.assert_not_awaited()
        message.answer_document.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
