import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.orm.exc import StaleDataError

from bot.handlers.trade_create import handle_trade_confirm


class FakeSession:
    def __init__(self, scalar_values=None, get_map=None, commit_side_effect=None):
        self.scalar_values = list(scalar_values or [])
        self.get_map = dict(get_map or {})
        self.added = []
        self.commits = 0
        self.commit_side_effect = list(commit_side_effect or [])
        self.rollbacks = 0
        self.is_active = True

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 99
        self.added.append(value)

    async def commit(self):
        self.commits += 1
        if self.commit_side_effect:
            error = self.commit_side_effect.pop(0)
            if error is not None:
                self.is_active = False
                raise error

    async def rollback(self):
        self.rollbacks += 1
        self.is_active = True

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

    async def test_fake_session_helpers_cover_existing_ids_and_update_get_fallbacks(self):
        session = FakeSession()
        already_identified = SimpleNamespace(id=701)
        session.add(already_identified)
        self.assertEqual(already_identified.id, 701)
        self.assertIsNone(await session.commit())
        self.assertIsNone(await session.refresh(already_identified))
        self.assertIsNone(await session.get(type("Other", (), {}), 8))

        context = FakeSessionContext(session)
        self.assertIs(await context.__aenter__(), session)
        self.assertFalse(await context.__aexit__(None, None, None))

        create_session = FakeSession()
        created_offer = SimpleNamespace(id=None)
        create_session.add(created_offer)

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User" and key == 1:
                return SimpleNamespace(id=key)
            return None

        self.assertIs(await update_get(type("Offer", (), {}), 1), created_offer)
        self.assertEqual((await update_get(type("User", (), {}), 1)).id, 1)
        self.assertIsNone(await update_get(type("Other", (), {}), 9))

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
        user = SimpleNamespace(id=1, limitations_expire_at=None, home_server="iran")
        db_user = SimpleNamespace(id=1)
        create_session = FakeSession()
        update_session = FakeSession(get_map={(None, 0): None})
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=777), SimpleNamespace(message_id=778)]))

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User" and key == 1:
                return db_user
            return None

        update_session.get = update_get
        self.assertIsNone(await update_session.get(type("Other", (), {}), 7))

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            message_id = await send_offer_to_channel(offer, publish_user)
            offer.channel_message_id = message_id
            return SimpleNamespace(message_id=message_id, error_code=None)

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
        ) as increment_mock, patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch("bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")):
            await handle_trade_confirm(callback, state, user=user, bot=bot)

        self.assertEqual(len(create_session.added), 1)
        offer = create_session.added[0]
        self.assertEqual(offer.id, 99)
        self.assertTrue(offer.offer_public_id.startswith("ofr_"))
        self.assertEqual(offer.notes, "فقط نقدی")
        self.assertEqual(offer.home_server, "foreign")
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

    async def test_handle_trade_confirm_retries_stale_publication_without_duplicate_send_or_expiry(self):
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
                    "commodity_name": "سکه",
                    "price": 123456,
                    "commodity_id": 7,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
            clear=AsyncMock(),
        )
        user = SimpleNamespace(id=1, limitations_expire_at=None, home_server="iran")
        db_user = SimpleNamespace(id=1)
        create_session = FakeSession()
        publish_session = FakeSession(commit_side_effect=[StaleDataError("stale offer version"), None])
        bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=[SimpleNamespace(message_id=777), SimpleNamespace(message_id=778)]
            )
        )

        async def publish_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User" and key == 1:
                return db_user
            return None

        publish_session.get = publish_get
        publish_calls = 0

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            nonlocal publish_calls
            publish_calls += 1
            if publish_calls == 1:
                message_id = await send_offer_to_channel(offer, publish_user)
                offer.channel_message_id = message_id
            return SimpleNamespace(message_id=offer.channel_message_id, error_code=None)

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(publish_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.increment_user_counter", new=AsyncMock()
        ) as increment_mock, patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch("bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")):
            await handle_trade_confirm(callback, state, user=user, bot=bot)

        self.assertEqual(publish_calls, 2)
        self.assertEqual(publish_session.rollbacks, 1)
        self.assertEqual(publish_session.commits, 2)
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(create_session.added[0].status.value, "active")
        self.assertEqual(create_session.added[0].channel_message_id, 777)
        increment_mock.assert_awaited_once_with(publish_session, db_user, "channel_message")
        self.assertIn("با موفقیت در کانال ارسال شد", callback.message.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
