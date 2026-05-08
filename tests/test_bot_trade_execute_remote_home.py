import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade
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
    )


class BotTradeExecuteRemoteHomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_remote_home_handles_pending_suggestion_success_and_error(self):
        user = SimpleNamespace(id=5, telegram_id=555, trading_restricted_until=None)
        bot = SimpleNamespace()
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
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(409, payload))), patch(
            "bot.handlers.trade_execute.send_or_update_trade_suggestion_message", new=AsyncMock()
        ) as suggestion_mock, patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        suggestion_mock.assert_awaited_once()
        callback.answer.assert_awaited_with("پیشنهاد جدید برای شما ارسال شد.", show_alert=False)

        callback = make_callback()
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], base_patches[4], patch(
            "bot.handlers.trade_execute.check_double_click", new=AsyncMock(return_value=True)
        ), patch("bot.handlers.trade_execute.forward_trade_to_home_server", new=AsyncMock(return_value=(200, {"ok": True}))), patch(
            "bot.handlers.trade_execute.remove_trade_suggestion_record", new=AsyncMock()
        ) as remove_mock, patch("bot.handlers.trade_execute.current_server", return_value="foreign"):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=bot)
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        remove_mock.assert_awaited_once()
        callback.answer.assert_awaited_with("معامله ثبت شد ✅", show_alert=False)

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