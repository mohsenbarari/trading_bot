"""Direct-characterization tests for bot.handlers.panel.show_support_contact.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, change product behavior, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    USER_PANEL_SUPPORT_MESSAGE,
    show_support_contact,
)
from core.enums import UserRole  # noqa: E402


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


class CharacterizePanelShowSupportContactTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.show_support_contact."""

    async def test_show_support_contact_no_user_early_returns(self) -> None:
        message = SimpleNamespace(answer=AsyncMock())

        with (
            patch("bot.handlers.panel.AsyncSessionLocal") as mock_session_factory,
            patch(
                "bot.handlers.panel._can_view_support",
                new_callable=AsyncMock,
            ) as mock_can_view_support,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_incoming_message_via_runtime,
        ):
            result = await show_support_contact(message, user=None)

        self.assertIsNone(result)
        mock_session_factory.assert_not_called()
        mock_can_view_support.assert_not_called()
        mock_can_view_support.assert_not_awaited()
        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

    async def test_show_support_contact_policy_denied_early_returns(self) -> None:
        message = SimpleNamespace(
            message_id=202,
            chat=SimpleNamespace(id=6002),
            answer=AsyncMock(),
        )
        user = SimpleNamespace(
            id=77,
            role=UserRole.WATCH,
            account_name="denied_user",
        )
        session_sentinel = SimpleNamespace(session_name="tracking_session_denied")
        context = TrackingSessionContext(session_sentinel)
        mock_session_factory = Mock(return_value=context)

        with (
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
                mock_session_factory,
            ),
            patch(
                "bot.handlers.panel._can_view_support",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_can_view_support,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_incoming_message_via_runtime,
        ):
            result = await show_support_contact(message, user)

        self.assertIsNone(result)

        mock_session_factory.assert_called_once_with()
        self.assertEqual(context.enter_count, 1)
        self.assertEqual(context.exit_count, 1)
        self.assertEqual(context.exit_args, (None, None, None))

        mock_can_view_support.assert_awaited_once_with(session_sentinel, user)
        call_args, call_kwargs = mock_can_view_support.await_args
        self.assertEqual(len(call_args), 2)
        self.assertIs(call_args[0], session_sentinel)
        self.assertIs(call_args[1], user)
        self.assertEqual(call_kwargs, {})

        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

    async def test_show_support_contact_eligible_user_delivers_support_message(
        self,
    ) -> None:
        message = SimpleNamespace(
            message_id=303,
            chat=SimpleNamespace(id=7003),
            answer=AsyncMock(),
        )
        user = SimpleNamespace(
            id=88,
            role=UserRole.STANDARD,
            account_name="eligible_user",
        )
        session_sentinel = SimpleNamespace(session_name="tracking_session_eligible")

        event_recorder: list[str] = []
        context = TrackingSessionContext(
            session_sentinel,
            event_recorder=event_recorder,
        )
        mock_session_factory = Mock(return_value=context)

        async def fake_can_view_support(session: Any, u: Any) -> bool:
            event_recorder.append("policy_check")
            return True

        async def fake_answer_runtime(*args: Any, **kwargs: Any) -> None:
            event_recorder.append("runtime_delivery")

        mock_can_view_support = AsyncMock(side_effect=fake_can_view_support)
        mock_answer_incoming_message_via_runtime = AsyncMock(
            side_effect=fake_answer_runtime
        )

        with (
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
                mock_session_factory,
            ),
            patch(
                "bot.handlers.panel._can_view_support",
                mock_can_view_support,
            ),
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                mock_answer_incoming_message_via_runtime,
            ),
        ):
            result = await show_support_contact(message, user)

        self.assertIsNone(result)

        mock_session_factory.assert_called_once_with()
        self.assertEqual(context.enter_count, 1)
        self.assertEqual(context.exit_count, 1)
        self.assertEqual(context.exit_args, (None, None, None))

        mock_can_view_support.assert_awaited_once_with(session_sentinel, user)
        can_view_args, can_view_kwargs = mock_can_view_support.await_args
        self.assertEqual(len(can_view_args), 2)
        self.assertIs(can_view_args[0], session_sentinel)
        self.assertIs(can_view_args[1], user)
        self.assertEqual(can_view_kwargs, {})

        self.assertEqual(
            event_recorder,
            ["session_enter", "policy_check", "session_exit", "runtime_delivery"],
        )
        self.assertLess(
            event_recorder.index("session_exit"),
            event_recorder.index("runtime_delivery"),
        )

        expected_constant = (
            "جهت ارتباط با پشتیبانی به آیدی تلگرامی زیر پیام دهید:\n"
            "@coin_trade_owner"
        )
        self.assertEqual(USER_PANEL_SUPPORT_MESSAGE, expected_constant)

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            USER_PANEL_SUPPORT_MESSAGE,
            source_key="panel-support-contact",
        )

        delivery_args, delivery_kwargs = (
            mock_answer_incoming_message_via_runtime.await_args
        )
        self.assertEqual(len(delivery_args), 3)
        self.assertIs(delivery_args[0], message)
        self.assertIs(delivery_args[1], user)
        self.assertEqual(delivery_args[2], USER_PANEL_SUPPORT_MESSAGE)
        self.assertIs(delivery_args[2], USER_PANEL_SUPPORT_MESSAGE)

        self.assertEqual(set(delivery_kwargs.keys()), {"source_key"})
        self.assertEqual(delivery_kwargs["source_key"], "panel-support-contact")

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
