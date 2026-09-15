"""Characterization tests for trade history back_to_profile callback router dispatch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-BACK-TO-PROFILE-L554

This characterization test file exercises registered callback-query router dispatch
for seed BOT-TRADE-CALLBACK-QUERY-TRADE-HISTORY-BACK-TO-PROFILE-L554
(bot.handlers.trade_history.back_to_profile).

Freezes two scenarios:
- an authenticated request for another profile when load_bot_public_profile returns unavailable
- an authenticated request for a different, accessible profile where load_bot_public_profile returns a profile, edits the message with built text and keyboard, and acknowledges the callback once

Exclusions:
This unit is test-only; it must not touch product code or network/runtime state,
cover self-profile behavior, enter profile-loader internals,
reconcile inventories, claim blocker/seed/Stage closure, or change other tests.

This is partial characterization evidence only: this partial unit closes no seed,
blocker, reconciliation, or Stage.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock

from aiogram import types as aiogram_types

from bot.callbacks import ProfileCallback
from bot.handlers import trade_history


class ExplicitAsyncSessionContext:
    """Explicit async context manager around a unique sentinel session."""

    def __init__(self, session: object) -> None:
        self.session = session
        self.enter_count = 0
        self.exit_count = 0
        self.aenter = mock.AsyncMock(return_value=session)
        self.aexit = mock.AsyncMock(return_value=False)

    async def __aenter__(self) -> object:
        self.enter_count += 1
        return await self.aenter()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.exit_count += 1
        return await self.aexit(exc_type, exc_val, exc_tb)


class TestTradeHistoryBackToProfileRouterCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    def _make_callback_event(
        self,
        *,
        callback_id: str,
        user_id: int,
        chat_id: int,
        message_id: int,
        data: str,
    ) -> aiogram_types.CallbackQuery:
        chat = aiogram_types.Chat(id=chat_id, type="private")
        message = aiogram_types.Message(
            message_id=message_id,
            date=datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc),
            chat=chat,
        )
        return aiogram_types.CallbackQuery(
            id=callback_id,
            from_user=aiogram_types.User(
                id=user_id,
                is_bot=False,
                first_name=f"User{user_id}",
            ),
            chat_instance=f"chat_inst_{callback_id}",
            message=message,
            data=data,
        )

    async def test_registered_unavailable_profile_alerts_once_without_edit(self):
        viewer_user_id = 101
        target_user_id = 202
        self.assertNotEqual(viewer_user_id, target_user_id)

        callback_data = ProfileCallback(target_user_id=target_user_id).pack()
        event = self._make_callback_event(
            callback_id="cb_back_to_profile_unavailable_101_202",
            user_id=viewer_user_id,
            chat_id=viewer_user_id,
            message_id=3001,
            data=callback_data,
        )

        runtime_user = SimpleNamespace(id=viewer_user_id)

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        sentinel_session = object()
        session_context = ExplicitAsyncSessionContext(sentinel_session)
        mock_session_factory = mock.Mock(return_value=session_context)

        expected_alert_text = "پروفایل در دسترس نیست."

        with (
            mock.patch(
                "bot.handlers.trade_history.AsyncSessionLocal",
                new=mock_session_factory,
            ),
            mock.patch(
                "bot.handlers.trade_history.load_bot_public_profile",
                new_callable=mock.AsyncMock,
                return_value=None,
            ) as mock_load_profile,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.build_bot_public_profile_text",
            ) as mock_build_profile_text,
            mock.patch(
                "bot.handlers.trade_history.build_bot_public_profile_keyboard",
            ) as mock_build_profile_keyboard,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_direct_answer,
        ):
            result = await trade_history.router.propagate_event(
                "callback_query",
                event,
                state=mock_state,
                user=runtime_user,
                raw_state=None,
            )

        self.assertIsNone(result)

        # Requirement 2: session factory and load_bot_public_profile assertions
        mock_session_factory.assert_called_once_with()
        self.assertEqual(mock_session_factory.call_args.args, ())
        self.assertEqual(mock_session_factory.call_args.kwargs, {})

        self.assertEqual(session_context.enter_count, 1)
        self.assertEqual(session_context.exit_count, 1)
        session_context.aenter.assert_awaited_once_with()
        session_context.aexit.assert_awaited_once_with(None, None, None)

        mock_load_profile.assert_awaited_once_with(
            sentinel_session,
            viewer=runtime_user,
            target_user_id=target_user_id,
        )
        self.assertIs(mock_load_profile.await_args.args[0], sentinel_session)
        self.assertIs(mock_load_profile.await_args.kwargs["viewer"], runtime_user)
        self.assertEqual(
            mock_load_profile.await_args.kwargs["target_user_id"],
            target_user_id,
        )

        # Requirement 3: answer callback and no edit/builders/direct answer/state operations
        mock_answer_runtime.assert_awaited_once_with(
            event,
            expected_alert_text,
            show_alert=True,
        )
        self.assertEqual(mock_answer_runtime.await_count, 1)
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(
            mock_answer_runtime.await_args.args[1],
            expected_alert_text,
        )
        self.assertEqual(
            mock_answer_runtime.await_args.kwargs,
            {"show_alert": True},
        )

        mock_edit_runtime.assert_not_called()
        mock_build_profile_text.assert_not_called()
        mock_build_profile_keyboard.assert_not_called()
        mock_direct_answer.assert_not_called()

        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()

    async def test_registered_accessible_profile_edits_then_answers_once(self):
        viewer_user_id = 101
        target_user_id = 202
        self.assertNotEqual(viewer_user_id, target_user_id)

        callback_data = ProfileCallback(target_user_id=target_user_id).pack()
        event = self._make_callback_event(
            callback_id="cb_back_to_profile_accessible_101_202",
            user_id=viewer_user_id,
            chat_id=viewer_user_id,
            message_id=3002,
            data=callback_data,
        )

        runtime_user = SimpleNamespace(id=viewer_user_id)

        mock_state = SimpleNamespace(
            clear=mock.AsyncMock(),
            update_data=mock.AsyncMock(),
            set_state=mock.AsyncMock(),
            set_data=mock.AsyncMock(),
            get_data=mock.AsyncMock(),
        )

        sentinel_session = object()
        session_context = ExplicitAsyncSessionContext(sentinel_session)
        mock_session_factory = mock.Mock(return_value=session_context)

        sentinel_profile = object()
        expected_profile_text = "👤 پروفایل تستی معتبر"
        expected_profile_keyboard = object()

        with (
            mock.patch(
                "bot.handlers.trade_history.AsyncSessionLocal",
                new=mock_session_factory,
            ),
            mock.patch(
                "bot.handlers.trade_history.load_bot_public_profile",
                new_callable=mock.AsyncMock,
                return_value=sentinel_profile,
            ) as mock_load_profile,
            mock.patch(
                "bot.handlers.trade_history.build_bot_public_profile_text",
                return_value=expected_profile_text,
            ) as mock_build_profile_text,
            mock.patch(
                "bot.handlers.trade_history.build_bot_public_profile_keyboard",
                return_value=expected_profile_keyboard,
            ) as mock_build_profile_keyboard,
            mock.patch(
                "bot.handlers.trade_history.edit_callback_message_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_edit_runtime,
            mock.patch(
                "bot.handlers.trade_history.answer_callback_query_via_runtime",
                new_callable=mock.AsyncMock,
            ) as mock_answer_runtime,
            mock.patch.object(
                aiogram_types.CallbackQuery,
                "answer",
                new_callable=mock.AsyncMock,
            ) as mock_direct_answer,
        ):
            result = await trade_history.router.propagate_event(
                "callback_query",
                event,
                state=mock_state,
                user=runtime_user,
                raw_state=None,
            )

        self.assertIsNone(result)

        # Requirement 2: session factory and load_bot_public_profile assertions
        mock_session_factory.assert_called_once_with()
        self.assertEqual(mock_session_factory.call_args.args, ())
        self.assertEqual(mock_session_factory.call_args.kwargs, {})

        self.assertEqual(session_context.enter_count, 1)
        self.assertEqual(session_context.exit_count, 1)
        session_context.aenter.assert_awaited_once_with()
        session_context.aexit.assert_awaited_once_with(None, None, None)

        mock_load_profile.assert_awaited_once_with(
            sentinel_session,
            viewer=runtime_user,
            target_user_id=target_user_id,
        )
        self.assertIs(mock_load_profile.await_args.args[0], sentinel_session)
        self.assertIs(mock_load_profile.await_args.kwargs["viewer"], runtime_user)
        self.assertEqual(
            mock_load_profile.await_args.kwargs["target_user_id"],
            target_user_id,
        )

        # Requirement 3: builder, edit, and single acknowledgment assertions
        mock_build_profile_text.assert_called_once_with(sentinel_profile)
        self.assertIs(mock_build_profile_text.call_args.args[0], sentinel_profile)

        mock_build_profile_keyboard.assert_called_once_with(sentinel_profile)
        self.assertIs(mock_build_profile_keyboard.call_args.args[0], sentinel_profile)

        mock_edit_runtime.assert_awaited_once_with(
            event,
            runtime_user,
            expected_profile_text,
            reply_markup=expected_profile_keyboard,
        )
        self.assertEqual(mock_edit_runtime.await_count, 1)
        self.assertIs(mock_edit_runtime.await_args.args[0], event)
        self.assertIs(mock_edit_runtime.await_args.args[1], runtime_user)
        self.assertEqual(mock_edit_runtime.await_args.args[2], expected_profile_text)
        self.assertEqual(
            mock_edit_runtime.await_args.kwargs,
            {"reply_markup": expected_profile_keyboard},
        )
        self.assertIs(
            mock_edit_runtime.await_args.kwargs["reply_markup"],
            expected_profile_keyboard,
        )

        mock_answer_runtime.assert_awaited_once_with(event)
        self.assertEqual(mock_answer_runtime.await_count, 1)
        self.assertEqual(mock_answer_runtime.await_args.args, (event,))
        self.assertIs(mock_answer_runtime.await_args.args[0], event)
        self.assertEqual(mock_answer_runtime.await_args.kwargs, {})

        mock_direct_answer.assert_not_called()

        mock_state.clear.assert_not_called()
        mock_state.update_data.assert_not_called()
        mock_state.set_state.assert_not_called()
        mock_state.set_data.assert_not_called()
        mock_state.get_data.assert_not_called()


if __name__ == "__main__":
    unittest.main()
