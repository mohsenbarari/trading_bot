import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot.handlers.trade_create as trade_create
from bot.handlers.trade_create import (
    _bot_market_is_open,
    _handoff_stale_wizard_state_to_text_offer,
    handle_cancel_all_offers_bot,
)
from models.offer import OfferStatus


class FakeExecuteResult:
    def __init__(self, offers):
        self._offers = offers

    def scalars(self):
        return self

    def all(self):
        return self._offers


class FakeSession:
    def __init__(self, offers):
        self._offers = offers
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        return FakeExecuteResult(self._offers)


class FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeHttpClient:
    def __init__(self):
        self.post = AsyncMock()


class FakeHttpClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateHelperAndCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_market_is_open_reads_runtime_schedule_flag(self):
        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(SimpleNamespace()),
        ), patch(
            "core.services.market_transition_service.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ) as evaluate_mock:
            self.assertTrue(await _bot_market_is_open())

        evaluate_mock.assert_awaited_once()

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(SimpleNamespace()),
        ), patch(
            "core.services.market_transition_service.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace()),
        ):
            self.assertFalse(await _bot_market_is_open())

    async def test_handoff_stale_wizard_state_branches(self):
        state = SimpleNamespace(clear=AsyncMock())
        user = SimpleNamespace(id=7)

        message = SimpleNamespace(text="خ ربع 30تا 75800")
        with patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=True),
        ), patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handle_text_offer_mock:
            self.assertTrue(await _handoff_stale_wizard_state_to_text_offer(message, state, user))

        state.clear.assert_not_awaited()
        handle_text_offer_mock.assert_not_awaited()

        message = SimpleNamespace(text="سلام")
        with patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=False),
        ), patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handle_text_offer_mock:
            self.assertFalse(await _handoff_stale_wizard_state_to_text_offer(message, state, user))

        state.clear.assert_not_awaited()
        handle_text_offer_mock.assert_not_awaited()

        message = SimpleNamespace(text="ف ربع 20تا 70000")
        with patch(
            "bot.handlers.panel.handoff_navigation_button",
            new=AsyncMock(return_value=False),
        ), patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handle_text_offer_mock:
            self.assertTrue(await _handoff_stale_wizard_state_to_text_offer(message, state, user))

        state.clear.assert_awaited_once()
        handle_text_offer_mock.assert_awaited_once_with(message, state, user, None)

        message = SimpleNamespace(text="خ 10تا 50000")
        self.assertFalse(await _handoff_stale_wizard_state_to_text_offer(message, state, None))

    async def test_handle_cancel_all_offers_handles_missing_user_and_empty_offers(self):
        message = SimpleNamespace(answer=AsyncMock())
        await handle_cancel_all_offers_bot(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        empty_session = FakeSession([])
        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(empty_session),
        ):
            await handle_cancel_all_offers_bot(message, state=SimpleNamespace(), user=SimpleNamespace(id=15))

        message.answer.assert_awaited_once_with("شما هیچ لفظ فعالی ندارید.")
        empty_session.commit.assert_not_awaited()

    async def test_handle_cancel_all_offers_expires_offers_and_syncs_side_effects(self):
        offers = [
            SimpleNamespace(id=10, status=OfferStatus.ACTIVE, channel_message_id=222),
            SimpleNamespace(id=11, status=OfferStatus.ACTIVE, channel_message_id=None),
        ]
        session = FakeSession(offers)
        message = SimpleNamespace(answer=AsyncMock())
        http_client = FakeHttpClient()

        with patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(session),
        ), patch(
            "os.getenv",
            side_effect=lambda key, default=None: "bot-token" if key == "BOT_TOKEN" else default,
        ), patch.object(
            trade_create.settings,
            "channel_id",
            1000,
        ), patch(
            "httpx.AsyncClient",
            return_value=FakeHttpClientContext(http_client),
        ), patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_event_mock, patch(
            "core.cache.decr_active_offer_count",
            new=AsyncMock(),
        ) as decr_active_offer_count_mock:
            await handle_cancel_all_offers_bot(message, state=SimpleNamespace(), user=SimpleNamespace(id=15))

        self.assertEqual(offers[0].status, OfferStatus.EXPIRED)
        self.assertEqual(offers[1].status, OfferStatus.EXPIRED)
        http_client.post.assert_awaited_once_with(
            "https://api.telegram.org/botbot-token/editMessageReplyMarkup",
            json={"chat_id": 1000, "message_id": 222},
            timeout=5,
        )
        self.assertEqual(publish_event_mock.await_count, 2)
        publish_event_mock.assert_any_await("offer:expired", {"id": 10})
        publish_event_mock.assert_any_await("offer:expired", {"id": 11})
        decr_active_offer_count_mock.assert_any_await(15)
        self.assertEqual(decr_active_offer_count_mock.await_count, 2)
        session.commit.assert_awaited_once()
        message.answer.assert_awaited_once()
        summary_text = message.answer.await_args.args[0]
        self.assertIn("تمام لفظ", summary_text)
        self.assertIn("(2 لفظ)", summary_text)


if __name__ == "__main__":
    unittest.main()
