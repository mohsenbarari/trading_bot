import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import _notify_remote_trade_success, handle_channel_trade
from models.offer import OfferStatus, OfferType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, offer):
        self.offer = offer

    async def execute(self, stmt):
        return FakeExecuteResult(self.offer)

    async def refresh(self, offer, attrs):
        return None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback(chat_id=200):
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=50, edit_reply_markup=AsyncMock()),
    )


def make_offer():
    return SimpleNamespace(
        id=7,
        status=OfferStatus.ACTIVE,
        user_id=9,
        offer_type=OfferType.BUY,
        quantity=5,
        remaining_quantity=5,
        is_wholesale=False,
        lot_sizes=[2, 3],
        home_server="iran",
        offer_public_id="ofr_remote_7",
        notes="تحویل فوری",
    )


class BotTradeExecuteRemoteHomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_remote_trade_success_prefers_callback_chat_over_stored_telegram_id(self):
        user = SimpleNamespace(id=5, telegram_id=555)
        offer = make_offer()
        bot = SimpleNamespace(send_message=AsyncMock())

        await _notify_remote_trade_success(
            bot,
            user,
            offer,
            2,
            {"trade_number": 10029, "trade_type": "sell", "commodity_name": "سکه"},
            fallback_chat_id=300,
            idempotency_key="telegram_callback:test",
        )

        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], 300)

    async def test_notify_remote_trade_success_uses_callback_chat_fallback(self):
        user = SimpleNamespace(id=5, telegram_id=None)
        offer = make_offer()
        bot = SimpleNamespace(send_message=AsyncMock())

        await _notify_remote_trade_success(
            bot,
            user,
            offer,
            2,
            {"trade_number": 10030, "trade_type": "sell", "commodity_name": "سکه"},
            fallback_chat_id=300,
            idempotency_key="telegram_callback:test",
        )

        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], 300)
        self.assertIn("🔴 فروش", bot.send_message.await_args.kwargs["text"])

    async def test_handle_channel_trade_remote_home_handles_pending_suggestion_success_and_error(self):
        user = SimpleNamespace(id=5, telegram_id=555, trading_restricted_until=None)
        bot = SimpleNamespace(send_message=AsyncMock())
        base_patches = [
            patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)),
            patch("bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(make_offer()))),
            patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))),
            patch("bot.handlers.trade_execute.is_remote_home", return_value=True),
            patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)),
        ]

        callback = make_callback()
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=False)
        ), patch("bot.handlers.trade_execute.get_available_trade_amounts", return_value=[2, 3]), patch(
            "bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"
        ), patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()) as upsert_mock, patch(
            "bot.handlers.trade_execute.schedule_trade_suggestion_cleanup"
        ) as cleanup_mock, patch("bot.handlers.trade_execute.schedule_trade_suggestion_pending_reset") as pending_reset_mock:
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup="KB")
        upsert_mock.assert_awaited_once()
        cleanup_mock.assert_called_once()
        pending_reset_mock.assert_called_once()
        callback.answer.assert_awaited_with("برای تایید دوباره روی همان دکمه بزنید ☑️", show_alert=False)

        callback = make_callback()
        payload = {"error_code": "TRADE_LOT_UNAVAILABLE", "offer_id": 7, "requested_amount": 2, "message": "MSG", "available_lots": [3]}
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(409, payload))) as forward_mock, patch(
            "bot.handlers.trade_execute.send_or_update_trade_suggestion_message", new=AsyncMock()
        ) as suggestion_mock, patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        suggestion_mock.assert_awaited_once()
        forwarded_payload = forward_mock.await_args.args[1]
        self.assertEqual(forwarded_payload["offer_public_id"], "ofr_remote_7")
        expected_idempotency_key = "telegram_callback:5:ofr_remote_7:2:remaining:5:50"
        self.assertEqual(forwarded_payload["idempotency_key"], expected_idempotency_key)
        callback.answer.assert_awaited_with("پیشنهاد جدید برای شما ارسال شد.", show_alert=False)

        callback = make_callback()
        success_payload = {
            "trade_number": 10020,
            "trade_type": "sell",
            "commodity_name": "سکه",
            "quantity": 2,
            "price": 123000,
            "counterparty_name": "مالک لفظ",
            "created_at": "1404/01/01 12:00",
            "offer_notes": "تسویه نقدی",
        }
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(200, success_payload))), patch(
            "bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()
        ) as remove_mock, patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        remove_mock.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        success_text = bot.send_message.await_args.kwargs["text"]
        self.assertNotIn("معامله ثبت شد", success_text)
        self.assertIn("🔴 فروش", success_text)
        self.assertIn("💰 فی: 123,000", success_text)
        self.assertIn("📦 تعداد: 2", success_text)
        self.assertIn("🏷️ کالا: سکه", success_text)
        self.assertIn("👤 طرف معامله: مالک لفظ", success_text)
        self.assertIn("🔢 شماره معامله: 10020", success_text)
        self.assertIn("🕐 زمان معامله: 1404/01/01 12:00", success_text)
        self.assertIn("📝 توضیحات: تسویه نقدی", success_text)
        callback.answer.assert_awaited_with("معامله ثبت شد ✅", show_alert=False)

        bot.send_message.reset_mock()
        callback = make_callback()
        recovered_payload = {
            "trade_number": 10021,
            "trade_type": "buy",
            "commodity_name": "سکه",
            "quantity": 2,
            "price": 124000,
            "counterparty_name": "مالک لفظ",
            "offer_notes": "تحویل امروز",
        }
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(504, {"detail": "timeout"}))), patch(
            "bot.handlers.trade_execute._wait_for_forwarded_trade_completion", new=AsyncMock(return_value=recovered_payload)
        ) as recovery_mock, patch(
            "bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()
        ) as remove_mock, patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        recovery_mock.assert_awaited_once_with(expected_idempotency_key)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        remove_mock.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        recovered_text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("🔢 شماره معامله: 10021", recovered_text)
        self.assertIn("📝 توضیحات: تحویل امروز", recovered_text)
        callback.answer.assert_awaited_with("معامله ثبت شد ✅", show_alert=False)

        bot.send_message.reset_mock()
        callback = make_callback()
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(504, {"detail": "timeout"}))), patch(
            "bot.handlers.trade_execute._wait_for_forwarded_trade_completion", new=AsyncMock(return_value=None)
        ), patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        bot.send_message.assert_not_awaited()
        self.assertIn("درخواست معامله ارسال شد", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})

        callback = make_callback()
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(400, {"detail": "خطا"}))), patch(
            "bot.handlers.trade_execute.current_server", return_value="foreign"
        ):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        self.assertIn("خطا", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})


if __name__ == "__main__":
    unittest.main()
