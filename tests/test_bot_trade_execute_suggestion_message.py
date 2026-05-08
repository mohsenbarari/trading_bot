import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_execute import send_or_update_trade_suggestion_message


def make_callback(chat_id=200, message_id=50, edit_side_effect=None):
    return SimpleNamespace(
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=message_id,
            edit_text=AsyncMock(side_effect=edit_side_effect),
        )
    )


class BotTradeExecuteSuggestionMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_or_update_trade_suggestion_message_updates_existing_private_message(self):
        callback = make_callback(chat_id=200, message_id=50)
        bot = SimpleNamespace(send_message=AsyncMock())
        payload = {"offer_id": 7, "available_lots": [2, 3], "message": "MSG", "requested_amount": 3}

        with patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)), patch(
            "bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"
        ), patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()) as upsert_mock, patch(
            "bot.handlers.trade_execute.schedule_trade_suggestion_cleanup"
        ) as cleanup_mock:
            await send_or_update_trade_suggestion_message(callback, bot, target_chat_id=200, payload=payload)

        callback.message.edit_text.assert_awaited_once_with("MSG", reply_markup="KB")
        upsert_mock.assert_awaited_once_with(offer_id=7, chat_id=200, message_id=50, requested_amount=3)
        cleanup_mock.assert_called_once()
        bot.send_message.assert_not_awaited()

    async def test_send_or_update_trade_suggestion_message_falls_back_to_send_message(self):
        callback = make_callback(chat_id=200, message_id=50, edit_side_effect=RuntimeError("boom"))
        sent_message = SimpleNamespace(message_id=88)
        bot = SimpleNamespace(send_message=AsyncMock(return_value=sent_message))
        payload = {"offer_id": 7, "available_lots": [2, 3], "message": "MSG", "requested_amount": 3}

        with patch("bot.handlers.trade_execute.settings", SimpleNamespace(channel_id=-100)), patch(
            "bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"
        ), patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()) as upsert_mock, patch(
            "bot.handlers.trade_execute.schedule_trade_suggestion_cleanup"
        ) as cleanup_mock:
            await send_or_update_trade_suggestion_message(callback, bot, target_chat_id=200, payload=payload)

        bot.send_message.assert_awaited_once_with(chat_id=200, text="MSG", reply_markup="KB")
        upsert_mock.assert_awaited_once_with(offer_id=7, chat_id=200, message_id=88, requested_amount=3)
        cleanup_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()