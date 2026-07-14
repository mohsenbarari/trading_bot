import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_cancel, handle_text_offer_confirm
from models.offer import OfferStatus


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 78
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        return None

    async def get(self, model, key):
        if self.added and model.__name__ == "Offer":
            return self.added[0]
        return None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateTextOfferFailureCancelTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_fake_session_helpers_cover_existing_ids_and_empty_rollback(self):
        session = FakeSession()
        already_identified = SimpleNamespace(id=702)
        session.add(already_identified)
        self.assertEqual(already_identified.id, 702)
        self.assertIsNone(await session.commit())
        self.assertIsNone(await session.refresh(already_identified))
        self.assertIsNone(await session.get(type("Other", (), {}), 4))

        context = FakeSessionContext(session)
        self.assertIs(await context.__aenter__(), session)
        self.assertFalse(await context.__aexit__(None, None, None))

        create_session = FakeSession()
        created_offer = SimpleNamespace(id=None)
        create_session.add(created_offer)

        async def rollback_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            return None

        self.assertIs(await rollback_get(type("Offer", (), {}), 1), created_offer)
        self.assertIsNone(await rollback_get(type("Other", (), {}), 1))

    async def test_handle_text_offer_confirm_handles_channel_unset_and_runtime_error_and_cancel(self):
        data = {
            "quantity": 12,
            "trade_type": "buy",
            "commodity_id": 7,
            "commodity_name": "سکه",
            "price": 123456,
            "is_wholesale": True,
            "lot_sizes": None,
            "notes": None,
        }

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), from_user=SimpleNamespace(id=555))
        state = SimpleNamespace(get_data=AsyncMock(return_value=data), clear=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[FakeSessionContext(FakeSession([0])), FakeSessionContext(FakeSession())],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=None, bot_username="botname")
        ):
            await handle_text_offer_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=SimpleNamespace())
        self.assertIn("کانال تنظیم نشده است", callback.message.edit_text.await_args.args[0])

        create_session = FakeSession()
        publish_session = FakeSession()
        rollback_session = FakeSession()

        async def rollback_get(model, key):
            if create_session.added:
                return create_session.added[0]
            return None

        publish_session.get = rollback_get
        rollback_session.get = rollback_get
        self.assertIsNone(await rollback_session.get(type("Other", (), {}), 2))
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), from_user=SimpleNamespace(id=555))
        state = SimpleNamespace(get_data=AsyncMock(return_value=data), clear=AsyncMock())

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            message_id = await send_offer_to_channel(offer, publish_user)
            offer.channel_message_id = message_id
            return SimpleNamespace(message_id=message_id, error_code=None)

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(publish_session),
                FakeSessionContext(rollback_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_text_offer_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom"))))
        self.assertEqual(create_session.added[0].status, OfferStatus.EXPIRED)
        self.assertIn("خطا در ارسال به کانال", callback.message.edit_text.await_args.args[0])

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await handle_text_offer_cancel(callback, state, user=SimpleNamespace(id=1))
        self.assertIn("لفظ لغو شد", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
