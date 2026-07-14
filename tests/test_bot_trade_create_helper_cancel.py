import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    _bot_market_is_open,
    _handoff_stale_wizard_state_to_text_offer,
    handle_cancel_all_offers_bot,
)
from core.services.offer_cancel_all_service import (
    OfferCancelAllItemResult,
    OfferCancelAllItemStatus,
    OfferCancelAllResult,
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

        with patch(
            "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
            new=AsyncMock(return_value=OfferCancelAllResult(items=(), remaining_active_count=0)),
        ) as cancel_service:
            await handle_cancel_all_offers_bot(message, state=SimpleNamespace(), user=SimpleNamespace(id=15))

        message.answer.assert_awaited_once_with("شما هیچ لفظ فعالی ندارید.")
        cancel_service.assert_awaited_once()

    async def test_handle_cancel_all_offers_expires_offers_and_syncs_side_effects(self):
        offers = [
            SimpleNamespace(id=10, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=222),
            SimpleNamespace(id=11, status=OfferStatus.ACTIVE, home_server="foreign", channel_message_id=None),
        ]
        message = SimpleNamespace(answer=AsyncMock())
        result = OfferCancelAllResult(
            items=tuple(
                OfferCancelAllItemResult(
                    offer_public_id=f"ofr_stage10_{offer.id:020d}",
                    home_server="foreign",
                    status=OfferCancelAllItemStatus.CANCELLED,
                    local_offer=offer,
                )
                for offer in offers
            ),
            remaining_active_count=0,
        )

        with patch(
            "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
            new=AsyncMock(return_value=result),
        ) as cancel_service, patch(
            "bot.handlers.trade_create.apply_offer_channel_state",
            new=AsyncMock(),
        ) as apply_offer_channel_state, patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ) as publish_event_mock, patch(
            "core.cache.set_active_offer_count",
            new=AsyncMock(),
        ) as set_active_offer_count_mock:
            await handle_cancel_all_offers_bot(message, state=SimpleNamespace(), user=SimpleNamespace(id=15))

        cancel_service.assert_awaited_once()
        apply_offer_channel_state.assert_any_await(offers[0], reason="bot_cancel_all", timeout=5)
        apply_offer_channel_state.assert_any_await(offers[1], reason="bot_cancel_all", timeout=5)
        self.assertEqual(apply_offer_channel_state.await_count, 2)
        self.assertEqual(publish_event_mock.await_count, 2)
        publish_event_mock.assert_any_await("offer:expired", {"id": 10})
        publish_event_mock.assert_any_await("offer:expired", {"id": 11})
        set_active_offer_count_mock.assert_awaited_once_with(15, 0)
        message.answer.assert_awaited_once()
        summary_text = message.answer.await_args.args[0]
        self.assertIn("تمام لفظ", summary_text)
        self.assertIn("(2 لفظ)", summary_text)

    async def test_handle_cancel_all_offers_reports_partial_failure_without_all_success_claim(self):
        message = SimpleNamespace(answer=AsyncMock())
        result = OfferCancelAllResult(
            items=(
                OfferCancelAllItemResult(
                    offer_public_id="ofr_stage10_00000000000000000001",
                    home_server="foreign",
                    status=OfferCancelAllItemStatus.CANCELLED,
                ),
                OfferCancelAllItemResult(
                    offer_public_id="ofr_stage10_00000000000000000002",
                    home_server="iran",
                    status=OfferCancelAllItemStatus.FAILED,
                    error_code="remote_503",
                    retryable=True,
                ),
            ),
            remaining_active_count=1,
        )
        with patch(
            "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
            new=AsyncMock(return_value=result),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()):
            await handle_cancel_all_offers_bot(message, state=SimpleNamespace(), user=SimpleNamespace(id=15))

        text = message.answer.await_args.args[0]
        self.assertIn("کامل نشد", text)
        self.assertIn("لغونشده: 1", text)
        self.assertNotIn("تمام لفظ‌های فعال شما", text)


if __name__ == "__main__":
    unittest.main()
