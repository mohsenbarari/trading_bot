import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from core.telegram_publisher_b2b_runtime import (
    accept_primary_b2b_acknowledgement,
    accept_publisher_b2b_dispatch,
)


class TelegramPublisherB2BRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_publisher_adapter_passes_only_edge_facts_to_durable_service(self):
        expected = object()
        with patch(
            "core.telegram_publisher_b2b_runtime.accept_telegram_publisher_dispatch",
            new=AsyncMock(return_value=expected),
        ) as accept:
            result = await accept_publisher_b2b_dispatch(
                object(), current_server="foreign", publisher_bot_identity="publisher_1",
                expected_primary_bot_id=11, sender_bot_id=11, text="tbq1|dispatch|x|1|2026-08-11T00:00:00Z",
                received_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
            )
        self.assertIs(result, expected)
        self.assertEqual(accept.await_args.kwargs["publisher_bot_identity"], "publisher_1")

    async def test_primary_adapter_does_not_create_a_command_for_ack(self):
        with patch(
            "core.telegram_publisher_b2b_runtime.accept_telegram_publisher_acknowledgement",
            new=AsyncMock(return_value=True),
        ) as accept:
            self.assertTrue(await accept_primary_b2b_acknowledgement(
                object(), current_server="foreign", sender_bot_id=22,
                publisher_bot_ids={"publisher_1": 22}, text="tbq1|ack|x|1|2026-08-11T00:00:00Z|2026-08-11T00:00:01Z",
                received_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
            ))
        self.assertEqual(accept.await_count, 1)
