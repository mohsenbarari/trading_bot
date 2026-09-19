"""Hermetic characterization of publisher-channel public trade callback adapter.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact Seed ID: BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-PUBLIC-TRADE-CALLBACK-L46
Scenario: P1-00B-SINGLE-V1.publisher-channel-public-trade-callback-adapter

This test module provides bounded handler-level characterization for the single
unindexed seed BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-PUBLIC-TRADE-CALLBACK-L46.
It captures existing runtime delegation without mutating runtime code or asserting Stage completion.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from bot.callbacks import ChannelTradePublicCallback
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


class PublisherChannelPublicTradeCallbackCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_trade_callback_forwards_public_identity_and_contention_flags(self) -> None:
        handler = _extract_nested_handler("public_trade_callback")
        callback = SimpleNamespace(
            id="cbq-public-trade-1",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=-100123456789),
                message_id=1001,
            ),
        )
        callback_data = ChannelTradePublicCallback(
            offer_public_id="ofr_pub_char_101",
            amount=25,
        )
        user = SimpleNamespace(id=55, username="public_buyer")
        bot = SimpleNamespace(id="bot-instance-1")

        fence = install_p1_00_network_fence()
        initial_redis_count = len(fence["live_redis_constructs"])
        initial_socket_count = len(fence["denied_connects"])

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
        self.assertEqual(len(fence["live_redis_constructs"]), initial_redis_count)
        self.assertEqual(len(fence["denied_connects"]), initial_socket_count)

    async def test_public_trade_callback_forwards_default_contention_flags(self) -> None:
        handler = _extract_nested_handler("public_trade_callback")
        callback = SimpleNamespace(
            id="cbq-public-trade-2",
            message=SimpleNamespace(
                chat=SimpleNamespace(id=-100123456789),
                message_id=1002,
            ),
        )
        callback_data = ChannelTradePublicCallback(
            offer_public_id="ofr_pub_char_102",
            amount=50,
        )
        user = SimpleNamespace(id=56, username="public_buyer_2")
        bot = SimpleNamespace(id="bot-instance-2")

        fence = install_p1_00_network_fence()
        initial_redis_count = len(fence["live_redis_constructs"])
        initial_socket_count = len(fence["denied_connects"])

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
        self.assertEqual(len(fence["live_redis_constructs"]), initial_redis_count)
        self.assertEqual(len(fence["denied_connects"]), initial_socket_count)


if __name__ == "__main__":
    unittest.main()
