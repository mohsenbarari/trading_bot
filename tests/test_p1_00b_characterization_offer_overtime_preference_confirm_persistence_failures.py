"""Direct handler characterization of persistence rejection branches for confirm_offer_overtime_preference.

Seed: BOT-OFFER-CALLBACK-QUERY-OFFER-OVERTIME-PREFERENCE-CONFIRM-OFFER-OVERTIME-PREFERENCE-L191
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Assignment: WA-P1-00B-FREEZE-20260904-001
Stage: P1-00b

This module directly exercises bot.handlers.offer_overtime_preference.confirm_offer_overtime_preference
for exactly three adjacent persistence-phase rejection outcomes after valid pending-state normalization:
  (A) AsyncSession.get(User, user.id) returns None;
  (B) AsyncSession.get returns db_user, but save_overtime_preference_from_bot raises
      OfferOvertimePreferenceNotAllowedError with a distinct source-independent message;
  (C) AsyncSession.get returns db_user, but save_overtime_preference_from_bot raises
      OfferOvertimePreferenceError with a distinct source-independent message.

Bounded scope: this test file provides partial seed evidence under P1-00b and does NOT
claim to close canonical blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, the
scenario inventory, or Stage P1-00b.
"""
from __future__ import annotations

# Install canonical network fence before any application imports
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from typing import Any, Optional
import unittest
from unittest import mock

from aiogram import types as aiogram_types

from bot.handlers.offer_overtime_preference import confirm_offer_overtime_preference
from core.offer_overtime_bot_copy import M_NOT_AVAILABLE_MESSAGE
from core.services.offer_overtime_preference_service import (
    OfferOvertimePreferenceError,
    OfferOvertimePreferenceNotAllowedError,
)
from models.user import User, UserRole


class KeyAwareUserSession:
    """Session adapter accepting strictly (User, expected_user_id) for await session.get(...)."""

    def __init__(
        self,
        expected_user_id: int,
        return_user: Optional[User],
        order_tracker: list[str],
    ) -> None:
        self._expected_user_id = expected_user_id
        self._return_user = return_user
        self._order_tracker = order_tracker
        self.get_call_count = 0

    async def get(self, model: Any, ident: Any) -> Optional[User]:
        self.get_call_count += 1
        if self.get_call_count > 1:
            raise AssertionError("KeyAwareUserSession received more than one get call")
        if model is not User:
            raise AssertionError(f"Expected User model lookup, got {model!r}")
        if ident != self._expected_user_id:
            raise AssertionError(
                f"Expected user lookup with id={self._expected_user_id}, got ident={ident!r}"
            )
        self._order_tracker.append("session.get")
        return self._return_user


class KeyAwareUserSessionContext:
    """Async context manager proving entry/exit lifecycle for AsyncSessionLocal."""

    def __init__(
        self,
        session: KeyAwareUserSession,
        order_tracker: list[str],
    ) -> None:
        self._session = session
        self._order_tracker = order_tracker
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> KeyAwareUserSession:
        if self.enter_count > 0:
            raise AssertionError("Session context already entered")
        self.enter_count += 1
        self._order_tracker.append("session.enter")
        return self._session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self.enter_count != 1 or self.exit_count > 0:
            raise AssertionError("Invalid session context exit lifecycle")
        self.exit_count += 1
        self._order_tracker.append("session.exit")
        return False


class FakeFSMContext:
    """In-memory FSMContext fixture tracking data access and state clearing."""

    def __init__(self, data: dict[str, Any], order_tracker: list[str]) -> None:
        self._data = dict(data)
        self._order_tracker = order_tracker
        self.clear_called = 0

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def clear(self) -> None:
        self.clear_called += 1
        self._data.clear()
        self._order_tracker.append("state.clear")


