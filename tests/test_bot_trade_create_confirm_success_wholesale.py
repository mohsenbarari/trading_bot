import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_trade_confirm


class FakeSession:
    def __init__(self, scalar_values=None, get_map=None):
        self.scalar_values = list(scalar_values or [])
        self.get_map = dict(get_map or {})
        self.added = []
        self.commits = 0

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 99
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        return None

    async def get(self, model, key):
        return self.get_map.get((model, key))


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateConfirmSuccessWholesaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_confirm_publishes_wholesale_offer_and_updates_channel_message(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 12,
                    "trade_type": "buy",
                    "trade_type_fa": "🟢 خرید",
                    "commodity_name": "سکه",
                    "price": 123456,
                    "commodity_id": 7,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": "فقط نقدی",
                }
            ),
            clear=AsyncMock(),
        )
        user = SimpleNamespace(id=1, limitations_expire_at=None)
        created_offer = None
        db_user = SimpleNamespace(id=1)
        create_session = FakeSession()
        update_session = FakeSession(get_map={(None, 0): None})
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=777), SimpleNamespace(message_id=778)]))

        def get_map_for_update():
            nonlocal created_offer
            if create_session.added:
                created_offer = create_session.added[0]
            return {
                (type(created_offer), 99): created_offer,
            }

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User" and key == 1:
                return db_user
            return None

        update_session.get = update_get

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(update_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.increment_user_counter", new=AsyncMock()
        ) as increment_mock, patch("bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")):
            await handle_trade_confirm(callback, state, user=user, bot=bot)

        self.assertEqual(len(create_session.added), 1)
        offer = create_session.added[0]
        self.assertEqual(offer.id, 99)
        self.assertEqual(offer.notes, "فقط نقدی")
        self.assertEqual(offer.channel_message_id, 777)
        first_call = bot.send_message.await_args_list[0]
        self.assertEqual(first_call.kwargs["chat_id"], -100)
        self.assertEqual(first_call.kwargs["reply_markup"].inline_keyboard[0][0].text, "12 عدد")
        second_call = bot.send_message.await_args_list[1]
        self.assertEqual(second_call.kwargs["chat_id"], 555)
        increment_mock.assert_awaited_once_with(update_session, db_user, "channel_message")
        self.assertIn("با موفقیت در کانال ارسال شد", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()