"""Hermetic characterization of publisher-channel callback adapters.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact Seed IDs:
  - BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-TRADE-CALLBACK-L31
  - BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-EXPIRY-CALLBACK-L61

This test file provides bounded handler-level characterization for the two named seeds
only and does not reconcile docs/refactor/two-server/section-1/inventory/recent-characterization.json,
reduce canonical inventory counts by itself, or close Stage P1-00b.
The sibling public_trade_callback is characterized in
tests/test_p1_00_characterization_six_gaps.py and is excluded here.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from bot.callbacks import ChannelTradeCallback, ExpireOfferCallback
from bot.handlers.telegram_publisher_channel_callbacks import (
    build_publisher_channel_callback_router,
)


def _extract_nested_handler(handler_name: str) -> Callable[..., Any]:
    router = build_publisher_channel_callback_router()
    matched = [
        observer.callback
        for observer in router.callback_query.handlers
        if getattr(observer.callback, "__name__", "") == handler_name
    ]
    if not matched:
        raise AssertionError(
            f"Required nested handler {handler_name!r} was not registered on publisher router"
        )
    if len(matched) > 1:
        raise AssertionError(
            f"Required nested handler {handler_name!r} was duplicated on publisher router: {len(matched)} matches found"
        )
    return matched[0]


class PublisherChannelCallbackAdapterCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_trade_callback_forwards_default_contention_flags(self) -> None:
        handler = _extract_nested_handler("trade_callback")
        callback = SimpleNamespace(
            id="cbq-trade-legacy-1",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=-100123456789),
                message_id=987,
            ),
        )
        callback_data = ChannelTradeCallback(offer_id=314, amount=25)
        user = SimpleNamespace(id=42, username="buyer_user")
        bot = SimpleNamespace(id="bot-sentinel-1")

        with patch(
            "bot.handlers.telegram_publisher_channel_callbacks._handle_channel_trade",
            new=AsyncMock(),
        ) as trade_mock, patch(
            "bot.handlers.telegram_publisher_channel_callbacks.handle_expire_offer",
            new=AsyncMock(),
        ) as expiry_mock:
            await handler(callback, callback_data, user, bot)

        trade_mock.assert_awaited_once_with(
            callback,
            callback_data,
            user,
            bot,
            trade_contention_preconfirmed=False,
            trade_contention_pre_gated=False,
        )
        self.assertIs(trade_mock.await_args.args[0], callback)
        self.assertIs(trade_mock.await_args.args[1], callback_data)
        self.assertIs(trade_mock.await_args.args[2], user)
        self.assertIs(trade_mock.await_args.args[3], bot)
        self.assertEqual(
            trade_mock.await_args.kwargs,
            {
                "trade_contention_preconfirmed": False,
                "trade_contention_pre_gated": False,
            },
        )
        expiry_mock.assert_not_called()

    async def test_trade_callback_forwards_explicit_contention_flags(self) -> None:
        handler = _extract_nested_handler("trade_callback")
        callback = SimpleNamespace(
            id="cbq-trade-legacy-2",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=-100123456789),
                message_id=988,
            ),
        )
        callback_data = ChannelTradeCallback(offer_id=315, amount=50)
        user = SimpleNamespace(id=43, username="buyer_user_2")
        bot = SimpleNamespace(id="bot-sentinel-2")

        with patch(
            "bot.handlers.telegram_publisher_channel_callbacks._handle_channel_trade",
            new=AsyncMock(),
        ) as trade_mock, patch(
            "bot.handlers.telegram_publisher_channel_callbacks.handle_expire_offer",
            new=AsyncMock(),
        ) as expiry_mock:
            await handler(
                callback,
                callback_data,
                user,
                bot,
                trade_contention_preconfirmed=True,
                trade_contention_pre_gated=True,
            )

        trade_mock.assert_awaited_once_with(
            callback,
            callback_data,
            user,
            bot,
            trade_contention_preconfirmed=True,
            trade_contention_pre_gated=True,
        )
        self.assertIs(trade_mock.await_args.args[0], callback)
        self.assertIs(trade_mock.await_args.args[1], callback_data)
        self.assertIs(trade_mock.await_args.args[2], user)
        self.assertIs(trade_mock.await_args.args[3], bot)
        self.assertEqual(
            trade_mock.await_args.kwargs,
            {
                "trade_contention_preconfirmed": True,
                "trade_contention_pre_gated": True,
            },
        )
        expiry_mock.assert_not_called()

    async def test_expiry_callback_delegates_exact_arguments(self) -> None:
        handler = _extract_nested_handler("expiry_callback")
        callback = SimpleNamespace(
            id="cbq-expire-offer-1",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=-100123456789),
                message_id=989,
            ),
        )
        callback_data = ExpireOfferCallback(offer_id=777)
        user = SimpleNamespace(id=88, username="offer_owner")
        bot = SimpleNamespace(id="bot-sentinel-3")

        with patch(
            "bot.handlers.telegram_publisher_channel_callbacks.handle_expire_offer",
            new=AsyncMock(),
        ) as expiry_mock, patch(
            "bot.handlers.telegram_publisher_channel_callbacks._handle_channel_trade",
            new=AsyncMock(),
        ) as trade_mock:
            await handler(callback, callback_data, user, bot)

        expiry_mock.assert_awaited_once_with(callback, callback_data, user, bot)
        self.assertIs(expiry_mock.await_args.args[0], callback)
        self.assertIs(expiry_mock.await_args.args[1], callback_data)
        self.assertIs(expiry_mock.await_args.args[2], user)
        self.assertIs(expiry_mock.await_args.args[3], bot)
        self.assertEqual(expiry_mock.await_args.kwargs, {})
        trade_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
