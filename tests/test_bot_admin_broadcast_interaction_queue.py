import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import admin_broadcast
from core.enums import UserRole
from core.services.telegram_admin_broadcast_service import (
    TELEGRAM_BROADCAST_TEXT_MAX_LENGTH,
    TelegramAdminBroadcastValidationError,
)


def _user():
    return SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN)


def _message(text="text"):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _state(data=None):
    return SimpleNamespace(
        clear=AsyncMock(),
        get_data=AsyncMock(return_value=dict(data or {})),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
    )


class _SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotAdminBroadcastInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_uses_durable_interaction_adapter(self):
        message = _message()
        state = _state()
        user = _user()
        with patch.object(
            admin_broadcast,
            "answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await admin_broadcast.start_telegram_admin_broadcast(
                message,
                state,
                user,
            )

        state.clear.assert_awaited_once()
        self.assertEqual(answer.await_args.args[:2], (message, user))
        self.assertEqual(answer.await_args.kwargs["source_key"], "admin-broadcast-menu")
        message.answer.assert_not_awaited()

    async def test_recipient_search_uses_one_durable_interaction(self):
        message = _message("ali")
        state = _state({"selected_user_ids": [2]})
        user = _user()
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch.object(
                admin_broadcast,
                "search_telegram_admin_broadcast_recipients",
                new=AsyncMock(return_value=()),
            ),
            patch.object(
                admin_broadcast,
                "answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as answer,
        ):
            await admin_broadcast.process_selected_recipient_search(
                message,
                state,
                user,
            )

        self.assertEqual(
            answer.await_args.kwargs["source_key"],
            "admin-broadcast-search-results",
        )
        state.update_data.assert_awaited_once_with(last_search_query="ali")

    async def test_content_validation_responses_have_distinct_sources(self):
        user = _user()
        cases = (
            ("", "admin-broadcast-content-empty"),
            (
                "x" * (TELEGRAM_BROADCAST_TEXT_MAX_LENGTH + 1),
                "admin-broadcast-content-too-long",
            ),
        )
        for text, source_key in cases:
            with self.subTest(source_key=source_key):
                message = _message(text)
                with patch.object(
                    admin_broadcast,
                    "answer_incoming_message_via_runtime",
                    new=AsyncMock(),
                ) as answer:
                    await admin_broadcast.process_broadcast_message_text(
                        message,
                        _state(),
                        user,
                    )
                self.assertEqual(answer.await_args.kwargs["source_key"], source_key)

    async def test_invalid_recipients_clear_state_after_durable_response(self):
        message = _message("پیام معتبر")
        state = _state({"audience_type": "all"})
        user = _user()
        with (
            patch.object(
                admin_broadcast,
                "_estimate_recipient_count",
                new=AsyncMock(
                    side_effect=TelegramAdminBroadcastValidationError(
                        "invalid_recipients"
                    )
                ),
            ),
            patch.object(
                admin_broadcast,
                "answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as answer,
        ):
            await admin_broadcast.process_broadcast_message_text(
                message,
                state,
                user,
            )

        self.assertEqual(
            answer.await_args.kwargs["source_key"],
            "admin-broadcast-recipients-invalid",
        )
        state.clear.assert_awaited_once()

    async def test_preview_uses_durable_interaction_and_keeps_confirmation_state(self):
        message = _message("پیام معتبر")
        state = _state({"audience_type": "all"})
        user = _user()
        with (
            patch.object(
                admin_broadcast,
                "_estimate_recipient_count",
                new=AsyncMock(return_value=3),
            ),
            patch.object(
                admin_broadcast,
                "answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as answer,
        ):
            await admin_broadcast.process_broadcast_message_text(
                message,
                state,
                user,
            )

        state.set_state.assert_awaited_once_with(
            admin_broadcast.AdminBroadcast.awaiting_confirmation
        )
        self.assertEqual(
            answer.await_args.kwargs["source_key"],
            "admin-broadcast-preview",
        )


if __name__ == "__main__":
    unittest.main()
