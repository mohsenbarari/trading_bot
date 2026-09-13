"""Hermetic characterization tests for build_publisher_b2b_router.receive_dispatch.

Seed: BOT-TG-MESSAGE-TELEGRAM-PUBLISHER-B2B-RECEIVE-DISPATCH-L27
Stage: Stage P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Bounded behavior: Directly execute the registered message callback
build_publisher_b2b_router.receive_dispatch across valid dispatches,
sender int-subclass edge rejection (bool sender ID), service error
propagation, context manager lifecycle, and observable ordering.

Characterize current behavior only; this module does not close seed
BOT-TG-MESSAGE-TELEGRAM-PUBLISHER-B2B-RECEIVE-DISPATCH-L27 or Stage P1-00b.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.telegram_publisher_b2b import build_publisher_b2b_router
from core.services.telegram_publisher_dispatch_service import (
    TelegramPublisherInboundDispatchResult,
)


def _build_session_local_mock() -> tuple[MagicMock, MagicMock, AsyncMock]:
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_session_local = MagicMock(return_value=mock_session_ctx)
    return mock_session_local, mock_session_ctx, mock_session


class TestTelegramPublisherB2BReceiveDispatchCharacterization(unittest.IsolatedAsyncioTestCase):
    """Hermetic behavioral tests directly executing receive_dispatch."""

    def setUp(self) -> None:
        self._denied_connects_before = len(_FENCE["denied_connects"])
        self._live_redis_before = len(_FENCE["live_redis_constructs"])
        self.identity = "pub-alpha"
        self.expected_primary_bot_id = 999111
        self.router = build_publisher_b2b_router(
            identity=self.identity,
            expected_primary_bot_id=self.expected_primary_bot_id,
        )
        self.callback = self.router.message.handlers[0].callback

    def tearDown(self) -> None:
        self.assertEqual(len(_FENCE["denied_connects"]), self._denied_connects_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), self._live_redis_before)

    def test_router_construction_registers_receive_dispatch_callback(self) -> None:
        """Requirement 1: Verify the real router registers receive_dispatch handler callback."""
        denied_before = len(_FENCE["denied_connects"])
        redis_before = len(_FENCE["live_redis_constructs"])

        self.assertEqual(self.router.name, f"publisher-b2b:{self.identity}")
        self.assertEqual(len(self.router.message.handlers), 1)
        self.assertIs(self.callback, self.router.message.handlers[0].callback)

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before)

    async def test_valid_dispatch_success_contract_and_observable_ordering(self) -> None:
        """Requirement 2: Valid dispatch awaits service, commits session, and answers ACK in order."""
        denied_before = len(_FENCE["denied_connects"])
        redis_before = len(_FENCE["live_redis_constructs"])

        mock_session_local, mock_session_ctx, mock_session = _build_session_local_mock()

        fixed_server = "srv-publisher-alpha"
        fixed_now = datetime(2026, 9, 13, 11, 20, 0, tzinfo=timezone.utc)
        fixed_sender_id = 12345678
        fixed_dispatch_text = "tbq1|dispatch|80e08f66-4164-4d45-b3b9-53e64df6d3f7|31|2026-08-11T12:00:00Z"
        fixed_ack_text = "tbq1|ack|80e08f66-4164-4d45-b3b9-53e64df6d3f7|31|2026-08-11T12:00:00Z"

        dispatch_result = TelegramPublisherInboundDispatchResult(
            publisher_bot_identity=self.identity,
            command_id="80e08f66-4164-4d45-b3b9-53e64df6d3f7",
            duplicate=False,
            acknowledgement_text=fixed_ack_text,
        )

        call_order: list[str] = []

        async def fake_accept_dispatch(*args: Any, **kwargs: Any) -> Any:
            call_order.append("service")
            return dispatch_result

        async def fake_commit() -> None:
            call_order.append("session commit")

        async def fake_answer(*args: Any, **kwargs: Any) -> Any:
            call_order.append("message.answer")

        mock_accept_dispatch = AsyncMock(side_effect=fake_accept_dispatch)
        mock_session.commit.side_effect = fake_commit
        mock_current_server = MagicMock(return_value=fixed_server)
        mock_utc_now = MagicMock(return_value=fixed_now)

        sender = MagicMock()
        sender.id = fixed_sender_id
        mock_message = MagicMock()
        mock_message.from_user = sender
        mock_message.text = fixed_dispatch_text
        mock_message.answer = AsyncMock(side_effect=fake_answer)

        with patch("bot.handlers.telegram_publisher_b2b.AsyncSessionLocal", mock_session_local), \
             patch("bot.handlers.telegram_publisher_b2b.current_server", mock_current_server), \
             patch("bot.handlers.telegram_publisher_b2b.utc_now", mock_utc_now), \
             patch("bot.handlers.telegram_publisher_b2b.accept_publisher_b2b_dispatch", mock_accept_dispatch):

            await self.callback(mock_message)

            mock_session_local.assert_called_once_with()
            mock_session_ctx.__aenter__.assert_awaited_once_with()

            # Service awaited exactly once with entered session as sole positional argument
            mock_accept_dispatch.assert_awaited_once_with(
                mock_session,
                current_server=fixed_server,
                publisher_bot_identity=self.identity,
                expected_primary_bot_id=self.expected_primary_bot_id,
                sender_bot_id=fixed_sender_id,
                text=fixed_dispatch_text,
                received_at=fixed_now,
            )
            self.assertEqual(len(mock_accept_dispatch.await_args.args), 1)
            self.assertIs(mock_accept_dispatch.await_args.args[0], mock_session)
            self.assertEqual(
                mock_accept_dispatch.await_args.kwargs,
                {
                    "current_server": fixed_server,
                    "publisher_bot_identity": self.identity,
                    "expected_primary_bot_id": self.expected_primary_bot_id,
                    "sender_bot_id": fixed_sender_id,
                    "text": fixed_dispatch_text,
                    "received_at": fixed_now,
                },
            )

            # Session committed exactly once with no extra arguments
            mock_session.commit.assert_awaited_once_with()
            self.assertEqual(mock_session.commit.await_args.args, ())
            self.assertEqual(mock_session.commit.await_args.kwargs, {})

            # Context exited cleanly
            mock_session_ctx.__aexit__.assert_awaited_once_with(None, None, None)

            # Message answer called with exactly acknowledgement_text and no extra arguments
            mock_message.answer.assert_awaited_once_with(fixed_ack_text)
            self.assertEqual(mock_message.answer.await_args.args, (fixed_ack_text,))
            self.assertEqual(mock_message.answer.await_args.kwargs, {})

            # Observable ordering: service, session commit, then message.answer
            self.assertEqual(call_order, ["service", "session commit", "message.answer"])

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before)

    async def test_invalid_sender_bool_rejected_without_service_or_commit_or_answer(self) -> None:
        """Requirement 3: bool sender ID rejected; session exits, no dispatch/commit/answer."""
        for invalid_sender_id in (True, False):
            with self.subTest(sender_id=invalid_sender_id):
                denied_before = len(_FENCE["denied_connects"])
                redis_before = len(_FENCE["live_redis_constructs"])

                mock_session_local, mock_session_ctx, mock_session = _build_session_local_mock()
                mock_accept_dispatch = AsyncMock()
                mock_current_server = MagicMock(return_value="srv-test-primary")
                mock_utc_now = MagicMock(return_value=datetime(2026, 9, 13, 11, 0, 0, tzinfo=timezone.utc))

                sender = MagicMock()
                sender.id = invalid_sender_id
                mock_message = MagicMock()
                mock_message.from_user = sender
                mock_message.text = "tbq1|dispatch|tenant|job-001"
                mock_message.answer = AsyncMock()

                with patch("bot.handlers.telegram_publisher_b2b.AsyncSessionLocal", mock_session_local), \
                     patch("bot.handlers.telegram_publisher_b2b.current_server", mock_current_server), \
                     patch("bot.handlers.telegram_publisher_b2b.utc_now", mock_utc_now), \
                     patch("bot.handlers.telegram_publisher_b2b.accept_publisher_b2b_dispatch", mock_accept_dispatch):

                    with self.assertRaises(ValueError) as err_ctx:
                        await self.callback(mock_message)

                    self.assertEqual(str(err_ctx.exception), "telegram_b2b_sender_missing")

                    mock_session_local.assert_called_once_with()
                    mock_session_ctx.__aenter__.assert_awaited_once_with()
                    mock_session_ctx.__aexit__.assert_awaited_once()
                    exit_args = mock_session_ctx.__aexit__.await_args.args
                    self.assertEqual(len(exit_args), 3)
                    self.assertIs(exit_args[0], ValueError)
                    self.assertIs(exit_args[1], err_ctx.exception)

                    mock_accept_dispatch.assert_not_called()
                    mock_accept_dispatch.assert_not_awaited()
                    mock_session.commit.assert_not_called()
                    mock_session.commit.assert_not_awaited()
                    mock_message.answer.assert_not_called()
                    mock_message.answer.assert_not_awaited()

                self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
                self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before)

    async def test_service_error_propagates_and_aborts_commit_and_answer(self) -> None:
        """Requirement 4: Runtime error in service propagates; session exits; commit and answer never awaited."""
        denied_before = len(_FENCE["denied_connects"])
        redis_before = len(_FENCE["live_redis_constructs"])

        mock_session_local, mock_session_ctx, mock_session = _build_session_local_mock()

        fixed_server = "srv-publisher-alpha"
        fixed_now = datetime(2026, 9, 13, 11, 25, 0, tzinfo=timezone.utc)
        fixed_sender_id = 98765432
        fixed_dispatch_text = "tbq1|dispatch|c290f1ee-6c54-4b01-90e6-d701748f0851|32|2026-08-11T12:05:00Z"
        fixed_error = RuntimeError("fixed-publisher-b2b-runtime-failure")

        mock_accept_dispatch = AsyncMock(side_effect=fixed_error)
        mock_current_server = MagicMock(return_value=fixed_server)
        mock_utc_now = MagicMock(return_value=fixed_now)

        sender = MagicMock()
        sender.id = fixed_sender_id
        mock_message = MagicMock()
        mock_message.from_user = sender
        mock_message.text = fixed_dispatch_text
        mock_message.answer = AsyncMock()

        with patch("bot.handlers.telegram_publisher_b2b.AsyncSessionLocal", mock_session_local), \
             patch("bot.handlers.telegram_publisher_b2b.current_server", mock_current_server), \
             patch("bot.handlers.telegram_publisher_b2b.utc_now", mock_utc_now), \
             patch("bot.handlers.telegram_publisher_b2b.accept_publisher_b2b_dispatch", mock_accept_dispatch):

            with self.assertRaises(RuntimeError) as err_ctx:
                await self.callback(mock_message)

            self.assertIs(err_ctx.exception, fixed_error)

            mock_session_local.assert_called_once_with()
            mock_session_ctx.__aenter__.assert_awaited_once_with()

            # Session context exits without swallowing the exception
            mock_session_ctx.__aexit__.assert_awaited_once()
            exit_args = mock_session_ctx.__aexit__.await_args.args
            self.assertEqual(len(exit_args), 3)
            self.assertIs(exit_args[0], RuntimeError)
            self.assertIs(exit_args[1], fixed_error)

            # Service call contract matches success case exactly
            mock_accept_dispatch.assert_awaited_once_with(
                mock_session,
                current_server=fixed_server,
                publisher_bot_identity=self.identity,
                expected_primary_bot_id=self.expected_primary_bot_id,
                sender_bot_id=fixed_sender_id,
                text=fixed_dispatch_text,
                received_at=fixed_now,
            )
            self.assertEqual(len(mock_accept_dispatch.await_args.args), 1)
            self.assertIs(mock_accept_dispatch.await_args.args[0], mock_session)
            self.assertEqual(
                mock_accept_dispatch.await_args.kwargs,
                {
                    "current_server": fixed_server,
                    "publisher_bot_identity": self.identity,
                    "expected_primary_bot_id": self.expected_primary_bot_id,
                    "sender_bot_id": fixed_sender_id,
                    "text": fixed_dispatch_text,
                    "received_at": fixed_now,
                },
            )

            # Commit and answer are never awaited
            mock_session.commit.assert_not_called()
            mock_session.commit.assert_not_awaited()
            mock_message.answer.assert_not_called()
            mock_message.answer.assert_not_awaited()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
