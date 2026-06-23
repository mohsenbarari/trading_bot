import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade
from core.enums import UserAccountStatus, UserRole
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


def make_callback():
    return SimpleNamespace(
        id="cb1",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=200), message_id=50, edit_reply_markup=AsyncMock()),
    )


def make_bot_user(**overrides):
    data = {
        "id": 5,
        "telegram_id": 555,
        "mobile_number": "0935",
        "account_name": "buyer",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
        "trading_restricted_until": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


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
        home_server=None,
        price=150000,
        commodity=SimpleNamespace(name="سکه"),
    )


class BotTradeExecuteInvalidAmountTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_handles_lot_suggestion_and_generic_invalid_amount(self):
        user = make_bot_user()
        base_patches = [
            patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)),
            patch("bot.handlers.trade_execute.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(make_offer()))),
            patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))),
            patch("bot.handlers.trade_execute.is_remote_home", return_value=False),
        ]

        callback = make_callback()
        payload = {"offer_id": 7, "requested_amount": 4, "message": "MSG", "available_lots": [2, 3]}
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], patch(
            "bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(False, "این لات دیگر موجود نیست.", 4, [2, 3])
        ), patch("bot.handlers.trade_execute.build_lot_unavailable_suggestion_payload", return_value=payload), patch(
            "bot.handlers.trade_execute.send_or_update_trade_suggestion_message", new=AsyncMock()
        ) as suggestion_mock:
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=4), user=user, bot=SimpleNamespace())
        suggestion_mock.assert_awaited_once()
        callback.answer.assert_awaited_with("پیشنهاد جدید برای شما ارسال شد.", show_alert=False)

        callback = make_callback()
        with base_patches[0], base_patches[1], base_patches[2], base_patches[3], patch(
            "bot.handlers.trade_execute.validate_offer_trade_amount", return_value=(False, "مقدار نامعتبر", 4, [])
        ):
            await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=4), user=user, bot=SimpleNamespace())
        self.assertIn("مقدار نامعتبر", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})


if __name__ == "__main__":
    unittest.main()
