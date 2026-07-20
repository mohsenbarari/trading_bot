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


def _callback(data="tgb:all"):
    return SimpleNamespace(
        id="callback-id",
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
    )


def _state(data=None):
    return SimpleNamespace(
        clear=AsyncMock(),
        get_data=AsyncMock(return_value=dict(data or {})),
        set_state=AsyncMock(),
        update_data=AsyncMock(),
    )


class _SessionContext:
    def __init__(self, session=None):
        self.session = session or SimpleNamespace()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotAdminBroadcastInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_text_prompt_uses_known_target_edit_adapter(self):
        callback = _callback()
        state = _state()
        user = _user()
        with patch.object(
            admin_broadcast,
            "edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as edit:
            await admin_broadcast._ask_for_message_text(callback, state, user)

        state.set_state.assert_awaited_once_with(
            admin_broadcast.AdminBroadcast.awaiting_message_text
        )
        self.assertEqual(edit.await_args.args[:2], (callback, user))
        self.assertEqual(
            edit.await_args.kwargs["source_key"],
            "admin-broadcast-ask-text",
        )
        callback.message.edit_text.assert_not_awaited()

    async def test_simple_callback_text_edits_use_distinct_durable_sources(self):
        cases = (
            (
                admin_broadcast.cancel_telegram_admin_broadcast,
                "tgb:cancel",
                {},
                "admin-broadcast-cancel",
            ),
            (
                admin_broadcast.choose_group_recipients,
                "tgb:groups",
                {},
                "admin-broadcast-groups",
            ),
            (
                admin_broadcast.choose_selected_recipients,
                "tgb:selected",
                {},
                "admin-broadcast-selected",
            ),
            (
                admin_broadcast.search_again,
                "tgb:search_again",
                {"selected_user_ids": [1, 2]},
                "admin-broadcast-search-again",
            ),
        )
        user = _user()
        for handler, data, state_data, source_key in cases:
            with self.subTest(source_key=source_key):
                callback = _callback(data)
                with patch.object(
                    admin_broadcast,
                    "edit_callback_message_via_runtime",
                    new=AsyncMock(),
                ) as edit:
                    await handler(callback, _state(state_data), user)

                self.assertEqual(edit.await_args.args[:2], (callback, user))
                self.assertEqual(edit.await_args.kwargs["source_key"], source_key)
                callback.message.edit_text.assert_not_awaited()

    async def test_all_ask_text_routes_forward_authenticated_user(self):
        cases = (
            (admin_broadcast.choose_all_recipients, "tgb:all", {}),
            (
                admin_broadcast.finish_group_selection,
                "tgb:groups_done",
                {"target_groups": ["ordinary"]},
            ),
            (
                admin_broadcast.finish_selected_recipients,
                "tgb:selected_done",
                {"selected_user_ids": [11]},
            ),
        )
        user = _user()
        for handler, data, state_data in cases:
            with self.subTest(handler=handler.__name__):
                callback = _callback(data)
                state = _state(state_data)
                with patch.object(
                    admin_broadcast,
                    "_ask_for_message_text",
                    new=AsyncMock(),
                ) as ask:
                    await handler(callback, state, user)

                ask.assert_awaited_once_with(callback, state, user)

    async def test_selected_user_toggle_uses_known_target_edit_adapter(self):
        callback = _callback("tgb:tu:3")
        state = _state(
            {
                "selected_user_ids": [2],
                "last_search_query": "",
            }
        )
        user = _user()
        with patch.object(
            admin_broadcast,
            "edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as edit:
            await admin_broadcast.toggle_selected_user(callback, state, user)

        state.update_data.assert_awaited_once_with(selected_user_ids=[2, 3])
        self.assertEqual(
            edit.await_args.kwargs["source_key"],
            "admin-broadcast-selected-toggle",
        )
        callback.message.edit_text.assert_not_awaited()

    async def test_confirmation_outcomes_use_distinct_known_target_edits(self):
        cases = (
            (
                TelegramAdminBroadcastValidationError("invalid"),
                "admin-broadcast-confirm-error",
            ),
            (
                SimpleNamespace(
                    broadcast=SimpleNamespace(id=41),
                    receipt_count=0,
                ),
                "admin-broadcast-confirm-empty",
            ),
            (
                SimpleNamespace(
                    broadcast=SimpleNamespace(id=42),
                    receipt_count=3,
                ),
                "admin-broadcast-confirm-queued",
            ),
        )
        user = _user()
        state_data = {
            "content": "پیام معتبر",
            "audience_type": "all",
            "target_groups": [],
            "selected_user_ids": [],
        }
        for create_result, source_key in cases:
            with self.subTest(source_key=source_key):
                callback = _callback("tgb:confirm")
                session = SimpleNamespace(commit=AsyncMock())
                create = AsyncMock()
                if isinstance(create_result, Exception):
                    create.side_effect = create_result
                else:
                    create.return_value = create_result
                with (
                    patch.object(
                        admin_broadcast,
                        "AsyncSessionLocal",
                        return_value=_SessionContext(session),
                    ),
                    patch.object(
                        admin_broadcast,
                        "create_telegram_admin_broadcast",
                        new=create,
                    ),
                    patch.object(
                        admin_broadcast,
                        "edit_callback_message_via_runtime",
                        new=AsyncMock(),
                    ) as edit,
                ):
                    await admin_broadcast.confirm_telegram_admin_broadcast(
                        callback,
                        _state(state_data),
                        user,
                    )

                self.assertEqual(edit.await_args.kwargs["source_key"], source_key)
                callback.message.edit_text.assert_not_awaited()

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
