import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.middlewares import trade_contention_gate as middleware


class FakeCallbackQuery:
    def __init__(
        self,
        *,
        data: str,
        chat_id: int = -100123,
        chat_type: str = "channel",
        telegram_id: int = 9001,
    ):
        self.data = data
        self.from_user = SimpleNamespace(id=telegram_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type))
        self.answer = AsyncMock()


class FakeLease:
    def __init__(self, *, acquired: bool):
        self.acquired = acquired
        self.release = AsyncMock()


class TradeContentionGateMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_public_and_legacy_trade_callbacks(self):
        public = middleware.parse_telegram_trade_callback_data("ct2:ofr_public_1:20")
        legacy = middleware.parse_telegram_trade_callback_data("channel_trade:42:5")

        self.assertEqual(public.offer_public_id, "ofr_public_1")
        self.assertIsNone(public.offer_id)
        self.assertEqual(public.amount, 20)
        self.assertEqual(legacy.offer_id, 42)
        self.assertIsNone(legacy.offer_public_id)
        self.assertEqual(legacy.amount, 5)
        self.assertIsNone(middleware.parse_telegram_trade_callback_data("ct2:ofr_public_1:0"))
        self.assertIsNone(middleware.parse_telegram_trade_callback_data("noop:1:2"))

    async def test_first_channel_tap_confirms_before_auth_handler(self):
        callback = FakeCallbackQuery(data="ct2:ofr_public_1:20")
        handler = AsyncMock()
        gate = middleware.TradeContentionGateMiddleware()

        with patch.object(middleware, "CallbackQuery", FakeCallbackQuery), patch.object(
            middleware.settings, "channel_id", -100123
        ), patch.object(
            middleware,
            "claim_telegram_trade_confirmation",
            new=AsyncMock(return_value=False),
        ) as claim_mock, patch.object(
            middleware,
            "try_acquire_trade_contention_gate",
            new=AsyncMock(),
        ) as acquire_mock:
            result = await gate(handler, callback, {})

        self.assertIsNone(result)
        handler.assert_not_awaited()
        acquire_mock.assert_not_awaited()
        claim_mock.assert_awaited_once()
        callback.answer.assert_awaited_once_with(middleware.TELEGRAM_TRADE_CONFIRM_MESSAGE, show_alert=False)

    async def test_busy_channel_offer_rejects_before_auth_handler(self):
        callback = FakeCallbackQuery(data="ct2:ofr_public_1:20")
        handler = AsyncMock()
        gate = middleware.TradeContentionGateMiddleware()
        lease = FakeLease(acquired=False)

        with patch.object(middleware, "CallbackQuery", FakeCallbackQuery), patch.object(
            middleware.settings, "channel_id", -100123
        ), patch.object(
            middleware,
            "claim_telegram_trade_confirmation",
            new=AsyncMock(return_value=True),
        ), patch.object(
            middleware,
            "try_acquire_trade_contention_gate",
            new=AsyncMock(return_value=lease),
        ) as acquire_mock:
            result = await gate(handler, callback, {})

        self.assertIsNone(result)
        handler.assert_not_awaited()
        acquire_mock.assert_awaited_once()
        lease.release.assert_not_awaited()
        callback.answer.assert_awaited_once_with(middleware.TELEGRAM_TRADE_BUSY_MESSAGE, show_alert=False)

    async def test_confirmed_channel_tap_sets_preconfirmed_and_releases_gate(self):
        callback = FakeCallbackQuery(data="channel_trade:42:5")
        handler = AsyncMock(return_value="handled")
        gate = middleware.TradeContentionGateMiddleware()
        lease = FakeLease(acquired=True)
        data = {}

        with patch.object(middleware, "CallbackQuery", FakeCallbackQuery), patch.object(
            middleware.settings, "channel_id", -100123
        ), patch.object(
            middleware,
            "claim_telegram_trade_confirmation",
            new=AsyncMock(return_value=True),
        ), patch.object(
            middleware,
            "try_acquire_trade_contention_gate",
            new=AsyncMock(return_value=lease),
        ) as acquire_mock:
            result = await gate(handler, callback, data)

        self.assertEqual(result, "handled")
        self.assertTrue(data["trade_contention_preconfirmed"])
        handler.assert_awaited_once_with(callback, data)
        acquire_mock.assert_awaited_once()
        lease.release.assert_awaited_once()
        callback.answer.assert_not_awaited()

    async def test_private_suggestion_callbacks_bypass_pre_auth_gate(self):
        callback = FakeCallbackQuery(data="ct2:ofr_public_1:20", chat_id=555, chat_type="private")
        handler = AsyncMock(return_value="private")
        gate = middleware.TradeContentionGateMiddleware()
        data = {}

        with patch.object(middleware, "CallbackQuery", FakeCallbackQuery), patch.object(
            middleware.settings, "channel_id", -100123
        ), patch.object(
            middleware,
            "claim_telegram_trade_confirmation",
            new=AsyncMock(),
        ) as claim_mock, patch.object(
            middleware,
            "try_acquire_trade_contention_gate",
            new=AsyncMock(),
        ) as acquire_mock:
            result = await gate(handler, callback, data)

        self.assertEqual(result, "private")
        self.assertNotIn("trade_contention_preconfirmed", data)
        handler.assert_awaited_once_with(callback, data)
        claim_mock.assert_not_awaited()
        acquire_mock.assert_not_awaited()

    async def test_missing_channel_id_bypasses_pre_auth_gate(self):
        callback = FakeCallbackQuery(data="ct2:ofr_public_1:20")
        handler = AsyncMock(return_value="no-channel")
        gate = middleware.TradeContentionGateMiddleware()
        data = {}

        with patch.object(middleware, "CallbackQuery", FakeCallbackQuery), patch.object(
            middleware.settings, "channel_id", None
        ), patch.object(
            middleware,
            "claim_telegram_trade_confirmation",
            new=AsyncMock(),
        ) as claim_mock, patch.object(
            middleware,
            "try_acquire_trade_contention_gate",
            new=AsyncMock(),
        ) as acquire_mock:
            result = await gate(handler, callback, data)

        self.assertEqual(result, "no-channel")
        self.assertNotIn("trade_contention_preconfirmed", data)
        handler.assert_awaited_once_with(callback, data)
        claim_mock.assert_not_awaited()
        acquire_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
