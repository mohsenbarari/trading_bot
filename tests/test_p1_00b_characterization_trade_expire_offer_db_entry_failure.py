"""Characterization test for handle_expire_offer database factory failure.

Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118
Bounded characterization suite under Stage P1-00b and Assignment WA-P1-00B-EVIDENCE-RECONCILE-20261001-064.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.trade_manage import handle_expire_offer


class FakeLease:
    """Minimal fake lease tracking release lifecycle and ordering."""

    def __init__(self, acquired: bool = True, events: list[str] | None = None):
        self.acquired = acquired
        self.events = events if events is not None else []
        self.release_calls = 0

        async def _release():
            self.release_calls += 1
            self.events.append("lease_release")

        self.release = AsyncMock(side_effect=_release)


def make_fake_callback(events: list[str] | None = None) -> SimpleNamespace:
    """Create a minimal CallbackQuery fake tracking UI interaction events."""
    ev = events if events is not None else []

    async def _answer(*args, **kwargs):
        ev.append("callback_answer")

    async def _edit_reply_markup(*args, **kwargs):
        ev.append("edit_reply_markup")

    async def _message_answer(*args, **kwargs):
        ev.append("callback_message_answer")

    answer_mock = AsyncMock(side_effect=_answer)
    edit_markup_mock = AsyncMock(side_effect=_edit_reply_markup)
    msg_answer_mock = AsyncMock(side_effect=_message_answer)

    return SimpleNamespace(
        id="cb-trade-expire-test",
        answer=answer_mock,
        message=SimpleNamespace(
            edit_reply_markup=edit_markup_mock,
            answer=msg_answer_mock,
        ),
    )


class TradeExpireOfferDbEntryFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for handle_expire_offer on database factory failure.

    Seed: BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118
    Scenario: P1-00B-TRADE-EXPIRE-DB-ENTRY-FAILURE-V1
    """

    async def asyncSetUp(self):
        self.trading_settings = SimpleNamespace(
            offer_expire_rate_per_minute=5,
            offer_expire_daily_limit_after_threshold=99,
        )

        patcher_ts = patch(
            "bot.handlers.trade_manage.get_trading_settings",
            return_value=self.trading_settings,
        )
        self.mock_get_trading_settings = patcher_ts.start()
        self.addCleanup(patcher_ts.stop)

        patcher_runtime = patch(
            "bot.handlers.trade_manage.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="legacy"),
        )
        self.mock_delivery_runtime = patcher_runtime.start()
        self.addCleanup(patcher_runtime.stop)

        self.mock_apply_offer_channel_state = AsyncMock()
        patcher_channel = patch(
            "bot.handlers.trade_manage.apply_offer_channel_state",
            new=self.mock_apply_offer_channel_state,
        )
        patcher_channel.start()
        self.addCleanup(patcher_channel.stop)

        self.mock_forward = AsyncMock()
        patcher_forward = patch(
            "bot.handlers.trade_manage.forward_offer_expiry_to_home_server",
            new=self.mock_forward,
        )
        patcher_forward.start()
        self.addCleanup(patcher_forward.stop)

        self.mock_build_keyboard = AsyncMock(return_value="NAV_KEYBOARD")
        patcher_nav = patch(
            "bot.handlers.trade_manage.build_persistent_navigation_keyboard",
            new=self.mock_build_keyboard,
        )
        patcher_nav.start()
        self.addCleanup(patcher_nav.stop)

        self.mock_acquire_gate = AsyncMock()
        patcher_gate = patch(
            "bot.handlers.trade_manage.try_acquire_offer_expiry_gate",
            new=self.mock_acquire_gate,
        )
        patcher_gate.start()
        self.addCleanup(patcher_gate.stop)

    async def test_db_factory_failure_propagates_and_releases_lease_without_delivery(self):
        """Test database factory failure raises RuntimeError and releases lease without delivery.

        Characterizes scenario where gate grants lease for offer_id=5, but AsyncSessionLocal
        factory raises a distinct RuntimeError before entering a database context.
        Verifies:
        - gate acquisition with offer_id=5
        - factory called exactly once before any DB context
        - identical RuntimeError propagates out of handler
        - lease.release awaited exactly once in finally block
        - no callback answer, forwarding, channel update, markup edit, or reply occurs
        """
        events: list[str] = []
        lease = FakeLease(acquired=True, events=events)

        async def _acquire_gate(offer_id: int | str):
            events.append(f"acquire_gate:{offer_id}")
            return lease

        self.mock_acquire_gate.side_effect = _acquire_gate

        distinct_error = RuntimeError("simulated AsyncSessionLocal factory failure before db entry")

        def _fail_session_factory(*args, **kwargs):
            events.append("db_factory_call")
            raise distinct_error

        mock_session_factory = MagicMock(side_effect=_fail_session_factory)
        with patch("bot.handlers.trade_manage.AsyncSessionLocal", new=mock_session_factory):
            callback = make_fake_callback(events=events)
            user = SimpleNamespace(id=7)
            callback_data = SimpleNamespace(offer_id=5)
            bot = SimpleNamespace()

            with self.assertRaises(RuntimeError) as ctx:
                await handle_expire_offer(
                    callback,
                    callback_data,
                    user=user,
                    bot=bot,
                )

            self.assertIs(ctx.exception, distinct_error)

        # Assert gate acquisition with offer_id=5
        self.mock_acquire_gate.assert_awaited_once_with(offer_id=5)

        # Assert database factory was called exactly once
        mock_session_factory.assert_called_once_with()
        self.assertEqual(mock_session_factory.call_count, 1)

        # Assert lease.release was awaited exactly once
        self.assertEqual(lease.release_calls, 1)
        lease.release.assert_awaited_once_with()

        # Assert no callback acknowledgement, forward, channel update, markup edit, or success reply occurs
        callback.answer.assert_not_awaited()
        self.mock_forward.assert_not_awaited()
        self.mock_apply_offer_channel_state.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()
        callback.message.answer.assert_not_awaited()
        self.mock_build_keyboard.assert_not_awaited()

        # Assert execution order: gate acquire -> db factory call -> lease release
        self.assertEqual(
            events,
            ["acquire_gate:5", "db_factory_call", "lease_release"],
        )


if __name__ == "__main__":
    unittest.main()
