import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from aiogram.types import Update

from bot.middlewares.callback_receipt import (
    CALLBACK_RECEIVED_AT_DATA_KEY,
    CallbackReceiptMiddleware,
)
from core.telegram_callback_receipt_context import (
    current_telegram_callback_received_at,
)


def callback_update() -> Update:
    return Update.model_validate(
        {
            "update_id": 77,
            "callback_query": {
                "id": "callback-77",
                "from": {
                    "id": 7007,
                    "is_bot": False,
                    "first_name": "Test",
                },
                "chat_instance": "chat-instance",
                "data": "page:2",
            },
        }
    )


class BotCallbackReceiptMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_binds_edge_timestamp_for_handler_and_resets_afterward(self):
        middleware = CallbackReceiptMiddleware()
        received_at = datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc)
        captured = {}

        async def handler(event, data):
            captured["data"] = data[CALLBACK_RECEIVED_AT_DATA_KEY]
            captured["context"] = current_telegram_callback_received_at()
            return "ok"

        with patch(
            "bot.middlewares.callback_receipt.utc_now",
            return_value=received_at,
        ):
            result = await middleware(handler, callback_update(), {})

        self.assertEqual(result, "ok")
        self.assertEqual(captured, {"data": received_at, "context": received_at})
        self.assertIsNone(current_telegram_callback_received_at())


if __name__ == "__main__":
    unittest.main()
