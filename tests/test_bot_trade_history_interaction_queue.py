import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import trade_history
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class _State:
    def __init__(self):
        self.update_data = AsyncMock()

    async def get_data(self):
        return {"history_months": 3}


def _callback():
    return SimpleNamespace(
        id="callback-id",
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=7007),
            message_id=91,
            answer=AsyncMock(),
        ),
    )


class BotTradeHistoryInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_text_feedback_uses_trade_noncritical_lane(self):
        user = SimpleNamespace(id=7)
        target = SimpleNamespace(account_name="target")
        callback_data = SimpleNamespace(target_user_id=9)
        state = _State()
        message = SimpleNamespace(answer=AsyncMock())

        with (
            patch(
                "bot.handlers.trade_history.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as incoming,
            patch(
                "bot.handlers.trade_history.answer_callback_message_via_runtime",
                new=AsyncMock(),
            ) as callback_reply,
            patch(
                "bot.handlers.trade_history._ensure_history_profile_access",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.trade_history.format_trade_history",
                return_value="HISTORY",
            ),
        ):
            with patch(
                "bot.handlers.trade_history.get_trade_history",
                new=AsyncMock(return_value=(target, [])),
            ):
                await trade_history.show_my_trade_history(message, state, user)
                await trade_history.export_excel(
                    _callback(), callback_data, state, user, SimpleNamespace()
                )
                await trade_history.export_pdf(
                    _callback(), callback_data, state, user, SimpleNamespace()
                )
                await trade_history.export_profile_trade_pdf(
                    _callback(), callback_data, state, user, SimpleNamespace()
                )

            with patch(
                "bot.handlers.trade_history.get_trade_history",
                new=AsyncMock(return_value=(target, [SimpleNamespace(id=1)])),
            ), patch(
                "bot.handlers.trade_history.generate_excel",
                new=AsyncMock(side_effect=RuntimeError("excel")),
            ), patch(
                "bot.handlers.trade_history.generate_pdf",
                new=AsyncMock(side_effect=RuntimeError("pdf")),
            ):
                await trade_history.export_excel(
                    _callback(), callback_data, state, user, SimpleNamespace()
                )
                await trade_history.export_pdf(
                    _callback(), callback_data, state, user, SimpleNamespace()
                )
                await trade_history.export_profile_trade_pdf(
                    _callback(), callback_data, state, user, SimpleNamespace()
                )

        self.assertEqual(incoming.await_args.kwargs["source_key"], "history-main")
        self.assertEqual(
            incoming.await_args.kwargs["action"],
            TelegramDeliveryAction.TRADE_NONCRITICAL,
        )
        self.assertEqual(
            [call.kwargs["source_key"] for call in callback_reply.await_args_list],
            [
                "history-excel-empty",
                "history-pdf-empty",
                "history-profile-pdf-empty",
                "history-excel-error",
                "history-pdf-error",
                "history-profile-pdf-error",
            ],
        )
        self.assertTrue(
            all(
                call.kwargs["action"]
                == TelegramDeliveryAction.TRADE_NONCRITICAL
                for call in callback_reply.await_args_list
            )
        )
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
