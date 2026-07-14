import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import TextOfferActionCallback
from bot.handlers.trade_create import Trade, handle_text_offer, handle_text_offer_confirm, handle_text_offer_warning_confirm
from core.enums import UserRole


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 181
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


class FakeState:
    def __init__(self):
        self.data = {}
        self.current_state = None
        self.update_data = AsyncMock(side_effect=self._update_data)
        self.get_data = AsyncMock(side_effect=self._get_data)
        self.get_state = AsyncMock(side_effect=self._get_state)
        self.set_state = AsyncMock(side_effect=self._set_state)
        self.clear = AsyncMock(side_effect=self._clear)

    async def _update_data(self, **kwargs):
        self.data.update(kwargs)

    async def _get_data(self):
        return dict(self.data)

    async def _get_state(self):
        return self.current_state

    async def _set_state(self, value):
        self.current_state = value

    async def _clear(self):
        self.data.clear()
        self.current_state = None


class BotTradeCreateTextOfferWarningFlowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.market_patcher = patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True))
        self.market_patcher.start()
        self.addCleanup(self.market_patcher.stop)
        self.admission_patcher = patch(
            "core.services.offer_creation_service.acquire_market_offer_admission_fence",
            new=AsyncMock(),
        )
        self.admission_patcher.start()
        self.addCleanup(self.admission_patcher.stop)

    async def test_fake_session_helpers_cover_existing_ids_and_empty_get(self):
        session = FakeSession()
        already_identified = SimpleNamespace(id=703)
        session.add(already_identified)
        self.assertEqual(already_identified.id, 703)
        self.assertIsNone(await session.commit())
        self.assertIsNone(await session.refresh(already_identified))
        offered = SimpleNamespace(id=None)
        session.add(offered)
        self.assertEqual(offered.id, 181)
        self.assertIs(await session.get(type("Offer", (), {}), 1), already_identified)
        self.assertEqual((await session.get(type("User", (), {}), 6)).id, 6)
        self.assertIsNone(await session.get(type("Other", (), {}), 1))

        context = FakeSessionContext(session)
        self.assertIs(await context.__aenter__(), session)
        self.assertFalse(await context.__aexit__(None, None, None))

        create_session = FakeSession()
        created_offer = SimpleNamespace(id=None)
        create_session.add(created_offer)

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User":
                return SimpleNamespace(id=key)
            return None

        self.assertIs(await update_get(type("Offer", (), {}), 1), created_offer)
        self.assertEqual((await update_get(type("User", (), {}), 8)).id, 8)
        self.assertIsNone(await update_get(type("Other", (), {}), 1))

    def make_warning_payload(self):
        return {
            "error_code": "OFFER_PRICE_WARNING",
            "warning_type": "sell_below_lowest_active",
            "title": "هشدار قیمت فروش",
            "detail": "قیمت فروش شما از پایین\u200cترین فروش فعال مشابه پایین\u200cتر است.",
            "message": "⚠️ هشدار قیمت فروش\n\nقیمت شما مشکوک است.",
            "reference_label": "پایین\u200cترین قیمت فروش فعال",
            "reference_price": 100000,
            "proposed_price": 99900,
            "difference_percent": 0.1,
        }

    async def test_text_offer_warning_flow_runs_from_message_parse_to_second_confirmation(self):
        user = SimpleNamespace(id=1, role=UserRole.STANDARD, trading_restricted_until=None, limitations_expire_at=None)
        state = FakeState()
        parsed_offer = SimpleNamespace(
            trade_type="sell",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=99900,
            is_wholesale=True,
            lot_sizes=None,
            notes="فوری",
        )
        incoming_message = SimpleNamespace(text="ف سکه 12تا 99900: فوری", answer=AsyncMock())
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
            data=TextOfferActionCallback(action="confirm").pack(),
        )
        warning_confirm_callback = SimpleNamespace(
            message=callback.message,
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
            data=TextOfferActionCallback(action="confirm_warning").pack(),
        )
        create_session = FakeSession()
        update_session = FakeSession()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=910), SimpleNamespace(message_id=911)]))

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User":
                return SimpleNamespace(id=key)
            return None

        update_session.get = update_get
        self.assertIsNone(await update_session.get(type("Other", (), {}), 5))

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            message_id = await send_offer_to_channel(offer, publish_user)
            offer.channel_message_id = message_id
            return SimpleNamespace(message_id=message_id, error_code=None)

        with patch("bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed_offer, None))), patch(
            "core.trading_settings.get_trading_settings",
            return_value=SimpleNamespace(max_active_offers=3),
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(update_session),
            ],
        ), patch(
            "core.utils.check_user_limits",
            side_effect=[(True, None), (True, None), (True, None), (True, None)],
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=self.make_warning_payload()),
        ), patch(
            "core.utils.increment_user_counter",
            new=AsyncMock(),
        ) as increment_mock, patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100, bot_username="botname"),
        ):
            await handle_text_offer(incoming_message, state, user=user, bot=bot)

            self.assertEqual(state.current_state, Trade.awaiting_text_confirm)
            self.assertEqual(state.data["price"], 99900)
            preview_text = incoming_message.answer.await_args.args[0]
            self.assertIn("پیش\u200cنمایش لفظ", preview_text)
            preview_keyboard = incoming_message.answer.await_args.kwargs["reply_markup"]
            self.assertEqual(
                preview_keyboard.inline_keyboard[0][0].callback_data,
                TextOfferActionCallback(action="confirm").pack(),
            )

            await handle_text_offer_confirm(callback, state, user=user, bot=bot)

            warning_text = callback.message.edit_text.await_args.args[0]
            self.assertIn("هشدار قیمت فروش", warning_text)
            warning_keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
            self.assertEqual(
                warning_keyboard.inline_keyboard[0][0].callback_data,
                TextOfferActionCallback(action="confirm_warning").pack(),
            )
            self.assertEqual(state.current_state, Trade.awaiting_text_confirm)

            await handle_text_offer_warning_confirm(warning_confirm_callback, state, user=user, bot=bot)

        self.assertEqual(create_session.added[0].channel_message_id, 910)
        self.assertTrue(create_session.added[0].exclude_from_competitive_price)
        self.assertEqual(create_session.added[0].price_warning_type, "sell_below_lowest_active")
        self.assertIn("منتشر شد", callback.message.edit_text.await_args_list[-1].args[0])
        self.assertIsNone(state.current_state)
        self.assertEqual(state.data, {})
        increment_mock.assert_awaited_once()
        self.assertEqual(bot.send_message.await_count, 2)
        warning_confirm_callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