class TestOfferOvertimePreferenceConfirmPersistenceFailures(unittest.IsolatedAsyncioTestCase):
    """Characterization suite for confirm_offer_overtime_preference persistence rejections."""

    def _create_user_fixture(self, user_id: int = 42) -> User:
        """Construct a valid in-memory User fixture satisfying model constraints."""
        return User(
            id=user_id,
            account_name=f"test_user_{user_id}",
            mobile_number=f"+98912000{user_id:04d}",
            normalized_account_name=f"test_user_{user_id}",
            normalized_mobile_number=f"0912000{user_id:04d}",
            full_name=f"Test User {user_id}",
            address="Tehran, Iran",
            role=UserRole.STANDARD,
            offer_overtime_minutes=0,
        )

    async def test_key_aware_session_contract_rejects_unexpected_model_or_identity(self) -> None:
        """Ensure the test session contract fails closed on any non-(User, expected_id) lookup."""
        order_tracker: list[str] = []
        user = self._create_user_fixture(user_id=101)

        # 1. Unexpected model is rejected
        session_wrong_model = KeyAwareUserSession(
            expected_user_id=101,
            return_user=user,
            order_tracker=order_tracker,
        )
        with self.assertRaises(AssertionError) as ctx_model:
            await session_wrong_model.get(str, 101)
        self.assertIn("Expected User model lookup", str(ctx_model.exception))

        # 2. Unexpected identity is rejected
        session_wrong_id = KeyAwareUserSession(
            expected_user_id=101,
            return_user=user,
            order_tracker=order_tracker,
        )
        with self.assertRaises(AssertionError) as ctx_id:
            await session_wrong_id.get(User, 999)
        self.assertIn("Expected user lookup with id=101", str(ctx_id.exception))

        # 3. Exact match succeeds
        session_valid = KeyAwareUserSession(
            expected_user_id=101,
            return_user=user,
            order_tracker=order_tracker,
        )
        retrieved = await session_valid.get(User, 101)
        self.assertIs(retrieved, user)
        self.assertEqual(session_valid.get_call_count, 1)

        # 4. Repeated call is rejected
        with self.assertRaises(AssertionError) as ctx_repeat:
            await session_valid.get(User, 101)
        self.assertIn("more than one get call", str(ctx_repeat.exception))

    async def test_confirm_persistence_missing_db_user_clears_state_and_alerts_m_not_available(self) -> None:
        """Branch A: session.get(User, caller.id) returns None.

        Expectations:
          - session.get is called once with (User, caller.id) and returns None.
          - save_overtime_preference_from_bot is NOT called.
          - state.clear() is awaited.
          - answer_callback_query_via_runtime is awaited once with (callback, M_NOT_AVAILABLE_MESSAGE, show_alert=True).
          - Caller user's offer_overtime_minutes remains unchanged.
          - No callback-message delivery, user panel markup, or success workflow executes.
          - Context manager enters once and exits cleanly.
        """
        order_tracker: list[str] = []
        caller_user = self._create_user_fixture(user_id=42)
        caller_user.offer_overtime_minutes = 0

        session = KeyAwareUserSession(
            expected_user_id=caller_user.id,
            return_user=None,
            order_tracker=order_tracker,
        )
        session_ctx = KeyAwareUserSessionContext(session, order_tracker)
        state = FakeFSMContext(
            {"overtime_preference_pending_minutes": 5},
            order_tracker,
        )
        callback = mock.MagicMock(spec=aiogram_types.CallbackQuery)
        callback.id = "cb_missing_user_42"

        mock_answer_query = mock.AsyncMock()

        async def _record_answer_query(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        mock_answer_query.side_effect = _record_answer_query

        mock_save = mock.AsyncMock()
        mock_answer_message = mock.AsyncMock()
        mock_panel = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=mock.AsyncMock(return_value=(True, None)),
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            return_value=session_ctx,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new=mock_save,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new=mock_answer_query,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new=mock_answer_message,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new=mock_panel,
        ):
            await confirm_offer_overtime_preference(
                callback=callback,
                state=state,  # type: ignore[arg-type]
                user=caller_user,
            )

        # Observable execution order: inside context, get is awaited, state cleared, query answered, then context exits
        expected_order = [
            "session.enter",
            "session.get",
            "state.clear",
            "answer_callback_query_via_runtime",
            "session.exit",
        ]
        self.assertEqual(order_tracker, expected_order)

        # Context lifecycle and session get contract
        self.assertEqual(session_ctx.enter_count, 1)
        self.assertEqual(session_ctx.exit_count, 1)
        self.assertEqual(session.get_call_count, 1)

        # Save must not execute
        mock_save.assert_not_called()

        # State must be cleared
        self.assertEqual(state.clear_called, 1)
        self.assertEqual(await state.get_data(), {})

        # Exact answer_callback_query_via_runtime call shape
        mock_answer_query.assert_awaited_once_with(
            callback,
            M_NOT_AVAILABLE_MESSAGE,
            show_alert=True,
        )

        # Caller preference unchanged
        self.assertEqual(caller_user.offer_overtime_minutes, 0)

        # Downstream success messaging and panel navigation must remain uninvoked
        mock_answer_message.assert_not_called()
        mock_panel.assert_not_called()

    async def test_confirm_persistence_not_allowed_error_clears_state_and_alerts_distinct_message(self) -> None:
        """Branch B: save_overtime_preference_from_bot raises OfferOvertimePreferenceNotAllowedError.

        Expectations:
          - session.get returns db_user fixture.
          - save_overtime_preference_from_bot is awaited once with (session, db_user, 5)
            and raises OfferOvertimePreferenceNotAllowedError with a distinct message.
          - Context manager exits on exception.
          - state.clear() is awaited in the exception handler.
          - answer_callback_query_via_runtime is awaited once with (callback, distinct_message, show_alert=True).
          - Caller and DB user preferences remain unchanged.
          - Success delivery and panel navigation must not execute.
        """
        order_tracker: list[str] = []
        caller_user = self._create_user_fixture(user_id=77)
        caller_user.offer_overtime_minutes = 0
        db_user = self._create_user_fixture(user_id=77)
        db_user.offer_overtime_minutes = 0

        distinct_not_allowed_msg = "TEST_PERMISSION_DENIED_REASON_OVERTIME_77"

        session = KeyAwareUserSession(
            expected_user_id=caller_user.id,
            return_user=db_user,
            order_tracker=order_tracker,
        )
        session_ctx = KeyAwareUserSessionContext(session, order_tracker)
        state = FakeFSMContext(
            {"overtime_preference_pending_minutes": 5},
            order_tracker,
        )
        callback = mock.MagicMock(spec=aiogram_types.CallbackQuery)
        callback.id = "cb_not_allowed_77"

        mock_save = mock.AsyncMock()

        async def _raise_not_allowed(db_arg: Any, user_arg: Any, minutes_arg: Any) -> None:
            order_tracker.append("save_overtime_preference_from_bot")
            raise OfferOvertimePreferenceNotAllowedError(distinct_not_allowed_msg)

        mock_save.side_effect = _raise_not_allowed

        mock_answer_query = mock.AsyncMock()

        async def _record_answer_query(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        mock_answer_query.side_effect = _record_answer_query

        mock_answer_message = mock.AsyncMock()
        mock_panel = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=mock.AsyncMock(return_value=(True, None)),
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            return_value=session_ctx,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new=mock_save,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new=mock_answer_query,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new=mock_answer_message,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new=mock_panel,
        ):
            await confirm_offer_overtime_preference(
                callback=callback,
                state=state,  # type: ignore[arg-type]
                user=caller_user,
            )

        # Observable order: inside context, get is awaited, save is awaited and raises,
        # context exits, then except block clears state and emits answer_callback_query_via_runtime.
        expected_order = [
            "session.enter",
            "session.get",
            "save_overtime_preference_from_bot",
            "session.exit",
            "state.clear",
            "answer_callback_query_via_runtime",
        ]
        self.assertEqual(order_tracker, expected_order)

        # Context lifecycle and session get contract
        self.assertEqual(session_ctx.enter_count, 1)
        self.assertEqual(session_ctx.exit_count, 1)
        self.assertEqual(session.get_call_count, 1)

        # Save call contract: exact identity and arguments
        mock_save.assert_awaited_once_with(session, db_user, 5)
        self.assertIs(mock_save.await_args[0][0], session)
        self.assertIs(mock_save.await_args[0][1], db_user)

        # State must be cleared
        self.assertEqual(state.clear_called, 1)
        self.assertEqual(await state.get_data(), {})

        # Exact answer_callback_query_via_runtime call shape with the distinct message
        mock_answer_query.assert_awaited_once_with(
            callback,
            distinct_not_allowed_msg,
            show_alert=True,
        )

        # Caller and DB user preferences unchanged
        self.assertEqual(caller_user.offer_overtime_minutes, 0)
        self.assertEqual(db_user.offer_overtime_minutes, 0)

        # Downstream success delivery must not execute
        mock_answer_message.assert_not_called()
        mock_panel.assert_not_called()

    async def test_confirm_persistence_preference_error_preserves_state_and_alerts_distinct_message(self) -> None:
        """Branch C: save_overtime_preference_from_bot raises OfferOvertimePreferenceError.

        Expectations:
          - session.get returns db_user fixture.
          - save_overtime_preference_from_bot is awaited once with (session, db_user, 5)
            and raises OfferOvertimePreferenceError with a distinct message.
          - Context manager exits on exception.
          - state.clear() is NOT called (pending state is preserved).
          - answer_callback_query_via_runtime is awaited once with (callback, distinct_message, show_alert=True).
          - Caller and DB user preferences remain unchanged.
          - Success delivery and panel navigation must not execute.
        """
        order_tracker: list[str] = []
        caller_user = self._create_user_fixture(user_id=88)
        caller_user.offer_overtime_minutes = 0
        db_user = self._create_user_fixture(user_id=88)
        db_user.offer_overtime_minutes = 0

        distinct_pref_error_msg = "TEST_INVALID_VALUE_REASON_OVERTIME_88"

        session = KeyAwareUserSession(
            expected_user_id=caller_user.id,
            return_user=db_user,
            order_tracker=order_tracker,
        )
        session_ctx = KeyAwareUserSessionContext(session, order_tracker)
        state = FakeFSMContext(
            {"overtime_preference_pending_minutes": 5},
            order_tracker,
        )
        callback = mock.MagicMock(spec=aiogram_types.CallbackQuery)
        callback.id = "cb_pref_error_88"

        mock_save = mock.AsyncMock()

        async def _raise_pref_error(db_arg: Any, user_arg: Any, minutes_arg: Any) -> None:
            order_tracker.append("save_overtime_preference_from_bot")
            raise OfferOvertimePreferenceError(distinct_pref_error_msg)

        mock_save.side_effect = _raise_pref_error

        mock_answer_query = mock.AsyncMock()

        async def _record_answer_query(*args: Any, **kwargs: Any) -> None:
            order_tracker.append("answer_callback_query_via_runtime")

        mock_answer_query.side_effect = _record_answer_query

        mock_answer_message = mock.AsyncMock()
        mock_panel = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new=mock.AsyncMock(return_value=(True, None)),
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            return_value=session_ctx,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new=mock_save,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new=mock_answer_query,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new=mock_answer_message,
        ), mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new=mock_panel,
        ):
            await confirm_offer_overtime_preference(
                callback=callback,
                state=state,  # type: ignore[arg-type]
                user=caller_user,
            )

        # Observable order: inside context, get is awaited, save is awaited and raises,
        # context exits, then except block emits answer_callback_query_via_runtime (no state.clear!).
        expected_order = [
            "session.enter",
            "session.get",
            "save_overtime_preference_from_bot",
            "session.exit",
            "answer_callback_query_via_runtime",
        ]
        self.assertEqual(order_tracker, expected_order)

        # Context lifecycle and session get contract
        self.assertEqual(session_ctx.enter_count, 1)
        self.assertEqual(session_ctx.exit_count, 1)
        self.assertEqual(session.get_call_count, 1)

        # Save call contract: exact identity and arguments
        mock_save.assert_awaited_once_with(session, db_user, 5)
        self.assertIs(mock_save.await_args[0][0], session)
        self.assertIs(mock_save.await_args[0][1], db_user)

        # State must NOT be cleared for OfferOvertimePreferenceError
        self.assertEqual(state.clear_called, 0)
        self.assertEqual(
            await state.get_data(),
            {"overtime_preference_pending_minutes": 5},
        )

        # Exact answer_callback_query_via_runtime call shape with the distinct message
        mock_answer_query.assert_awaited_once_with(
            callback,
            distinct_pref_error_msg,
            show_alert=True,
        )

        # Caller and DB user preferences unchanged
        self.assertEqual(caller_user.offer_overtime_minutes, 0)
        self.assertEqual(db_user.offer_overtime_minutes, 0)

        # Downstream success delivery must not execute
        mock_answer_message.assert_not_called()
        mock_panel.assert_not_called()
