import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import (
    _queue_authoritative_channel_offer_refresh,
    update_offer_channel_markup,
)
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from models.offer import OfferStatus


class BotTradeExecuteUpdateMarkupTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_action_refresh_uses_canonical_state_and_queue(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        offer = SimpleNamespace(offer_public_id="ofr_1")
        state = SimpleNamespace(telegram_message_id=777)
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1)
        with patch(
            "bot.handlers.trade_execute.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_execute.configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch(
            "bot.handlers.trade_execute.load_telegram_publication_state_for_update",
            new=AsyncMock(return_value=state),
        ) as load_state, patch(
            "bot.handlers.trade_execute.enqueue_current_offer_delivery",
            new=AsyncMock(return_value=SimpleNamespace()),
        ) as enqueue, patch(
            "bot.handlers.trade_execute.settings",
            SimpleNamespace(channel_id=-100),
        ):
            queued = await _queue_authoritative_channel_offer_refresh(
                session,
                offer,
                invalid_active_action=True,
            )

        self.assertTrue(queued)
        load_state.assert_awaited_once_with(session, offer)
        self.assertEqual(
            enqueue.await_args.kwargs["action"],
            TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
        )
        self.assertIs(enqueue.await_args.kwargs["state"], state)
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_terminal_stale_action_lets_authoritative_mapper_choose_edit(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        offer = SimpleNamespace(offer_public_id="ofr_2")
        state = SimpleNamespace(telegram_message_id=778)
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1)
        with patch(
            "bot.handlers.trade_execute.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_execute.configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch(
            "bot.handlers.trade_execute.load_telegram_publication_state_for_update",
            new=AsyncMock(return_value=state),
        ), patch(
            "bot.handlers.trade_execute.enqueue_current_offer_delivery",
            new=AsyncMock(return_value=SimpleNamespace()),
        ) as enqueue, patch(
            "bot.handlers.trade_execute.settings",
            SimpleNamespace(channel_id=-100),
        ):
            queued = await _queue_authoritative_channel_offer_refresh(
                session,
                offer,
                invalid_active_action=False,
            )

        self.assertTrue(queued)
        self.assertIsNone(enqueue.await_args.kwargs["action"])

    async def test_legacy_runtime_never_creates_invalid_action_job(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.LEGACY)
        with patch(
            "bot.handlers.trade_execute.current_server",
            return_value="foreign",
        ), patch(
            "bot.handlers.trade_execute.configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch(
            "bot.handlers.trade_execute.load_telegram_publication_state_for_update",
            new=AsyncMock(),
        ) as load_state:
            queued = await _queue_authoritative_channel_offer_refresh(
                session,
                SimpleNamespace(offer_public_id="ofr_3"),
                invalid_active_action=True,
            )

        self.assertFalse(queued)
        load_state.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_queue_owner_never_edits_channel_directly(self):
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        offer = SimpleNamespace(
            channel_message_id=11,
            remaining_quantity=5,
            status=OfferStatus.ACTIVE,
        )
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1)
        with patch(
            "bot.handlers.trade_execute.configured_telegram_delivery_runtime",
            return_value=runtime,
        ), patch(
            "bot.handlers.trade_execute.apply_offer_channel_state",
            new=AsyncMock(),
        ) as apply_state:
            await update_offer_channel_markup(bot, offer)

        apply_state.assert_not_awaited()
        bot.edit_message_reply_markup.assert_not_awaited()

    async def test_update_offer_channel_markup_handles_missing_completed_and_active_offers(self):
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())

        await update_offer_channel_markup(bot, SimpleNamespace(channel_message_id=None))
        bot.edit_message_reply_markup.assert_not_awaited()

        offer = SimpleNamespace(channel_message_id=10, remaining_quantity=0, status=OfferStatus.ACTIVE)
        with patch("bot.handlers.trade_execute.apply_offer_channel_state", new=AsyncMock()) as apply_offer_channel_state:
            await update_offer_channel_markup(bot, offer)
        apply_offer_channel_state.assert_awaited_once_with(offer, reason="bot_channel_trade")
        bot.edit_message_reply_markup.assert_not_awaited()

        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        offer = SimpleNamespace(
            channel_message_id=11,
            remaining_quantity=5,
            status=OfferStatus.ACTIVE,
            id=7,
            quantity=10,
            is_wholesale=False,
            lot_sizes=[3, 2],
        )
        with patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)), patch(
            "bot.handlers.trade_execute.build_offer_trade_buttons", return_value="KB"
        ) as buttons_mock:
            await update_offer_channel_markup(bot, offer)

        buttons_mock.assert_called_once_with(7, 10, 5, False, [3, 2], offer_public_id=None)
        bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=-100, message_id=11, reply_markup="KB")


if __name__ == "__main__":
    unittest.main()
