import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.telegram_callback_answer import (
    TelegramCallbackReceiptMissingError,
    answer_callback_query_via_runtime,
)
from core.telegram_callback_receipt_context import (
    bind_telegram_callback_received_at,
    reset_telegram_callback_received_at,
)


class BotTelegramCallbackAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_mode_preserves_aiogram_call_shape(self):
        callback = SimpleNamespace(answer=AsyncMock(return_value=True))
        with patch(
            "bot.telegram_callback_answer.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="legacy"),
        ):
            result = await answer_callback_query_via_runtime(callback)
            await answer_callback_query_via_runtime(
                callback,
                "هشدار",
                show_alert=True,
            )

        self.assertTrue(result)
        self.assertEqual(callback.answer.await_args_list[0].args, ())
        self.assertEqual(callback.answer.await_args_list[0].kwargs, {})
        self.assertEqual(callback.answer.await_args_list[1].args, ("هشدار",))
        self.assertEqual(
            callback.answer.await_args_list[1].kwargs,
            {"show_alert": True},
        )

    async def test_queue_mode_requires_edge_receipt_context(self):
        callback = SimpleNamespace(id="callback-1", answer=AsyncMock())
        with patch(
            "bot.telegram_callback_answer.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="queue-v1"),
        ):
            with self.assertRaises(TelegramCallbackReceiptMissingError):
                await answer_callback_query_via_runtime(callback)
        callback.answer.assert_not_awaited()

    async def test_queue_mode_enqueues_m0_with_original_receipt_time(self):
        callback = SimpleNamespace(id="callback-2", answer=AsyncMock())
        session = SimpleNamespace(commit=AsyncMock())
        received_at = datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc)
        token = bind_telegram_callback_received_at(received_at)
        try:
            with patch(
                "bot.telegram_callback_answer.configured_telegram_delivery_runtime",
                return_value=SimpleNamespace(mode="queue-v1"),
            ), patch(
                "bot.telegram_callback_answer.current_server",
                return_value="foreign",
            ), patch(
                "bot.telegram_callback_answer.enqueue_telegram_callback_answer",
                new=AsyncMock(return_value=SimpleNamespace(created=True)),
            ) as enqueue:
                result = await answer_callback_query_via_runtime(
                    callback,
                    "انجام شد",
                    show_alert=False,
                    session=session,
                )
        finally:
            reset_telegram_callback_received_at(token)

        self.assertTrue(result.created)
        enqueue.assert_awaited_once_with(
            session,
            current_server="foreign",
            callback_query_id="callback-2",
            received_at=received_at,
            text="انجام شد",
            show_alert=False,
        )
        session.commit.assert_awaited_once_with()
        callback.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
