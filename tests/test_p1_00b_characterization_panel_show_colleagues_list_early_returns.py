"""Direct-characterization tests for bot.handlers.panel.show_colleagues_list early returns.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-MESSAGE-PANEL-SHOW-COLLEAGUES-LIST-L470
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This characterization does not update the registry, reduce blockers, close the seed,
change product behavior, or close the Stage.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import show_colleagues_list  # noqa: E402
from core.enums import UserAccountStatus, UserRole  # noqa: E402
from core.services.user_account_status_service import (  # noqa: E402
    is_user_global_web_locked,
)


class TrackingSessionContext:
    """Tracking async context manager for AsyncSessionLocal in characterization tests."""

    def __init__(
        self,
        session_instance: Any,
        event_recorder: list[str] | None = None,
    ) -> None:
        self.session_instance = session_instance
        self.event_recorder = event_recorder
        self.enter_count = 0
        self.exit_count = 0
        self.exit_args: tuple[Any, ...] | None = None

    async def __aenter__(self) -> Any:
        self.enter_count += 1
        if self.event_recorder is not None:
            self.event_recorder.append("session_enter")
        return self.session_instance

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.exit_count += 1
        self.exit_args = (exc_type, exc, tb)
        if self.event_recorder is not None:
            self.event_recorder.append("session_exit")
        return False


class CharacterizePanelShowColleaguesListEarlyReturnsTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing early-return branches of show_colleagues_list."""

    async def test_show_colleagues_list_no_user_early_returns(self) -> None:
        message = SimpleNamespace(
            message_id=101,
            chat=SimpleNamespace(id=5001),
            answer=AsyncMock(),
        )
        state = Mock()

        with (
            patch(
                "bot.handlers.panel.is_user_global_web_locked",
            ) as mock_is_user_global_web_locked,
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
            ) as mock_session_factory,
            patch(
                "bot.handlers.panel._can_view_colleagues_list",
                new_callable=AsyncMock,
            ) as mock_can_view_colleagues_list,
            patch(
                "bot.handlers.panel._load_colleagues_for_user",
                new_callable=AsyncMock,
            ) as mock_load_colleagues_for_user,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_incoming_message_via_runtime,
        ):
            result = await show_colleagues_list(message, state, None)

        self.assertIsNone(result)
        mock_is_user_global_web_locked.assert_not_called()
        mock_session_factory.assert_not_called()
        mock_can_view_colleagues_list.assert_not_called()
        mock_can_view_colleagues_list.assert_not_awaited()
        mock_load_colleagues_for_user.assert_not_called()
        mock_load_colleagues_for_user.assert_not_awaited()
        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()
        self.assertEqual(state.mock_calls, [])

    async def test_show_colleagues_list_globally_locked_user_early_returns(
        self,
    ) -> None:
        message = SimpleNamespace(
            message_id=202,
            chat=SimpleNamespace(id=5002),
            answer=AsyncMock(),
        )
        state = Mock()
        user = SimpleNamespace(
            id=102,
            account_name="locked_user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            messenger_grace_expires_at=None,
        )

        event_recorder: list[str] = []

        def fake_is_user_global_web_locked(u: Any) -> bool:
            event_recorder.append("lock_check")
            return is_user_global_web_locked(u)

        async def fake_answer_runtime(*args: Any, **kwargs: Any) -> None:
            event_recorder.append("runtime_delivery")

        mock_is_user_global_web_locked = Mock(
            side_effect=fake_is_user_global_web_locked
        )
        mock_answer_incoming_message_via_runtime = AsyncMock(
            side_effect=fake_answer_runtime
        )

        with (
            patch(
                "bot.handlers.panel.is_user_global_web_locked",
                mock_is_user_global_web_locked,
            ),
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
            ) as mock_session_factory,
            patch(
                "bot.handlers.panel._can_view_colleagues_list",
                new_callable=AsyncMock,
            ) as mock_can_view_colleagues_list,
            patch(
                "bot.handlers.panel._load_colleagues_for_user",
                new_callable=AsyncMock,
            ) as mock_load_colleagues_for_user,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                mock_answer_incoming_message_via_runtime,
            ),
        ):
            result = await show_colleagues_list(message, state, user)

        self.assertIsNone(result)

        mock_is_user_global_web_locked.assert_called_once_with(user)
        lock_args, lock_kwargs = mock_is_user_global_web_locked.call_args
        self.assertEqual(len(lock_args), 1)
        self.assertIs(lock_args[0], user)
        self.assertEqual(lock_kwargs, {})

        self.assertEqual(event_recorder, ["lock_check", "runtime_delivery"])

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.",
            source_key="panel-colleagues-account-locked",
        )
        delivery_args, delivery_kwargs = (
            mock_answer_incoming_message_via_runtime.await_args
        )
        self.assertEqual(len(delivery_args), 3)
        self.assertIs(delivery_args[0], message)
        self.assertIs(delivery_args[1], user)
        self.assertEqual(
            delivery_args[2],
            "دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.",
        )
        self.assertEqual(set(delivery_kwargs.keys()), {"source_key"})
        self.assertEqual(
            delivery_kwargs["source_key"],
            "panel-colleagues-account-locked",
        )

        mock_session_factory.assert_not_called()
        mock_can_view_colleagues_list.assert_not_called()
        mock_can_view_colleagues_list.assert_not_awaited()
        mock_load_colleagues_for_user.assert_not_called()
        mock_load_colleagues_for_user.assert_not_awaited()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()
        self.assertEqual(state.mock_calls, [])

    async def test_show_colleagues_list_policy_denied_early_returns(self) -> None:
        message = SimpleNamespace(
            message_id=303,
            chat=SimpleNamespace(id=5003),
            answer=AsyncMock(),
        )
        state = Mock()
        user = SimpleNamespace(
            id=103,
            account_name="active_denied_user",
            role=UserRole.WATCH,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )

        session_sentinel = SimpleNamespace(session_name="tracking_session_colleagues")
        event_recorder: list[str] = []
        context = TrackingSessionContext(
            session_sentinel,
            event_recorder=event_recorder,
        )
        mock_session_factory = Mock(return_value=context)

        def fake_is_user_global_web_locked(u: Any) -> bool:
            event_recorder.append("lock_check")
            return is_user_global_web_locked(u)

        async def fake_can_view_colleagues_list(session: Any, u: Any) -> bool:
            event_recorder.append("policy_check")
            return False

        async def fake_answer_runtime(*args: Any, **kwargs: Any) -> None:
            event_recorder.append("runtime_delivery")

        mock_is_user_global_web_locked = Mock(
            side_effect=fake_is_user_global_web_locked
        )
        mock_can_view_colleagues_list = AsyncMock(
            side_effect=fake_can_view_colleagues_list
        )
        mock_answer_incoming_message_via_runtime = AsyncMock(
            side_effect=fake_answer_runtime
        )

        with (
            patch(
                "bot.handlers.panel.is_user_global_web_locked",
                mock_is_user_global_web_locked,
            ),
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
                mock_session_factory,
            ),
            patch(
                "bot.handlers.panel._can_view_colleagues_list",
                mock_can_view_colleagues_list,
            ),
            patch(
                "bot.handlers.panel._load_colleagues_for_user",
                new_callable=AsyncMock,
            ) as mock_load_colleagues_for_user,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                mock_answer_incoming_message_via_runtime,
            ),
        ):
            result = await show_colleagues_list(message, state, user)

        self.assertIsNone(result)

        mock_is_user_global_web_locked.assert_called_once_with(user)
        lock_args, lock_kwargs = mock_is_user_global_web_locked.call_args
        self.assertEqual(len(lock_args), 1)
        self.assertIs(lock_args[0], user)
        self.assertEqual(lock_kwargs, {})

        mock_session_factory.assert_called_once_with()
        self.assertEqual(context.enter_count, 1)
        self.assertEqual(context.exit_count, 1)
        self.assertEqual(context.exit_args, (None, None, None))

        mock_can_view_colleagues_list.assert_awaited_once_with(
            session_sentinel, user
        )
        policy_args, policy_kwargs = mock_can_view_colleagues_list.await_args
        self.assertEqual(len(policy_args), 2)
        self.assertIs(policy_args[0], session_sentinel)
        self.assertIs(policy_args[1], user)
        self.assertEqual(policy_kwargs, {})

        mock_load_colleagues_for_user.assert_not_called()
        mock_load_colleagues_for_user.assert_not_awaited()

        self.assertEqual(
            event_recorder,
            [
                "lock_check",
                "session_enter",
                "policy_check",
                "runtime_delivery",
                "session_exit",
            ],
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "این بخش فقط برای کاربران عادی فعال است.",
            source_key="panel-colleagues-not-allowed",
        )
        delivery_args, delivery_kwargs = (
            mock_answer_incoming_message_via_runtime.await_args
        )
        self.assertEqual(len(delivery_args), 3)
        self.assertIs(delivery_args[0], message)
        self.assertIs(delivery_args[1], user)
        self.assertEqual(
            delivery_args[2],
            "این بخش فقط برای کاربران عادی فعال است.",
        )
        self.assertEqual(set(delivery_kwargs.keys()), {"source_key"})
        self.assertEqual(
            delivery_kwargs["source_key"],
            "panel-colleagues-not-allowed",
        )

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()
        self.assertEqual(state.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
