import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import handle_channel_trade
from core.enums import UserAccountStatus, UserRole
from models.offer import OfferStatus


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


class BotTradeExecuteOfferGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_channel_trade_handles_missing_inactive_and_own_offer(self):
        user = make_bot_user()
        common_patches = [
            patch("bot.handlers.trade_execute.check_user_limits", return_value=(True, None)),
            patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)),
        ]

        cases = [
            (None, ("این لفظ دیگر در دسترس نیست.",), {"show_alert": True}),
            (SimpleNamespace(status=OfferStatus.COMPLETED), ("این لفظ دیگر فعال نیست.",), {"show_alert": True}),
            (SimpleNamespace(status=OfferStatus.ACTIVE, user_id=5), (), {}),
        ]

        for offer, expected_args, expected_kwargs in cases:
            callback = make_callback()
            with common_patches[0], common_patches[1], patch(
                "bot.handlers.trade_execute.AsyncSessionLocal",
                return_value=FakeSessionContext(FakeSession(offer)),
            ):
                await handle_channel_trade(callback, SimpleNamespace(offer_id=7, amount=2), user=user, bot=SimpleNamespace())
            callback.answer.assert_awaited_once_with(*expected_args, **expected_kwargs)

    async def test_inactive_offer_requests_authoritative_channel_refresh(self):
        user = make_bot_user()
        offer = SimpleNamespace(status=OfferStatus.COMPLETED)
        callback = make_callback()

        with patch(
            "bot.handlers.trade_execute.check_user_limits",
            return_value=(True, None),
        ), patch(
            "bot.handlers.trade_execute.settings",
            SimpleNamespace(channel_id=-100),
        ), patch(
            "bot.handlers.trade_execute.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession(offer)),
        ), patch(
            "bot.handlers.trade_execute._queue_authoritative_channel_offer_refresh",
            new=AsyncMock(return_value=True),
        ) as refresh:
            await handle_channel_trade(
                callback,
                SimpleNamespace(offer_id=7, amount=2),
                user=user,
                bot=SimpleNamespace(),
            )

        refresh.assert_awaited_once()
        self.assertIs(refresh.await_args.args[1], offer)
        self.assertFalse(refresh.await_args.kwargs["invalid_active_action"])
        callback.answer.assert_awaited_once_with(
            "این لفظ دیگر فعال نیست.",
            show_alert=True,
        )


if __name__ == "__main__":
    unittest.main()
