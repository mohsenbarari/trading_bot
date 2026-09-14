"""Characterization tests for trade_create handle_cancel_all_offers_bot entrypoint.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-CANCEL-ALL-OFFERS-BOT-L2380
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.handlers.trade_create import (
    AsyncSessionLocal,
    handle_cancel_all_offers_bot,
)
from core.services.offer_cancel_all_service import (
    OfferCancelAllItemResult,
    OfferCancelAllItemStatus,
    OfferCancelAllResult,
    format_offer_cancel_all_bot_message,
)
from core.services.offer_expiry_service import (
    OfferExpiryReason,
    OfferExpirySourceSurface,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class TestTradeCreateCancelAllOffersBotCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guard(self) -> None:
        message = SimpleNamespace(text="نشد")
        state = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
            new_callable=mock.AsyncMock,
        ) as mock_cancel, mock.patch(
            "bot.handlers.trade_create.apply_offer_channel_state",
            new_callable=mock.AsyncMock,
        ) as mock_apply_channel, mock.patch(
            "api.routers.realtime.publish_event",
            new_callable=mock.AsyncMock,
        ) as mock_publish_event, mock.patch(
            "core.cache.set_active_offer_count",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming:
            result = await handle_cancel_all_offers_bot(message, state=state, user=None)

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [])
        mock_cancel.assert_not_called()
        mock_apply_channel.assert_not_called()
        mock_publish_event.assert_not_called()
        mock_set_cache.assert_not_called()
        mock_answer_incoming.assert_not_called()

    async def test_authenticated_empty_result(self) -> None:
        message = SimpleNamespace(text="نشد")
        state = mock.AsyncMock()
        principal = SimpleNamespace(id=37)
        empty_result = OfferCancelAllResult(items=(), remaining_active_count=0)

        with mock.patch(
            "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
            new_callable=mock.AsyncMock,
            return_value=empty_result,
        ) as mock_cancel, mock.patch(
            "bot.handlers.trade_create.apply_offer_channel_state",
            new_callable=mock.AsyncMock,
        ) as mock_apply_channel, mock.patch(
            "api.routers.realtime.publish_event",
            new_callable=mock.AsyncMock,
        ) as mock_publish_event, mock.patch(
            "core.cache.set_active_offer_count",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_incoming:
            result = await handle_cancel_all_offers_bot(message, state=state, user=principal)

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [])

        mock_cancel.assert_awaited_once_with(
            owner_user_id=37,
            actor_user_id=37,
            source_surface=OfferExpirySourceSurface.TELEGRAM_BOT,
            expire_reason=OfferExpiryReason.BOT_CANCEL_ALL,
            session_factory=AsyncSessionLocal,
        )
        self.assertIs(mock_cancel.await_args.kwargs["session_factory"], AsyncSessionLocal)

        mock_apply_channel.assert_not_called()
        mock_publish_event.assert_not_called()
        mock_set_cache.assert_not_called()

        mock_answer_incoming.assert_awaited_once_with(
            message,
            principal,
            "شما هیچ لفظ فعالی ندارید.",
            source_key="offer-cancel-all-result",
            action=TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
        )

    async def test_authenticated_two_local_offer_success_ordering(self) -> None:
        message = SimpleNamespace(text="نشد")
        state = mock.AsyncMock()
        principal = SimpleNamespace(id=37)

        offer_1 = SimpleNamespace(id=101, offer_public_id="ofr_stage10_00000000000000000101")
        offer_2 = SimpleNamespace(id=102)

        item_1 = OfferCancelAllItemResult(
            offer_public_id="ofr_stage10_00000000000000000101",
            home_server="foreign",
            status=OfferCancelAllItemStatus.CANCELLED,
            local_offer=offer_1,
        )
        item_2 = OfferCancelAllItemResult(
            offer_public_id="ofr_stage10_00000000000000000102",
            home_server="foreign",
            status=OfferCancelAllItemStatus.CANCELLED,
            local_offer=offer_2,
        )
        cancel_result = OfferCancelAllResult(
            items=(item_1, item_2),
            remaining_active_count=0,
        )

        observed_order: list[str] = []

        async def fake_cancel(**kwargs):
            observed_order.append("cancellation")
            return cancel_result

        async def fake_apply_channel(offer, **kwargs):
            observed_order.append(f"apply_offer_channel_state:{offer.id}")
            return True

        async def fake_publish_event(event_type, payload, **kwargs):
            observed_order.append(f"publish_event:{payload['id']}")
            return "event-1"

        async def fake_set_active_count(user_id, count):
            observed_order.append(f"set_active_offer_count:{user_id}:{count}")
            return True

        async def fake_answer_incoming(msg, usr, text, **kwargs):
            observed_order.append("answer_incoming_message_via_runtime")
            return None

        with mock.patch(
            "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
            new_callable=mock.AsyncMock,
            side_effect=fake_cancel,
        ) as mock_cancel, mock.patch(
            "bot.handlers.trade_create.apply_offer_channel_state",
            new_callable=mock.AsyncMock,
            side_effect=fake_apply_channel,
        ) as mock_apply_channel, mock.patch(
            "api.routers.realtime.publish_event",
            new_callable=mock.AsyncMock,
            side_effect=fake_publish_event,
        ) as mock_publish_event, mock.patch(
            "core.cache.set_active_offer_count",
            new_callable=mock.AsyncMock,
            side_effect=fake_set_active_count,
        ) as mock_set_cache, mock.patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_incoming,
        ) as mock_answer_incoming:
            result = await handle_cancel_all_offers_bot(message, state=state, user=principal)

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [])

        self.assertEqual(
            observed_order,
            [
                "cancellation",
                "apply_offer_channel_state:101",
                "publish_event:101",
                "apply_offer_channel_state:102",
                "publish_event:102",
                "set_active_offer_count:37:0",
                "answer_incoming_message_via_runtime",
            ],
        )

        mock_cancel.assert_awaited_once_with(
            owner_user_id=37,
            actor_user_id=37,
            source_surface=OfferExpirySourceSurface.TELEGRAM_BOT,
            expire_reason=OfferExpiryReason.BOT_CANCEL_ALL,
            session_factory=AsyncSessionLocal,
        )
        self.assertIs(mock_cancel.await_args.kwargs["session_factory"], AsyncSessionLocal)

        self.assertEqual(
            mock_apply_channel.await_args_list,
            [
                mock.call(offer_1, reason="bot_cancel_all", timeout=5),
                mock.call(offer_2, reason="bot_cancel_all", timeout=5),
            ],
        )

        self.assertEqual(
            mock_publish_event.await_args_list,
            [
                mock.call(
                    "offer:expired",
                    {"id": 101, "offer_public_id": "ofr_stage10_00000000000000000101"},
                ),
                mock.call(
                    "offer:expired",
                    {"id": 102},
                ),
            ],
        )

        payload_1 = mock_publish_event.await_args_list[0].args[1]
        payload_2 = mock_publish_event.await_args_list[1].args[1]
        self.assertEqual(payload_1, {"id": 101, "offer_public_id": "ofr_stage10_00000000000000000101"})
        self.assertIn("offer_public_id", payload_1)
        self.assertEqual(payload_2, {"id": 102})
        self.assertNotIn("offer_public_id", payload_2)

        mock_set_cache.assert_awaited_once_with(37, 0)

        expected_text = format_offer_cancel_all_bot_message(cancel_result)
        mock_answer_incoming.assert_awaited_once_with(
            message,
            principal,
            expected_text,
            source_key="offer-cancel-all-result",
            action=TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
        )


if __name__ == "__main__":
    unittest.main()
