import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_confirm, handle_text_offer_warning_confirm


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 81
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        return None

    async def get(self, model, key):
        if self.added and model.__name__ == "Offer":
            return self.added[0]
        if model.__name__ == "User":
            return SimpleNamespace(id=key)
        return None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateTextOfferWarningConfirmTests(unittest.IsolatedAsyncioTestCase):
    def make_state(self):
        return SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 12,
                    "trade_type": "sell",
                    "commodity_id": 7,
                    "commodity_name": "سکه",
                    "price": 99900,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": "فوری",
                }
            ),
            clear=AsyncMock(),
        )

    def warning_payload(self):
        return {
            "error_code": "OFFER_PRICE_WARNING",
            "warning_type": "sell_below_lowest_active",
            "title": "هشدار قیمت فروش",
            "detail": "قیمت فروش شما از پایین\u200cترین فروش فعال مشابه پایین\u200cتر است.",
            "message": "warning message",
            "reference_label": "پایین\u200cترین قیمت فروش فعال",
            "reference_price": 100000,
            "proposed_price": 99900,
            "difference_percent": 0.1,
        }

    async def test_text_offer_confirm_shows_second_warning_confirmation_before_publish(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = self.make_state()

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[FakeSessionContext(FakeSession([0])), FakeSessionContext(FakeSession())],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=self.warning_payload())
        ), patch("bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")):
            await handle_text_offer_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=SimpleNamespace())

        self.assertIn("warning message", callback.message.edit_text.await_args.args[0])
        keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
        self.assertIn("confirm_warning", keyboard.inline_keyboard[0][0].callback_data)
        state.clear.assert_not_awaited()

    async def test_text_offer_warning_confirm_publishes_and_flags_offer(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = self.make_state()
        create_session = FakeSession()
        update_session = FakeSession()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=910), SimpleNamespace(message_id=911)]))

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(update_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=self.warning_payload())
        ), patch("core.utils.increment_user_counter", new=AsyncMock()), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_text_offer_warning_confirm(
                callback,
                state,
                user=SimpleNamespace(id=1, limitations_expire_at=None),
                bot=bot,
            )

        self.assertTrue(create_session.added[0].exclude_from_competitive_price)
        self.assertEqual(create_session.added[0].price_warning_type, "sell_below_lowest_active")
        self.assertIn("منتشر شد", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()